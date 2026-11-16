"""월 마감 체크리스트 기한 지남 → 받은 요청(사람 할 일함)에 올린다 (11/16, close_alerts.py)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime

import pytest

from ax_agent_template.routing import Assignment
from ax_contracts.schemas import HandoffCreate, HandoffTask
from finance_agent import close_alerts, finance_api, handlers, persist
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state(MockSources())
    monkeypatch.setattr(
        close_alerts.routing,
        "assignment_for",
        lambda service, skill: Assignment(unit="재무/정산", assignee="태윤"),
    )


class Inbox:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.got: list[HandoffCreate] = []

    async def __call__(self, body: HandoffCreate) -> HandoffTask:
        if self.fail:
            raise ValueError("inbox down")
        self.got.append(body)
        return HandoffTask(
            task_id=f"hof_{len(self.got)}",
            context_id=body.context_id,
            from_service=body.from_service,
            to_service=body.to_service,
            skill=body.skill,
            input=body.input,
            reason=body.reason,
            assignee_unit=body.assignee_unit,
            assignee=body.assignee,
            created_at=datetime.now(UTC),
        )


def _checklist(month: str = "2026-11") -> dict:
    return {
        "month": month,
        "items": [
            {"key": "daily_closing", "label": "일 결산 확정", "status": "pending",
             "detail": "남은 날 2일", "link": "closing", "due": "2026-11-02", "overdue": True},
            {"key": "bank", "label": "은행 입금 대조", "status": "done", "detail": "차이 0일",
             "link": "reconciliation", "due": "2026-12-03", "overdue": False},
            {"key": "month_closing", "label": "월 결산 확정 (사람)", "status": "pending",
             "detail": "결산 탭에서 확정", "link": "closing", "due": "2026-12-05",
             "overdue": False},
        ],
    }  # fmt: skip


def test_an_overdue_item_goes_to_the_inbox_once() -> None:
    inbox, sent = Inbox(), {}
    out = asyncio.run(close_alerts.alert_overdue([_checklist()], sent, poster=inbox))
    assert [r["status"] for r in out] == ["posted"]
    [body] = inbox.got
    assert (body.skill, body.from_service, body.assignee_unit, body.assignee) == (
        "close_overdue",
        "finance",
        "재무/정산",
        "태윤",
    )
    assert body.input["item"] == "daily_closing" and body.input["due"] == "2026-11-02"
    assert body.input["link"] == "/finance/daily#closing"
    assert "기한(2026-11-02)이 지났어요" in body.reason
    assert body.idempotency_key == "close_overdue:shop-demo:2026-11:daily_closing:2026-11-02"
    # 같은 달·항목·기한은 다시 올리지 않는다
    assert asyncio.run(close_alerts.alert_overdue([_checklist()], sent, poster=inbox)) == []
    assert len(inbox.got) == 1
    # 기한이 바뀌면(다음 남은 날) 새로 올린다
    moved = _checklist()
    moved["items"][0]["due"] = "2026-11-05"
    assert len(asyncio.run(close_alerts.alert_overdue([moved], sent, poster=inbox))) == 1
    # 화면에는 올린 항목에 받은 요청 번호가 붙는다
    view = _checklist()
    close_alerts.mark_alerted(view, sent, "shop-demo")
    assert view["items"][0]["alert"]["task_id"] == "hof_1"
    assert "alert" not in view["items"][1]


def test_no_assignee_in_the_org_chart_is_not_invented(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(close_alerts.routing, "assignment_for", lambda service, skill: None)
    inbox, sent = Inbox(), {}
    out = asyncio.run(close_alerts.alert_overdue([_checklist()], sent, poster=inbox))
    assert [r["status"] for r in out] == ["no_assignee"]
    assert inbox.got == [] and sent == {}


def test_an_unreachable_inbox_is_retried_next_time() -> None:
    sent: dict = {}
    out = asyncio.run(close_alerts.alert_overdue([_checklist()], sent, poster=Inbox(fail=True)))
    assert [r["status"] for r in out] == ["failed"] and sent == {}
    out = asyncio.run(close_alerts.alert_overdue([_checklist()], sent, poster=Inbox()))
    assert [r["status"] for r in out] == ["posted"]


def test_mock_mode_does_not_post() -> None:
    assert asyncio.run(close_alerts.alert_overdue([_checklist()], {})) == []


def test_last_month_is_checked_after_the_month_turns(monkeypatch: pytest.MonkeyPatch) -> None:
    """월 결산 확정은 기한이 다음 달 초라, 달이 바뀐 뒤 지난달 체크리스트에서 기한이 지난다."""
    inbox = Inbox()
    monkeypatch.setattr(close_alerts, "post", inbox)
    monkeypatch.setattr(close_alerts.template_config, "is_mock", lambda name: False)
    out = asyncio.run(finance_api.run_close_alerts(date(2026, 12, 20)))
    months = {b.input["month"] for b in inbox.got}
    assert "2026-11" in months  # 지난달
    nov = [b for b in inbox.got if b.input["month"] == "2026-11"]
    assert any(b.input["item"] == "month_closing" for b in nov)
    assert all(r["status"] == "posted" for r in out)
    assert set(handlers.state().records.close_alerts) == {b.idempotency_key for b in inbox.got}
    # 체크리스트 화면에 받은 요청 번호가 붙는다
    books = asyncio.run(handlers.state().books())
    view = asyncio.run(finance_api.checklist_view(books, date(2026, 11, 30), date(2026, 12, 20)))
    closing = next(i for i in view["items"] if i["key"] == "month_closing")
    assert closing["alert"]["assignee_unit"] == "재무/정산"


def test_alert_records_survive_a_restart(tmp_path) -> None:
    sent = handlers.state().records.close_alerts
    asyncio.run(close_alerts.alert_overdue([_checklist()], sent, poster=Inbox()))
    path = tmp_path / "state.json"
    persist.save(handlers.state(), path)
    handlers.reset_state(MockSources())
    assert persist.load(handlers.state(), path)
    assert list(handlers.state().records.close_alerts) == [
        "close_overdue:shop-demo:2026-11:daily_closing:2026-11-02"
    ]
