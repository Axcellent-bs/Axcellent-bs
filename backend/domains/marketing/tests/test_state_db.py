"""공지·쿠폰 기록은 다시 켜도 남고, 못 보낸 이벤트는 outbox로 다시 보낸다 (2026-11-23)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ax_agent_template import events
from ax_contracts.schemas import AgentResult, EventEnvelope, EventType, RequestMeta, TaskStatus
from marketing_agent.coupons import CouponLedger
from marketing_agent.notice import NoticeStore, make_draft
from marketing_agent.schemas import DraftNoticeInput
from marketing_agent.state_db import StateDb, emit, flush

NOW = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)


def _draft(kind: str, component: str = "payment", ctx: str = "ctx_a"):  # noqa: ANN202
    data = DraftNoticeInput.model_validate({"kind": kind, "facts": {"component": component}})
    return make_draft(ctx, data)


def _result(key: str) -> AgentResult:
    meta = RequestMeta(context_id="ctx_c", token="t", idempotency_key=key, deadline=NOW)
    return AgentResult(meta=meta, status=TaskStatus.COMPLETED, output={"coupon_id": "cpn_0001"})


def _envelope(n: int) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt_{n:04d}",
        type=EventType.COUPON_ISSUED,
        occurred_at=NOW,
        source="marketing",
        idempotency_key=f"k{n}",
        data={},
    )


def test_notice_banner_list_survives_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "state.db")
    first = NoticeStore(now=lambda: NOW, db=StateDb(path))
    opened = _draft("incident_opened")
    first.save_draft(opened)
    nid = first.publish("ctx_a", opened)
    first.join("ctx_b", _draft("incident_opened", ctx="ctx_b"), nid)  # 다른 사건이 같은 배너를 씀

    again = NoticeStore(now=lambda: NOW, db=StateDb(path))
    active, total = again.active()
    assert total == 1 and active[0].notice_id == nid
    assert again.get_draft(opened.draft_id) == opened
    assert again.published_id(opened.draft_id) == nid
    assert again.can_end("ctx_a", nid) is False  # 같이 쓰는 사건도 남았다
    assert again.end_incident_notices("ctx_a") == []
    assert again.end_incident_notices("ctx_b") == [nid]

    third = NoticeStore(now=lambda: NOW, db=StateDb(path))
    assert third.active() == ([], 0)  # 끝난 것도 남는다
    assert third.publish("ctx_z", _draft("incident_opened", "api", "ctx_z")) == "ntc_0002"


def test_coupon_dedup_survives_a_restart(tmp_path: Path) -> None:
    path = str(tmp_path / "state.db")
    first = CouponLedger(db=StateDb(path))
    assert first.new_coupon_id() == "cpn_0001"
    done = _result("key-1")
    first.record("ctx_c", "key-1", 1, done, cache=True)
    first.record("ctx_c", "key-2", 2, _result("key-2"), cache=False)  # 보류: 번호만 소비

    again = CouponLedger(db=StateDb(path))
    assert again.previous_by_key("key-1") == done
    assert again.previous_by_sequence("ctx_c", 1) == done
    assert again.previous_by_sequence("ctx_c", 2) is None and again.issued_count() == 1
    again.check_sequence("ctx_c", 3)  # 다음 번호는 3
    assert again.new_coupon_id() == "cpn_0002"


def test_without_a_bus_events_wait_in_the_outbox_then_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = StateDb()
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("MARKETING_MOCK", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    events.reset_mock_bus()
    assert asyncio.run(emit(db, _envelope(1))) is False  # REDIS_URL이 없으면 쌓인다
    db.enqueue(_envelope(1))  # 같은 event_id는 한 건만
    assert db.outbox_count() == 1
    assert asyncio.run(flush(db)) == 0 and db.outbox_count() == 1  # 아직 버스가 없다

    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")  # 버스가 생겼다(목 버스)
    db.enqueue(_envelope(2))
    assert asyncio.run(flush(db)) == 2 and db.outbox_count() == 0
    assert [e.event_id for e in events.MOCK_PUBLISHED][-2:] == ["evt_0001", "evt_0002"]
    events.reset_mock_bus()


def test_published_hook_runs_once_on_published(monkeypatch: pytest.MonkeyPatch) -> None:
    from marketing_agent.sns_publish import publisher

    seen: list[tuple[object, str | None]] = []

    class Store:
        def transition(self, *_a: object, **_k: object) -> bool:
            return True

    monkeypatch.setattr(publisher, "store", lambda: Store())
    monkeypatch.setattr(publisher, "PUBLISHED_HOOKS", [lambda r, m, _res: seen.append((r, m))])
    monkeypatch.setattr(publisher, "_row_log", lambda *a, **k: None)
    row = type(
        "Row", (), {"idempotency_key": "k", "media_id": "m1", "result": {}, "execution_id": None}
    )()
    publisher._close(row, publisher.PUBLISHING, publisher.FAILED, "x")  # noqa: SLF001
    assert seen == []
    publisher._close(row, publisher.PUBLISHING, publisher.PUBLISHED, None)  # noqa: SLF001
    assert seen == [(row, "m1")]
