"""캠페인별 광고 숫자 skill (docs/marketing-ad-metrics-design.md B, 목 모드, 외부 호출 없음)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.ad_metrics import GetAdMetricsInput, RecordAdMetricsInput
from marketing_agent import ad_metrics, handlers
from marketing_agent.main import app

CTX = "ctx_ad_0001"
client = TestClient(app)
CMP = "cmp_PROP-1"
DAY = "2026-11-20"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    gated: list[str] = []

    async def fake_execute(action, params, context, evidence):  # noqa: ANN001
        gated.append(action)
        return ExecuteDecision(execution_id="exe_ad1", grade="A", status="executed")

    monkeypatch.setattr(handlers, "execute", fake_execute)
    monkeypatch.setattr(ad_metrics, "today_kst", lambda: datetime(2026, 11, 23).date())
    _receive()
    gated.clear()  # 기획안 받기도 관문을 거친다. 광고 숫자 호출만 센다
    return gated


def _call(skill: str, input: dict, token: str = "mock:dashboard-api") -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input,
    }
    return client.post("/a2a", json=body).json()


def _receive(proposal_id: str = "PROP-1") -> None:
    brief = {
        "schema_version": "1",
        "proposal_id": proposal_id,
        "insight_id": "INS-7",
        "title": "독서의 달 기획전",
        "idea": "11월 독서 키워드 상품을 묶어 배너로 알린다",
        "why": "독서 검색이 작년 11월 평균의 1.6배였다",
        "facts": [{"label": "독서 검색", "value": "1.6배", "source": "t", "link": None}],
        "target": {"label": "주문 증분", "value": "+10% 이상", "measure": None},
        "products": [],
        "format": "banner",
        "approved_by": "예진",
        "note": None,
        "body_md": None,
    }
    assert _call("receive_campaign_brief", brief, token="mock:planning")["output"]["outcome"] in (
        "accepted",
        "duplicate",
    )


def _row(**over: object) -> dict:
    return {
        "campaign_id": CMP,
        "date": DAY,
        "source": "instagram",
        "medium": "social",
        "impressions": 1000,
        "clicks": 30,
        "spend_krw": 50_000,
        "entered_by": "태윤",
    } | over


def test_record_then_get_keeps_unknown_apart_from_zero(_mock_mode: list[str]) -> None:
    out = _call("record_campaign_ad_metrics", _row())
    assert out["output"]["outcome"] == "recorded" and _mock_mode == ["record_campaign_ad_metrics"]
    banner = _row(date="2026-11-21", source="banner", medium="onsite", spend_krw=0)
    assert _call("record_campaign_ad_metrics", banner)["output"]["outcome"] == "recorded"
    unknown = _row(date="2026-11-22", spend_krw=None, clicks=None)
    assert _call("record_campaign_ad_metrics", unknown)["output"]["outcome"] == "recorded"

    got = _call("get_campaign_ad_metrics", {"campaign_id": CMP})["output"]
    assert got["status"] == "ok" and len(got["rows"]) == 3
    assert [r["spend_krw"] for r in got["rows"]] == [50_000, 0, None]  # 0원과 모름이 다르다
    totals = got["totals"]
    assert totals["impressions"] == 3000
    assert totals["spend_krw"] is None and totals["clicks"] is None
    assert totals["missing_dates"] == {"spend_krw": ["2026-11-22"], "clicks": ["2026-11-22"]}
    assert "ctr" not in str(got) and "roas" not in str(got)  # 비율은 데이터분석이 계산한다
    assert got["combos"]["banner"] == ["onsite"]


def test_get_by_period_and_unknown_when_empty() -> None:
    assert _call("get_campaign_ad_metrics", {"campaign_id": CMP})["output"]["status"] == "unknown"
    _call("record_campaign_ad_metrics", _row())
    _call("record_campaign_ad_metrics", _row(date="2026-11-22"))
    period = {"date_from": "2026-11-21", "date_to": "2026-11-22"}
    got = _call("get_campaign_ad_metrics", period)["output"]
    assert [r["date"] for r in got["rows"]] == ["2026-11-22"]
    both_ends = {"date_from": DAY, "date_to": "2026-11-22"}
    assert len(_call("get_campaign_ad_metrics", both_ends)["output"]["rows"]) == 2
    empty = _call("get_campaign_ad_metrics", {"date_from": "2026-01-01", "date_to": "2026-01-02"})
    assert empty["output"]["status"] == "unknown" and empty["output"]["reason"] == "아직 입력 없음"


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"campaign_id": "cmp_PROP-404"}, "unknown_campaign"),
        ({"date": "2026-11-24"}, "future_date"),
        ({"date": "2026-02-30"}, "invalid_date"),
        ({"source": "banner", "medium": "cpc"}, "invalid_source_medium"),
        ({"clicks": 2000}, "clicks_over_impressions"),
    ],
)
def test_rejections_do_not_store_or_call_the_gate(
    _mock_mode: list[str], over: dict, reason: str
) -> None:
    out = _call("record_campaign_ad_metrics", _row(**over))["output"]
    assert (out["outcome"], out["reason_code"]) == ("rejected", reason)
    assert _mock_mode == []
    assert _call("get_campaign_ad_metrics", {"campaign_id": CMP})["output"]["status"] == "unknown"


def test_negative_values_are_invalid_input() -> None:
    assert _call("record_campaign_ad_metrics", _row(spend_krw=-1))["status"] != "completed"


def test_rewrite_keeps_revisions_and_same_request_is_idempotent() -> None:
    _call("record_campaign_ad_metrics", _row())
    again = _call("record_campaign_ad_metrics", _row())["output"]
    assert again["outcome"] == "unchanged" and again["row"]["revisions"] == 0
    fixed = _call("record_campaign_ad_metrics", _row(spend_krw=55_000, entered_by="재헌"))["output"]
    assert fixed["outcome"] == "updated" and fixed["row"]["revisions"] == 1
    assert fixed["row"]["entered_by"] == "태윤"  # 처음 적은 사람은 그대로
    history = handlers.STATE.ad_metrics.revisions(CMP)
    assert history[0]["before"]["spend_krw"] == 50_000 and history[0]["changed_by"] == "재헌"


def test_gate_refusal_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def held(action, params, context, evidence):  # noqa: ANN001
        return ExecuteDecision(execution_id="exe_ad2", grade="A", status="rejected")

    monkeypatch.setattr(handlers, "execute", held)
    out = _call("record_campaign_ad_metrics", _row())
    assert out["status"] != "completed" or out["output"].get("outcome") != "recorded"
    assert _call("get_campaign_ad_metrics", {"campaign_id": CMP})["output"]["status"] == "unknown"


def test_requesters_follow_the_card() -> None:
    for who in ("mock:data", "mock:finance", "mock:orchestrator"):
        assert _call("get_campaign_ad_metrics", {"campaign_id": CMP}, token=who)["status"] in (
            "completed",
        )
    refused = _call("get_campaign_ad_metrics", {"campaign_id": CMP}, token="mock:planning")
    assert refused["status"] == "rejected"
    write = _call("record_campaign_ad_metrics", _row(), token="mock:data")
    assert write["status"] == "rejected"


def test_store_survives_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "ad.db")
    first = ad_metrics.AdMetricsStore(path)
    first.record(RecordAdMetricsInput.model_validate(_row()))
    again = ad_metrics.AdMetricsStore(path)
    rows = again.query(GetAdMetricsInput(campaign_id=CMP))
    assert [(r.date, r.spend_krw) for r in rows] == [(DAY, 50_000)]
