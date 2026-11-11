"""고객 아이디어 일일 보고: 받기(중복·거절), 기간별(주·월·연) 합산, 권한."""

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas.idea import IdeaReportInput
from data_agent import handlers, idea_reports
from data_agent.main import app, load_card

CTX = "ctx_ui_idea0001"
SUPPORT = "mock:support"
client = TestClient(app)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(idea_reports.DB_ENV, raising=False)
    handlers.reset_idea_report_store()


def _call(skill: str, input: dict, token: str = SUPPORT, tenant: str | None = None) -> dict:
    meta = {
        "context_id": CTX,
        "token": token,
        "path": [],
        "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
    }
    if tenant:
        meta["tenant_id"] = tenant
    return client.post("/a2a", json={"meta": meta, "skill": skill, "input": input}).json()


def _cluster(key: str, count: int, customers: int | None = None) -> dict:
    return {"key": key, "label": key, "count": count, "customer_count": customers or count}


def _report(day: str, clusters: list[dict], total: int | None = None) -> dict:
    return {
        "report_date": day,
        "total_ideas": total if total is not None else sum(c["count"] for c in clusters),
        "clusters": clusters,
    }


def _send(day: str, clusters: list[dict], **kw) -> dict:
    return _call("receive_idea_report", _report(day, clusters), **kw)


# ---------------------------------------------------------------- 받기


def test_a_report_is_accepted_once_per_day() -> None:
    first = _send("2026-11-09", [_cluster("생일 쿠폰", 3)])
    assert first["outcome"] == "accepted" and first["output"]["report_date"] == "2026-11-09"
    again = _send("2026-11-09", [_cluster("다른 말", 9)])
    assert again["outcome"] == "duplicate"
    listing = _call("list_idea_reports", {"period": "day"}, token="mock:planning")["output"]
    (bucket,) = listing["buckets"]
    assert bucket["top"][0]["label"] == "생일 쿠폰"  # 보관본은 그대로


def test_the_same_day_of_another_company_is_a_separate_report() -> None:
    assert _send("2026-11-09", [_cluster("a b", 1)], tenant="shop-a")["outcome"] == "accepted"
    assert _send("2026-11-09", [_cluster("a b", 1)], tenant="shop-b")["outcome"] == "accepted"
    mine = _call("list_idea_reports", {"period": "day"}, tenant="shop-a")["output"]
    assert len(mine["buckets"]) == 1


def test_future_dates_and_inconsistent_counts_are_rejected() -> None:
    far = (datetime.now(UTC).date() + timedelta(days=5)).isoformat()
    assert _send(far, [_cluster("a b", 1)])["output"]["reason_code"] == "future_date"
    bad = _call("receive_idea_report", _report("2026-11-09", [_cluster("a b", 5)], total=2))
    assert bad["output"]["reason_code"] == "inconsistent_counts"
    assert _call("list_idea_reports", {})["output"]["unknown_reason"]  # 아무것도 저장 안 됨


def test_only_support_may_send_a_report() -> None:
    out = _call(
        "receive_idea_report", _report("2026-11-09", [_cluster("a b", 1)]), token="mock:planning"
    )
    assert out["status"] != "completed"


def test_the_skills_are_in_the_card_and_handlers() -> None:
    ids = {s.id for s in load_card().skills}
    assert {"receive_idea_report", "list_idea_reports"} <= ids
    assert {"receive_idea_report", "list_idea_reports"} <= set(handlers.HANDLERS)


# ---------------------------------------------------------------- 기간별 합산


def _seed() -> None:
    # 2026-11-02(월)~08(일) 주, 2026-11-09(월)~ 주, 2025-12에 하루, 2026-01에 하루
    _send("2026-11-03", [_cluster("생일 쿠폰", 4, 3), _cluster("무료 배송", 2, 2)])
    _send("2026-11-05", [_cluster("생일 쿠폰", 2, 2)])
    _send("2026-11-10", [_cluster("무료 배송", 5, 4)])
    _send("2025-12-01", [_cluster("적립금 두배", 1)])
    _send("2026-01-04", [_cluster("생일 쿠폰", 1)])


def _trends(**q) -> dict:
    return _call("list_idea_reports", q)["output"]


def test_weeks_start_on_monday_and_newest_comes_first() -> None:
    _seed()
    weeks = _trends(period="week")["buckets"]
    starts = [b["start"] for b in weeks]
    assert starts == sorted(starts, reverse=True)
    first_week = next(b for b in weeks if b["start"] == "2026-11-02")
    assert first_week["end"] == "2026-11-08" and first_week["report_days"] == 2
    top = first_week["top"][0]
    assert (top["label"], top["count"], top["customer_count"], top["days"]) == (
        "생일 쿠폰",
        6,
        5,
        2,
    )
    assert first_week["total_ideas"] == 8


def test_months_and_years_add_up_the_daily_reports() -> None:
    _seed()
    months = {b["start"]: b for b in _trends(period="month")["buckets"]}
    nov = months["2026-11-01"]
    assert nov["end"] == "2026-11-30" and nov["report_days"] == 3
    assert [(r["label"], r["count"]) for r in nov["top"]] == [("무료 배송", 7), ("생일 쿠폰", 6)]
    years = {b["start"]: b for b in _trends(period="year")["buckets"]}
    assert years["2026-01-01"]["end"] == "2026-12-31"
    assert years["2026-01-01"]["total_ideas"] == 6 + 2 + 5 + 1
    assert years["2026-01-01"]["top"][0]["label"] == "생일 쿠폰"  # 6 + 1 = 7, 무료 배송은 7
    assert years["2025-01-01"]["top"][0]["label"] == "적립금 두배"


def test_similar_wording_across_days_is_merged_into_one_request() -> None:
    _send("2026-11-03", [_cluster("생일 쿠폰 이벤트", 3)])
    _send("2026-11-04", [_cluster("생일 쿠폰", 2)])  # 말 조각이 크게 겹침 (2/3)
    _send("2026-11-05", [_cluster("무료 배송 이벤트", 1)])  # 겹침 1/5 → 다른 요청
    (week,) = _trends(period="week")["buckets"]
    assert [(r["label"], r["count"], r["days"]) for r in week["top"]] == [
        ("생일 쿠폰 이벤트", 5, 2),
        ("무료 배송 이벤트", 1, 1),
    ]


def test_top_and_buckets_limits_apply() -> None:
    _seed()
    out = _trends(period="week", buckets=1, top=1)
    assert len(out["buckets"]) == 1 and len(out["buckets"][0]["top"]) == 1
    assert _call("list_idea_reports", {"top": 99}).get("status") != "completed"  # 상한 초과


def test_period_helpers() -> None:
    assert idea_reports.period_start(date(2026, 11, 8), "week") == date(2026, 11, 2)
    assert idea_reports.period_end(date(2026, 2, 1), "month") == date(2026, 2, 28)
    assert idea_reports.period_end(date(2028, 2, 1), "month") == date(2028, 2, 29)


def test_reports_survive_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "ideas.db")
    store = idea_reports.IdeaReportStore(path)
    report = IdeaReportInput(**_report("2026-11-03", [_cluster("a b", 2)]))
    store.add("t", report, datetime.now(UTC))
    store.close()
    again = idea_reports.IdeaReportStore(path)
    assert [r.report_date for r in again.reports("t")] == [date(2026, 11, 3)]
    again.close()
