"""고객 아이디어 일일 보고: 같은 뜻 묶기, 하루치 보고 만들기, 데이터로 넘기기, 기간별 읽기."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from support_agent import handlers, ideas
from support_agent.ideas import Idea, IdeaStore
from support_agent.main import ALL_HANDLERS, app, load_card
from support_agent.store import InquiryStore

DAY = date(2026, 11, 9)


class Sent(list[AgentRequest]):
    pass


@pytest.fixture(autouse=True)
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SUPPORT_REPORT_TZ", "Asia/Seoul")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(handlers, "IDEAS", IdeaStore())
    log = Sent()

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        assert target == "data"
        log.append(req)
        if req.skill == "list_idea_reports":
            output: dict[str, Any] = {"period": req.input["period"], "buckets": []}
        else:
            output = {"outcome": "accepted", "report_date": req.input["report_date"]}
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=output)

    monkeypatch.setattr(handlers, "SEND", fake_send)
    return log


def call(skill: str, input: dict[str, Any] | None = None, requester: str = "dashboard-api") -> Any:
    body = {
        "meta": {
            "context_id": "ctx_ui_idea00000001",
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input or {},
    }
    return TestClient(app).post("/a2a", json=body).json()


def add(text: str, user: int, kind: str = "event", at: str = "2026-11-09T03:00:00Z") -> Idea:
    idea = handlers.IDEAS.add(
        tenant_id=TENANT_ID_DEFAULT,
        context_id="ctx_shop_x",
        user_id=user,
        kind=kind,
        text=text,  # type: ignore[arg-type]
    )
    handlers.IDEAS._items[f"{TENANT_ID_DEFAULT}:{idea.idea_id}"] = idea.model_copy(
        update={"created_at": at}
    )
    return handlers.IDEAS.get(TENANT_ID_DEFAULT, idea.idea_id)  # type: ignore[return-value]


# ---------------------------------------------------------------- 같은 뜻 묶기


def test_similar_requests_form_one_cluster_ranked_by_count() -> None:
    items = [
        add("생일에 쿠폰을 주는 이벤트가 있으면 좋겠어요", 1),
        add("생일 쿠폰 이벤트를 열어 주세요", 2),
        add("생일 축하 쿠폰을 보내 주면 좋겠어요", 3),
        add("포인트를 두 배로 주는 요일 이벤트", 4),
    ]
    clusters = ideas.cluster_ideas(items)
    top = clusters[0]
    assert (top.count, top.customer_count) == (3, 3)
    assert "생일" in top.label and "쿠폰" in top.label
    assert top.key == " ".join(sorted(top.label.split()))
    assert clusters[1].count == 1 and "포인트" in clusters[1].label


def test_the_same_customer_counts_once_in_customer_count() -> None:
    items = [
        add("생일 쿠폰 이벤트 부탁해요", 1),
        add("생일 쿠폰 이벤트를 꼭 해 주세요", 1),  # 같은 고객이 다른 글로 다시 냄
        add("생일 쿠폰 이벤트 좋겠어요", 2),
    ]
    (top,) = ideas.cluster_ideas(items)
    assert (top.count, top.customer_count) == (3, 2)


def test_clusters_never_carry_customer_text() -> None:
    items = [add("생일에 쿠폰을 주는 이벤트가 있으면 좋겠어요", 1)]
    (cluster,) = ideas.cluster_ideas(items)
    dumped = cluster.model_dump_json()
    assert "있으면 좋겠어요" not in dumped and "주는" not in dumped


def test_the_days_are_counted_in_the_company_time_zone() -> None:
    # 11-09 15:30Z는 한국 시간으로 11-10 00:30이다
    late = add("생일 쿠폰 이벤트 열어 주세요", 1, at="2026-11-09T15:30:00Z")
    early = add("생일 쿠폰 이벤트 부탁해요", 2, at="2026-11-09T14:59:00Z")
    items = [late, early]
    assert ideas.daily_report(items, date(2026, 11, 9))[0] == 1
    assert ideas.daily_report(items, date(2026, 11, 10))[0] == 1


def test_only_event_ideas_are_reported() -> None:
    items = [
        add("생일 쿠폰 이벤트 열어 주세요", 1),
        add("재입고 알림 기능이 좋겠어요", 2, kind="feature"),
    ]
    total, clusters = ideas.daily_report(items, DAY)
    assert total == 1 and len(clusters) == 1


# ---------------------------------------------------------------- 데이터로 넘기기


def test_the_daily_report_is_sent_to_data_without_customer_text(sent: Sent) -> None:
    for user in (1, 2, 3):
        add(f"생일 쿠폰 이벤트를 열어 주세요 {user}번 고객", user)
    out = call("send_idea_report", {"report_date": "2026-11-09"})["output"]
    assert out["outcome"] == "accepted" and out["total_ideas"] == 3 and out["clusters"] >= 1
    (req,) = sent
    assert req.skill == "receive_idea_report"
    assert req.input["report_date"] == "2026-11-09" and req.input["total_ideas"] == 3
    top = req.input["clusters"][0]
    assert top["count"] == 3 and top["customer_count"] == 3
    assert "열어 주세요" not in str(req.input)  # 원문은 보내지 않는다
    assert req.meta.idempotency_key.endswith(f"send_idea_report:{TENANT_ID_DEFAULT}:2026-11-09")


def test_a_day_without_ideas_sends_nothing(sent: Sent) -> None:
    out = call("send_idea_report", {"report_date": "2026-11-09"})["output"]
    assert out["outcome"] == "no_ideas" and sent == []


def test_without_a_date_yesterday_is_reported(sent: Sent) -> None:
    now = datetime.now(ideas.report_tz())
    yesterday = (now - timedelta(days=1)).astimezone(UTC).replace(hour=3).isoformat()
    add("생일 쿠폰 이벤트 열어 주세요", 1, at=yesterday.replace("+00:00", "Z"))
    out = call("send_idea_report")["output"]
    assert out["report_date"] == (now.date() - timedelta(days=1)).isoformat()


@pytest.mark.parametrize("reply_outcome", ["duplicate", "rejected"])
def test_data_answers_are_passed_through(
    monkeypatch: pytest.MonkeyPatch, reply_outcome: str
) -> None:
    add("생일 쿠폰 이벤트 열어 주세요", 1)

    async def reply(target: str, req: AgentRequest) -> AgentResult:
        out = {"outcome": reply_outcome, "report_date": req.input["report_date"]}
        if reply_outcome == "rejected":
            out["reason_code"] = "future_date"
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)

    monkeypatch.setattr(handlers, "SEND", reply)
    out = call("send_idea_report", {"report_date": "2026-11-09"})["output"]
    assert out["outcome"] == reply_outcome


def test_an_unreachable_data_is_reported_so_the_day_can_be_resent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    add("생일 쿠폰 이벤트 열어 주세요", 1)

    async def down(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unavailable")

    monkeypatch.setattr(handlers, "SEND", down)
    out = call("send_idea_report", {"report_date": "2026-11-09"})["output"]
    assert out["outcome"] == "data_unavailable"

    async def boom(target: str, req: AgentRequest) -> AgentResult:
        raise ConnectionError

    monkeypatch.setattr(handlers, "SEND", boom)
    assert call("send_idea_report", {"report_date": "2026-11-09"})["output"]["outcome"] == (
        "data_unavailable"
    )


def test_only_the_dashboard_may_send_the_daily_report(sent: Sent) -> None:
    add("생일 쿠폰 이벤트 열어 주세요", 1)
    out = call("send_idea_report", {"report_date": "2026-11-09"}, requester="shop-api")
    assert out["status"] != "completed" and sent == []


# ---------------------------------------------------------------- 기간별 읽기


def test_trends_are_read_from_data(sent: Sent) -> None:
    out = call("list_idea_trends", {"period": "month", "buckets": 3})["output"]
    assert out["period"] == "month" and out["buckets"] == []
    assert sent[0].skill == "list_idea_reports"
    assert sent[0].input == {"period": "month", "buckets": 3, "top": 5}


def test_trends_say_so_when_data_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(target: str, req: AgentRequest) -> AgentResult:
        raise ConnectionError

    monkeypatch.setattr(handlers, "SEND", boom)
    out = call("list_idea_trends")["output"]
    assert out["buckets"] == [] and out["unknown_reason"]


def test_the_skills_are_in_the_card_and_handlers() -> None:
    ids = {s.id for s in load_card().skills}
    for skill in ("send_idea_report", "list_idea_trends"):
        assert skill in ids and skill in ALL_HANDLERS
