"""요청 색인: 요청을 회사 기억(memory) `request` 카드로 저장하고 Dense 검색으로 찾는다.

기획 서버와 개발 서버가 같은 색인을 써서, 개발 AI도 기획 쪽에만 있는(아직 개발로 안 넘어온)
요청을 찾는다. 카드에는 요청 id와 그 요청을 가진 서비스(`owner`)를 남기고, 다른 서비스 요청의
지금 상태는 그 서비스의 `GET /api/planning/requests`로 읽는다 (카드의 상태는 오래될 수 있어서).

- 쓰기: `card_write`(관문 `memory.card_write`), 멱등 키 `request_card:<요청 id>`
- 찾기: `card_search` kind `request`
- 못 쓰거나 못 찾으면 예외를 던진다. 부르는 쪽(related.Relater)이 글자 검색으로 대신한다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_agent_template.config import agent_url
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.record import (
    CardDraft,
    CardSearchInput,
    CardSearchOutput,
    CardWriteInput,
)
from planning_agent.domain.models import Request

TIMEOUT_SECONDS = 10.0
AgentCaller = Callable[[str, AgentRequest], Awaitable[AgentResult]]
PeerReader = Callable[[str], Awaitable[list[Request]]]


class IndexUnavailable(Exception):
    """memory나 다른 서비스를 읽지 못했다."""


@dataclass
class Hit:
    request_id: str
    owner: str  # 그 요청을 가진 서비스 (planning·dev)
    score: float


class MemoryRequestIndex:
    def __init__(
        self,
        owner: str,
        caller: AgentCaller | None = None,
        peer_reader: PeerReader | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> None:
        self.owner = owner or "planning"
        self._call = caller or (lambda target, req: call_agent(target, req))
        self._peer = peer_reader or read_peer_requests
        self.tenant_id = tenant_id

    def _meta(self, ctx: str, key: str | None = None) -> RequestMeta:
        return RequestMeta(
            context_id=ctx,
            tenant_id=self.tenant_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=TIMEOUT_SECONDS),
            idempotency_key=key,
        )

    async def _ask(self, skill: str, ctx: str, inp: dict, key: str | None = None) -> dict:
        req = AgentRequest(meta=self._meta(ctx, key), skill=skill, input=inp)
        try:
            out = await self._call("memory", req)
        except Exception as exc:  # noqa: BLE001 - 꺼짐·연결 실패
            raise IndexUnavailable(f"memory 호출 실패 ({type(exc).__name__})") from exc
        if out.status != TaskStatus.COMPLETED:
            raise IndexUnavailable(f"memory {skill}: {out.status.value} {out.outcome or ''}")
        return out.output or {}

    async def write(self, req: Request, text: str) -> None:
        card = CardDraft(
            kind="request",
            tenant_id=self.tenant_id,
            context_id=req.context_id,
            title=req.title[:200] or req.id,
            body=text,
            tags=[req.id, self.owner],
            source_refs=[req.id],
            author=self.owner,
            confidence="mid",
            extra={"request_id": req.id, "owner": self.owner, "from": req.from_},
        )
        await self._ask(
            "card_write",
            req.context_id,
            CardWriteInput(card=card).model_dump(mode="json"),
            key=f"request_card:{req.id}",
        )

    async def search(self, text: str, top_k: int) -> list[Hit]:
        body = await self._ask(
            "card_search",
            "planning:related",
            CardSearchInput(query=text, kind=["request"], top_k=top_k).model_dump(mode="json"),
        )
        try:
            out = CardSearchOutput.model_validate(body)
        except ValidationError as exc:
            raise IndexUnavailable("card_search 출력이 계약과 다름") from exc
        hits: list[Hit] = []
        for h in out.hits:
            rid = str(h.card.extra.get("request_id") or "")
            if rid and not any(x.request_id == rid for x in hits):
                hits.append(Hit(rid, str(h.card.extra.get("owner") or "planning"), h.score))
        return hits

    async def peer_requests(self, owner: str) -> list[Request]:
        """다른 서비스가 가진 요청의 지금 상태."""
        try:
            return await self._peer(owner)
        except Exception as exc:  # noqa: BLE001
            raise IndexUnavailable(f"{owner} 요청을 읽지 못함 ({type(exc).__name__})") from exc


async def read_peer_requests(owner: str) -> list[Request]:
    async with httpx.AsyncClient() as client:
        res = await client.get(f"{agent_url(owner)}/api/planning/requests", timeout=TIMEOUT_SECONDS)
        res.raise_for_status()
        rows = res.json().get("data") or []
    return [Request.model_validate(r) for r in rows]


def default_index(role: str) -> MemoryRequestIndex | None:
    """목 모드면 None (memory를 부르지 않는다)."""
    if template_config.is_mock("planning"):
        return None
    return MemoryRequestIndex(owner=role or "planning")
