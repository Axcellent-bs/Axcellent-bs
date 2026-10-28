"""실제 연동 부품: Agent Card 수집(HTTP)과 부서 호출(`call_agent`).

이벤트 구독(Redis Streams)은 agent-template `events.py`의 TODO라 아직 연결하지 않는다.
"""

from __future__ import annotations

from typing import Any

import httpx

from ax_agent_template.client import call_agent
from ax_agent_template.config import agent_url
from ax_agent_template.services import SERVICES
from ax_orchestrator.registry import CardFetcher

CARD_PATH = "/.well-known/agent-card.json"
CARD_TIMEOUT = 5.0


def http_card_fetcher(client: httpx.AsyncClient | None = None) -> CardFetcher:
    """`GET <부서>/.well-known/agent-card.json`. 실패하면 None(레지스트리가 처리).

    부서가 서비스 여러 개로 나뉘어 있으면(services.py, 예: support → inquiry·claims·reviews) 서비스 카드를
    모두 받아 skill을 합친 부서 카드 하나로 돌려준다. 호출은 `call_agent`가 skill로 서비스를 고른다.
    """

    async def get(http: httpx.AsyncClient, name: str) -> dict[str, Any] | None:
        try:
            response = await http.get(agent_url(name) + CARD_PATH, timeout=CARD_TIMEOUT)
            if response.status_code != 200:
                return None
            data = response.json()
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    async def fetch(dept: str) -> dict[str, Any] | None:
        http = client or httpx.AsyncClient()
        try:
            targets = [s.id for s in SERVICES if s.department == dept and s.skills]
            if not targets or targets == [dept]:
                return await get(http, dept)
            cards = [c for c in [await get(http, t) for t in targets] if c]
            if not cards:
                return None
            skills = [skill for c in cards for skill in c.get("skills", [])]
            return {**cards[0], "name": dept, "skills": skills}
        finally:
            if client is None:
                await http.aclose()

    return fetch


__all__ = ["call_agent", "http_card_fetcher"]
