"""실제 연동 부품: Agent Card 수집(HTTP)과 부서 호출(`call_agent`).

이벤트 구독(Redis Streams)은 agent-template `events.py`의 TODO라 아직 연결하지 않는다.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ax_agent_template.client import call_agent
from ax_agent_template.config import agent_url
from ax_agent_template.services import SERVICES
from ax_orchestrator.engine import ReviewChecker
from ax_orchestrator.registry import CardFetcher

CARD_PATH = "/.well-known/agent-card.json"
CARD_TIMEOUT = 5.0


def http_card_fetcher(client: httpx.AsyncClient | None = None) -> CardFetcher:
    """`GET <부서>/.well-known/agent-card.json`. 실패하면 None(레지스트리가 처리).

    부서가 서비스 여러 개로 나뉘어 있으면(services.py, 예: support → inquiry·claims·reviews)
    서비스 카드를 모두 받아 skill을 합친 부서 카드 하나로 돌려준다.
    호출은 `call_agent`가 skill로 서비스를 고른다.
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
            # 같은 skill id가 두 서비스 카드에 있어도 부서 카드에는 하나만 둔다.
            # 겹치면 카드 검증이 "skill id 중복"으로 거부돼 그 부서 skill이 모두 건너뛰어진다.
            # 겹치면 나중 서비스 것을 쓴다(호출 대응표 `_SKILL_HOME`도 나중 서비스를 가리킨다)
            by_id: dict[str, Any] = {}
            no_id: list[Any] = []
            for c in cards:
                for skill in c.get("skills", []):
                    if isinstance(skill, dict) and "id" in skill:
                        by_id[skill["id"]] = skill
                    else:
                        no_id.append(skill)
            skills = [*by_id.values(), *no_id]
            return {**cards[0], "name": dept, "skills": skills}
        finally:
            if client is None:
                await http.aclose()

    return fetch


def http_review_checker() -> ReviewChecker:
    """`GET <safety>/api/safety/reviews/<review_id>`로 보류 건 상태를 읽는다. 못 읽으면 None.

    사람이 승인했는지는 이벤트가 아니라 안전 관문의 기록으로 확인한다(읽기 조회라 토큰이 필요 없다).
    """

    async def check(review_id: str) -> dict[str, Any] | None:
        url = f"{agent_url('safety')}/api/safety/reviews/{quote(review_id, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=CARD_TIMEOUT) as http:
                response = await http.get(url)
            if response.status_code != 200:
                return None
            data = response.json().get("data")
            return data if isinstance(data, dict) else None
        except (httpx.HTTPError, ValueError):
            return None

    return check


__all__ = ["call_agent", "http_card_fetcher", "http_review_checker"]
