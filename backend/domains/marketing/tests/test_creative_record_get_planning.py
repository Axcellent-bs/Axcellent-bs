"""기획이 고른 회사 캐릭터를 읽는다 (11/26 H, 운영 wr_6d9d9e6df4 PROP-42: 카드가 dashboard-api만
허용해 기획 호출이 FORBIDDEN_SKILL → "고른 회사 캐릭터를 읽지 못했어요").

- 카드: creative_record_get을 planning도 부를 수 있다(두 카드 같게)
- 기획이 읽을 때는 영상·썸네일·업로드 진행 확인을 하지 않는다(가벼운 읽기, 시간 초과 막기)
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.server import check_acl
from ax_contracts.schemas import AgentCard, AgentRequest, RequestMeta, TaskStatus
from marketing_agent import creative_records as cr
from marketing_agent import handlers

HERE = Path(__file__).resolve()
CARDS = (
    HERE.parents[1] / "agent-card.json",
    HERE.parents[3] / "services" / "coupon" / "agent-card.json",
)
SHEET = {"kind": "virtual_human", "summary_ko": "검은 긴 생머리", "consistency_en": "a woman"}


@pytest.fixture(autouse=True)
def mem_store() -> Iterator[cr.CreativeRecords]:
    s = cr.CreativeRecords(":memory:")
    cr.set_store(s)
    yield s
    cr.set_store(None)


def _req(rid: str, requester: str) -> AgentRequest:
    meta = RequestMeta(
        context_id="proposal:PROP-42",
        token="t",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
        requester=requester,
    )
    return AgentRequest(meta=meta, skill="creative_record_get", input={"record_id": rid})


def _side_work(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []

    def mark(name: str) -> Any:
        async def run(*_a: Any, **_k: Any) -> None:
            seen.append(name)

        return run

    monkeypatch.setattr(handlers._render, "check_video", mark("video"))  # noqa: SLF001
    monkeypatch.setattr(handlers._thumbs, "check", mark("thumbs"))  # noqa: SLF001
    monkeypatch.setattr(handlers._thumbs, "request", mark("thumbs_request"))  # noqa: SLF001
    return seen


@pytest.mark.parametrize("card", CARDS, ids=["marketing", "coupon"])
def test_planning_may_read_a_record(card: Path) -> None:
    parsed = AgentCard.model_validate(json.loads(card.read_text(encoding="utf-8")))
    assert check_acl(parsed, "creative_record_get", "planning")
    assert check_acl(parsed, "creative_record_get", "dashboard-api")
    assert not check_acl(parsed, "creative_record_get", "finance")


def test_planning_read_skips_the_progress_checks(
    mem_store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = mem_store.add_sheet(SHEET, None, None)
    seen = _side_work(monkeypatch)
    got = asyncio.run(handlers.creative_record_get(_req(rid, "planning")))
    assert got.status == TaskStatus.COMPLETED
    assert got.output["sheet"]["consistency_en"] == "a woman"
    assert seen == []
    # 화면이 읽을 때는 지금처럼 이어 만든다
    asyncio.run(handlers.creative_record_get(_req(rid, "dashboard-api")))
    assert "video" in seen and "thumbs_request" in seen
