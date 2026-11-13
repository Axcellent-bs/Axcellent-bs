"""영향 분석 구간: 신호가 장애 시작 시각(since)을 주면 그때부터 센다 (앞 장애와 섞이지 않게)."""

from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import _since
from ax_orchestrator.models import Incident, StepRun
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


def test_row_impact_window_matches_what_impact_analysis_gets() -> None:
    inc = _incident(OPENED - timedelta(seconds=25))
    inc.steps["impact"] = StepRun("impact", "impact_analysis", "영향 분석 (data)")
    view = inc.view()
    window = _window(inc)
    assert (view["impact_from"], view["impact_to"]) == (window["window_from"], window["window_to"])


def test_row_has_no_impact_window_without_the_impact_step() -> None:
    view = _incident(None).view()
    assert (view["impact_from"], view["impact_to"]) == (None, None)


def _verified(inc: Incident, at: datetime) -> None:
    run = StepRun("recovery", "verify_recovery", "복구 검증 (infra)", status="completed")
    run.finished_at = at
    inc.steps["recovery"] = run
    inc.outputs["recovery"] = {"recovered": True, "checked_at": at.isoformat()}


def test_window_ends_at_recovery_when_it_comes_before_the_default_end() -> None:
    inc = _incident(OPENED - timedelta(seconds=25))
    inc.recovered_at = OPENED + timedelta(seconds=30)
    _verified(inc, OPENED + timedelta(seconds=60))
    assert _window(inc)["window_to"] == "2026-10-28T07:24:31Z"  # 신호가 검증보다 먼저


def test_window_ends_at_the_verification_without_a_shop_signal() -> None:
    inc = _incident(OPENED - timedelta(seconds=25))
    _verified(inc, OPENED + timedelta(seconds=60))
    assert _window(inc)["window_to"] == "2026-10-28T07:25:01Z"


def test_an_unverified_shop_signal_does_not_end_the_window() -> None:
    """검증이 복구를 확인하지 않았으면 쇼핑몰 신호로 구간을 끊지 않는다(복구 시간과 같은 기준)."""
    inc = _incident(OPENED - timedelta(seconds=25))
    inc.recovered_at = OPENED + timedelta(seconds=30)
    assert _window(inc)["window_to"] == "2026-10-28T07:39:01Z"
    inc.status = "resolved"
    inc.settled_by_recovery = True  # 장애 중으로 닫힌 뒤 신호로 풀림: 신호 시각까지
    assert _window(inc)["window_to"] == "2026-10-28T07:24:31Z"


def test_window_ignores_a_recovery_outside_the_window() -> None:
    inc = _incident(OPENED - timedelta(seconds=25))
    inc.recovered_at = OPENED + timedelta(hours=1)  # 15분 뒤 끝보다 늦다
    assert _window(inc)["window_to"] == "2026-10-28T07:39:01Z"
    inc.recovered_at = OPENED - timedelta(minutes=5)  # 시작보다 이르다
    assert _window(inc)["window_to"] == "2026-10-28T07:39:01Z"
