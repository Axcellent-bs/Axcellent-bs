"""조회당 제작비에 회사 유튜브 조회수 더하기 (11/23).

마케팅이 캠페인에 연결한 회사 유튜브 영상의 조회수(`youtube_views`)를 데이터 영상 성과 7일 값
(`views`)과 따로 받는다. 재무는 둘을 더해 조회당 제작비를 내고 어디서 온 숫자인지 남긴다.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from ax_contracts.schemas.finance import CampaignResultInput
from finance_agent import campaign as cp
from finance_agent import handlers as fh
from finance_agent.handlers import reset_state, state
from finance_agent.sources import MockSources


class _AnyStarted(dict):
    def __contains__(self, key: object) -> bool:
        return True

    def __missing__(self, key: str) -> dict:
        return {"campaign_id": key, "total_krw": 10**12, "active_krw": 0, "month": "2026-11"}


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    reset_state(MockSources())
    state().budget_override = {"amount_krw": 1_000_000, "basis": "시험", "provisional": True}
    state().started_override = _AnyStarted()


def _row(cid: str, category: str, production: int | None, views: int | None, point: str | None,
         youtube: int | None) -> dict[str, Any]:  # fmt: skip
    return {
        "campaign_id": cid,
        "campaign_name": f"{category} {cid}",
        "channel": "유튜브",
        "period_from": "2026-11-03",
        "period_to": "2026-11-10",
        "spend_krw": 0,
        "source": "data.video_performance",
        "content_category": category,
        "format": "shorts",
        "production_cost_krw": production,
        "views": views,
        "views_point": point,
        "youtube_views": youtube,
        "youtube_views_at": "2026-11-23T01:00:00+00:00" if youtube is not None else None,
        "youtube_video_ids": ["vidAAAAAAAA"] if youtube is not None else None,
    }


def _cost(*args: Any) -> dict[str, Any]:
    return cp.view_cost(CampaignResultInput(**_row("c1", "아재개그", *args)).model_dump())


@pytest.mark.parametrize(
    ("production", "views", "point", "youtube", "status", "per_view", "total"),
    [
        (100_000, 8_000, "7d", 2_000, "measured", 10.0, 10_000),  # 영상 성과 + 유튜브
        (100_000, None, None, 4_000, "measured", 25.0, 4_000),  # 유튜브만
        (100_000, 10_000, "7d", None, "measured", 10.0, 10_000),  # 옛 결과와 같다
        (100_000, 8_000, "1d", 2_000, "measuring", None, None),  # 영상 성과가 7일 전이면 기다린다
        (100_000, None, None, None, "no_views", None, None),
        (None, 8_000, "7d", 2_000, "no_production_cost", None, None),
        (100_000, None, None, 0, "zero_views", None, 0),
    ],
)
def test_cost_per_view_adds_youtube_views_and_keeps_sources(
    production: int | None,
    views: int | None,
    point: str | None,
    youtube: int | None,
    status: str,
    per_view: float | None,
    total: int | None,
) -> None:
    got = _cost(production, views, point, youtube)
    assert (got["view_cost_status"], got["cost_per_view"], got["views_total"]) == (
        status,
        per_view,
        total,
    )
    assert got["views_from"] == {
        "video_performance": views,
        "youtube": youtube,
    }


def test_categories_sum_both_sources_and_show_the_breakdown() -> None:
    campaigns = {
        r["campaign_id"]: cp.compute_result(CampaignResultInput(**r).model_dump())
        for r in (
            _row("a1", "아재개그", 100_000, 8_000, "7d", 2_000),
            _row("a2", "아재개그", 100_000, None, None, 10_000),
            _row("b1", "몸개그", 100_000, 2_500, "7d", None),
        )
    }
    rows = {r["content_category"]: r for r in cp.category_view_costs(campaigns)}
    a = rows["아재개그"]
    assert (a["views"], a["performance_views"], a["youtube_views"]) == (20_000, 8_000, 12_000)
    assert a["cost_per_view"] == 10.0  # 200,000 ÷ 20,000
    assert cp.views_text(a) == "20,000회(영상 성과 7일 8,000 + 회사 유튜브 12,000)"
    assert cp.views_text(rows["몸개그"]) == "2,500회"  # 유튜브가 없으면 옛 문구 그대로
    shift = cp.budget_shift(campaigns)
    assert shift is not None
    assert any("회사 유튜브 12,000" in e["value"] for e in shift["evidence"])


def test_old_records_without_views_total_still_sum() -> None:
    old = cp.compute_result(CampaignResultInput(**_row("o1", "말장난", 50_000, 5_000, "7d", None))
                            .model_dump())  # fmt: skip
    for key in ("views_total", "views_from"):
        old.pop(key)  # 이 칸이 생기기 전에 저장된 기록
    (row,) = cp.category_view_costs({"o1": old})
    assert (row["views"], row["performance_views"], row["cost_per_view"]) == (5_000, 5_000, 10.0)


def test_resend_without_youtube_keeps_the_earlier_youtube_views() -> None:
    first = CampaignResultInput(**_row("k1", "아재개그", 100_000, 8_000, "7d", 2_000))
    asyncio.run(fh.record_result(first, "ctx_cmp_k1"))
    again = CampaignResultInput(**_row("k1", "아재개그", 100_000, 8_000, "7d", None))
    record = asyncio.run(fh.record_result(again, "ctx_cmp_k1"))
    assert record["youtube_views"] == 2_000 and record["youtube_video_ids"] == ["vidAAAAAAAA"]
    assert record["write"] == "already_recorded"
    assert state().records.campaigns["k1"]["cost_per_view"] == 10.0
