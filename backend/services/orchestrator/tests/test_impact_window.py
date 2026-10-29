"""영향 분석 구간: 신호가 장애 시작 시각(since)을 주면 그때부터 센다 (앞 장애와 섞이지 않게)."""

from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import _since
from ax_orchestrator.models import Incident
from ax_orchestrator.plans.scenario_a import _window

OPENED = datetime(2026, 10, 28, 7, 24, 1, tzinfo=UTC)


def _incident(since: datetime | None) -> Incident:
    return Incident(
        context_id="ctx_inc_test",
        kind="scenario_a",
        title="t",
        component="payment",
        opened_at=OPENED,
        since=since,
    )


def test_window_starts_at_since() -> None:
    since = OPENED - timedelta(seconds=25)
    window = _window(_incident(since))
    assert window["window_from"] == "2026-10-28T07:23:36Z"
    assert window["window_to"] == "2026-10-28T07:39:01Z"


def test_window_without_since_falls_back_to_15_minutes() -> None:
    assert _window(_incident(None))["window_from"] == "2026-10-28T07:09:01Z"


def _env(data: dict) -> EventEnvelope:
    return EventEnvelope(
        event_id="evt_test",
        type=EventType.SHOP_DEGRADED,
        occurred_at=OPENED,
        context_id=None,
        source="shop-api",
        idempotency_key="k",
        data=data,
    )


def test_since_is_read_from_event() -> None:
    env = _env({"component": "payment", "since": "2026-10-28T07:23:36Z"})
    assert _since(env) == OPENED - timedelta(seconds=25)
    assert _since(_env({"component": "payment"})) is None
    assert _since(_env({"component": "payment", "since": "어제"})) is None
