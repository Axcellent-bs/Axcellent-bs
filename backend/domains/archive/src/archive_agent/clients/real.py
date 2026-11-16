"""실제 연동 클라이언트: memory는 HTTP로 부른다.

기본은 목이고, `HR_MEMORY_MODE=http`일 때만 쓴다.
인터페이스는 `clients/memory.py`의 Protocol과 같다.

가정(상대 쪽이 아직 없어서 정한 것 — 확인되면 이 파일만 고친다):
- memory: REST `HR_MEMORY_URL`(기본 `AX_MEMORY_URL`, 없으면 http://localhost:8002) 아래
  `POST /api/memory/cards`(저장), `GET /api/memory/cards/{id}`, `GET /api/memory/cards`(목록, 쿼리
  area·states), `GET /api/memory/cards/search`(검색, 쿼리 q·area·states·kinds·limit),
  `PATCH /api/memory/cards/{id}/status`, `PATCH /api/memory/cards/{id}/candidate`.
  응답은 `{"data": ...}` 봉투다(공통 구성안 API 형식). 실제 이름·경로는 재현의 memory가 정한다.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from pydantic import ValidationError

from archive_agent.clients.errors import UpstreamError
from archive_agent.clients.memory import CardHit
from ax_contracts.schemas import Card

CALL_TIMEOUT_SECONDS = 20


class HttpMemoryClient:
    """memory 서비스에 카드를 쓰고 읽는다. 쓰기는 호출하는 쪽이 `execute()` 뒤에만 부른다."""

    def __init__(
        self, base_url: str | None = None, http_client: httpx.AsyncClient | None = None
    ) -> None:
        url = base_url or os.getenv("HR_MEMORY_URL") or os.getenv("AX_MEMORY_URL")
        self._base = (url or "http://localhost:8002").rstrip("/")
        self._http = http_client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict[str, Any] | None = None,
        allow_missing: bool = False,
    ) -> Any:
        """`{"data": ...}` 봉투의 data를 돌려준다. 404는 `allow_missing`일 때만 None이다."""
        client = self._http or httpx.AsyncClient()
        try:
            response = await client.request(
                method,
                f"{self._base}{path}",
                json=json,
                params=params,
                timeout=CALL_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as exc:
            raise UpstreamError("memory", f"{method} {path}: {type(exc).__name__}") from exc
        finally:
            if self._http is None:
                await client.aclose()
        if response.status_code == 404 and allow_missing:
            return None
        if response.status_code >= 400:
            raise UpstreamError("memory", f"{method} {path}: http {response.status_code}")
        try:
            envelope = response.json()
        except ValueError as exc:
            raise UpstreamError("memory", f"{method} {path}: 응답이 JSON이 아니다") from exc
        if not isinstance(envelope, dict) or "data" not in envelope:
            raise UpstreamError("memory", f"{method} {path}: 응답에 data가 없다")
        return envelope["data"]

    @staticmethod
    def _card(raw: Any) -> Card:
        try:
            return Card.model_validate(raw)
        except ValidationError as exc:
            raise UpstreamError("memory", "카드 형식 오류") from exc

    async def write_card(self, card: Card) -> str:
        data = await self._request("POST", "/api/memory/cards", json=card.model_dump(mode="json"))
        found = data.get("card_id") if isinstance(data, dict) else None
        if not found:
            raise UpstreamError("memory", "카드 저장 응답에 card_id가 없다")
        return str(found)

    async def get_card(self, card_id: str) -> Card | None:
        data = await self._request("GET", f"/api/memory/cards/{card_id}", allow_missing=True)
        return None if data is None else self._card(data)

    async def list_cards(
        self, *, area: str | None = None, states: list[str] | None = None
    ) -> list[Card]:
        params: dict[str, Any] = {}
        if area:
            params["area"] = area
        if states:
            params["states"] = ",".join(states)
        data = await self._request("GET", "/api/memory/cards", params=params)
        if not isinstance(data, list):
            raise UpstreamError("memory", "카드 목록 형식 오류")
        return [self._card(item) for item in data]

    async def search_cards(
        self,
        query: str,
        *,
        area: str | None = None,
        kinds: list[str] | None = None,
        states: list[str] | None = None,
        limit: int = 5,
    ) -> list[CardHit]:
        params: dict[str, Any] = {"q": query, "limit": limit}
        if area:
            params["area"] = area
        if kinds:
            params["kinds"] = ",".join(kinds)
        if states:
            params["states"] = ",".join(states)
        data = await self._request("GET", "/api/memory/cards/search", params=params)
        if not isinstance(data, list):
            raise UpstreamError("memory", "검색 결과 형식 오류")
        hits = []
        for item in data:
            try:
                hits.append(CardHit(self._card(item["card"]), float(item["score"])))
            except (KeyError, TypeError, ValueError) as exc:
                raise UpstreamError("memory", "검색 결과 형식 오류") from exc
        return hits

    async def set_status(self, card_id: str, status: str, superseded_by: str | None = None) -> None:
        await self._request(
            "PATCH",
            f"/api/memory/cards/{card_id}/status",
            json={"status": status, "superseded_by": superseded_by},
        )

    async def set_candidate(self, card_id: str, candidate: bool) -> None:
        await self._request(
            "PATCH", f"/api/memory/cards/{card_id}/candidate", json={"candidate": candidate}
        )
