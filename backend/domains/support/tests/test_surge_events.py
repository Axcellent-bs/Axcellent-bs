"""문의 급증 이벤트(`support.inquiry_surge`): 같은 분류가 몰리면 한 번만, 코드 값만 알린다."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template import events as bus
from ax_contracts.schemas import AgentRequest, EventType, RequestMeta
from ax_contracts.schemas.event import EVENT_DATA_MODELS, SupportInquirySurgeData
from support_agent import events, surge_events, surge_settings
from support_agent.schemas import Analysis, Classification, Triage
from support_agent.store import InquiryStore

NOW = datetime(2026, 11, 9, 9, 30, 20, tzinfo=UTC)
PRIVATE = "카드가 안 긁혀요 010-1234-5678"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in (
        surge_settings.ENV_ENABLED,
        surge_settings.ENV_MIN_COUNT,
        surge_settings.ENV_WINDOW,
    ):
        monkeypatch.delenv(name, raising=False)
    bus.reset_mock_bus()
    surge_events.reset()


def request() -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_req_0001",
            token="mock:dashboard-api",
            deadline=NOW + timedelta(seconds=30),
            idempotency_key="k",
        ),
        skill="create_inquiry",
    )


def analysis(category: str) -> Analysis:
    return Analysis(
        classification=Classification(
            intent_ko="결제",
            category=category,
            sentiment="calm",
            urgency="medium",
            search_query=category,
        )
    )


def add(store: InquiryStore, category: str, minutes_ago: float, triage: str | None = None) -> int:
    conversation = store.create(
        context_id="ctx_req_0001",
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject=PRIVATE,
        order_id=None,
        order_no=None,
        first_message=PRIVATE,
        analysis=analysis(category),
    )
    at = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conversation.messages[0].created_at = at
    conversation.updated_at = at
    if triage:
        conversation.triage = Triage(
            state="needed",
            reason=triage,
            marked_by="auto",
            marked_at=at,  # type: ignore[arg-type]
        )
    return conversation.id


def fire(store: InquiryStore, now: datetime = NOW) -> list[str]:
    return asyncio.run(surge_events.check_and_emit(request(), store, now))


def surges() -> list:
    return [e for e in bus.MOCK_PUBLISHED if e.type == EventType.SUPPORT_INQUIRY_SURGE]


def test_기준_미만이면_내보내지_않는다() -> None:
    store = InquiryStore()
    for k in range(4):
        add(store, "payment", k + 1)
    assert fire(store) == [] and surges() == []


def test_같은_분류가_기준_이상_몰리면_코드_값만_담아_한_번_내보낸다() -> None:
    store = InquiryStore()
    ids = [add(store, "payment", k + 1) for k in range(5)]
    add(store, "shipping", 2)
    assert fire(store) == ["payment"]
    (event,) = surges()
    data = SupportInquirySurgeData.model_validate(event.data)
    assert (data.topic_code, data.count, data.window_minutes) == ("payment", 5, 10)
    assert data.baseline_per_window == 0 and data.ratio is None
    assert set(data.inquiry_ids) <= set(ids)
    assert event.source == "support" and not event.untrusted
    assert "010-1234" not in event.model_dump_json() and "김하늘" not in event.model_dump_json()
    assert EVENT_DATA_MODELS[EventType.SUPPORT_INQUIRY_SURGE] is SupportInquirySurgeData


def test_창_안에서는_다시_내보내지_않고_창이_지나면_다시_알린다() -> None:
    store = InquiryStore()
    for k in range(5):
        add(store, "payment", k + 1)
    fire(store)
    add(store, "payment", 0.2)
    assert fire(store) == []  # 같은 창
    assert len(surges()) == 1
    later = NOW + timedelta(minutes=11)
    for k in range(5):
        add(store, "payment", -(k + 2))  # 새 창(NOW+2~6분)에 새 문의
    assert fire(store, later) == ["payment"]
    assert len(surges()) == 2


def test_같은_창_시작이면_같은_멱등_키다() -> None:
    start = NOW.replace(second=0, microsecond=0) - timedelta(minutes=10)
    assert events.inquiry_surge_key("payment", start) == (
        "support:support.inquiry_surge:payment:2026-11-09T09:20Z"
    )


def test_분류_필요_문의가_절반_이상이면_사유를_싣는다() -> None:
    store = InquiryStore()
    for k in range(4):
        add(store, "payment", k + 1, triage="no_error_code")
    add(store, "payment", 5)
    fire(store)
    assert SupportInquirySurgeData.model_validate(surges()[0].data).triage_reason == "no_error_code"


def test_분류_필요가_적거나_사유가_갈리면_사유를_비운다() -> None:
    store = InquiryStore()
    for k in range(2):
        add(store, "payment", k + 1, triage="no_error_code")
    for k in range(3):
        add(store, "payment", k + 3)
    fire(store)
    assert SupportInquirySurgeData.model_validate(surges()[0].data).triage_reason is None


def test_스위치와_기준은_한_곳에서_바꾼다(monkeypatch: pytest.MonkeyPatch) -> None:
    store = InquiryStore()
    for k in range(5):
        add(store, "payment", k + 1)
    monkeypatch.setenv(surge_settings.ENV_ENABLED, "off")
    assert fire(store) == []
    monkeypatch.delenv(surge_settings.ENV_ENABLED)
    monkeypatch.setenv(surge_settings.ENV_MIN_COUNT, "6")
    assert fire(store) == []
    monkeypatch.setenv(surge_settings.ENV_MIN_COUNT, "5")
    assert fire(store) == ["payment"]


@pytest.mark.parametrize(("raw", "value"), [("", 5), ("x", 5), ("0", 5), ("5000", 5), ("8", 8)])
def test_잘못된_기준값은_기본값(monkeypatch: pytest.MonkeyPatch, raw: str, value: int) -> None:
    monkeypatch.setenv(surge_settings.ENV_MIN_COUNT, raw)
    assert surge_settings.min_count() == value


def test_발행이_실패해도_문의_접수를_막지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(envelope):  # noqa: ANN001, ANN202
        raise RuntimeError("bus down")

    monkeypatch.setattr(events, "publish", broken)
    store = InquiryStore()
    for k in range(5):
        add(store, "payment", k + 1)
    assert fire(store) == ["payment"]  # 알리지 못해도 예외 없이 끝난다


def test_재시작해도_같은_구간이면_분이_바뀌어도_같은_멱등_키다() -> None:
    store = InquiryStore()
    for k in range(5):
        add(store, "payment", k + 1)
    assert fire(store) == ["payment"]
    surge_events.reset()  # 재시작: 메모리 기록이 지워진다
    assert fire(store, NOW + timedelta(minutes=3)) == ["payment"]  # 같은 10분 구간(09:30~09:40)
    assert len(surges()) == 1  # 같은 구간이라 같은 멱등 키 → 받는 쪽(버스)이 한 번만 받는다
