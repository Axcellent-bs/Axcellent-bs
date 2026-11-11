"""알림 받는 주소: 고객사 판매 서버가 회사 키로 이벤트를 보내면 확인 뒤 이벤트 버스에 넣는다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_agent_template.testing import reset_state  # noqa: F401  (autouse 픽스처)
from ax_dashboard_api import shop_events
from ax_dashboard_api.main import app

URL = "/api/dashboard/events"
KEY = "ck_catbooks_unit_only"
AUTH = {"authorization": f"Bearer {KEY}"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv(shop_events.KEYS_ENV, f"shop-demo:{KEY}, other-co:ck_other_unit_only")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _envelope(**extra) -> dict:
    return {
        "event_id": "evt_0123456789ab",
        "type": "shop.degraded",
        "occurred_at": "2026-11-11T05:00:00Z",
        "tenant_id": "shop-demo",
        "context_id": None,
        "source": "shop-api",
        "idempotency_key": "shop.degraded:payment:1",
        "data": {
            "component": "payment",
            "metric_code": "error_rate_5xx",
            "value": 0.15,
            "threshold": 0.02,
        },
        **extra,
    }


def test_a_shop_event_with_the_company_key_goes_onto_the_bus(client) -> None:
    res = client.post(URL, json=_envelope(), headers=AUTH)
    assert res.status_code == 202, res.text
    assert res.json()["data"] == {"event_id": "evt_0123456789ab", "accepted": True}
    [sent] = events.MOCK_PUBLISHED
    assert sent.type.value == "shop.degraded"
    assert sent.tenant_id == "shop-demo"
    assert sent.source == "shop-api"


def test_the_same_event_twice_is_put_on_the_bus_once(client) -> None:
    for _ in range(2):
        assert client.post(URL, json=_envelope(), headers=AUTH).status_code == 202
    assert len(events.MOCK_PUBLISHED) == 1


def test_only_shop_server_event_kinds_are_allowed() -> None:
    """SHOP-05의 판매 서버 이벤트(1차·2차)만 받는다."""
    assert {t.value for t in shop_events.ALLOWED_TYPES} == {
        "order.created",
        "order.cancelled",
        "order.delivered",
        "payment.succeeded",
        "payment.failed",
        "payment.refunded",
        "shipment.delayed",
        "return.received",
        "seller.registered",
        "product.created",
        "shop.degraded",
        "shop.recovered",
    }


def test_recovered_is_accepted_too(client) -> None:
    body = _envelope(type="shop.recovered", event_id="evt_recovered001")
    assert client.post(URL, json=body, headers=AUTH).status_code == 202
    assert [e.type.value for e in events.MOCK_PUBLISHED] == ["shop.recovered"]


def test_no_key_is_401(client) -> None:
    # conftest의 TestClient는 사람 토큰을 기본으로 붙이므로 헤더를 비워 보낸다
    res = client.post(URL, json=_envelope(), headers={"authorization": ""})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "AUTH_REQUIRED"
    assert events.MOCK_PUBLISHED == []


def test_an_unknown_key_is_403(client) -> None:
    res = client.post(URL, json=_envelope(), headers={"authorization": "Bearer ck_wrong"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "COMPANY_KEY_INVALID"
    assert events.MOCK_PUBLISHED == []


def test_a_human_token_is_not_a_company_key(client) -> None:
    res = client.post(URL, json=_envelope(), headers={"authorization": "Bearer test-human-token"})
    assert res.status_code == 403
    assert events.MOCK_PUBLISHED == []


def test_an_agent_token_is_not_a_company_key(client) -> None:
    res = client.post(URL, json=_envelope(), headers={"authorization": "Bearer mock:shop-api"})
    assert res.status_code == 403
    assert events.MOCK_PUBLISHED == []


def test_without_configured_keys_everything_is_refused(client, monkeypatch) -> None:
    monkeypatch.delenv(shop_events.KEYS_ENV)
    res = client.post(URL, json=_envelope(), headers=AUTH)
    assert res.status_code == 403
    assert events.MOCK_PUBLISHED == []


def test_a_key_cannot_send_for_another_company(client) -> None:
    res = client.post(URL, json=_envelope(tenant_id="other-co"), headers=AUTH)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "TENANT_MISMATCH"
    assert events.MOCK_PUBLISHED == []


@pytest.mark.parametrize(
    "event_type", ["incident.opened", "safety.action_executed", "metric.anomaly"]
)
def test_a_customer_cannot_put_internal_events(client, event_type) -> None:
    res = client.post(URL, json=_envelope(type=event_type), headers=AUTH)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "EVENT_TYPE_NOT_ALLOWED"
    assert events.MOCK_PUBLISHED == []


def test_only_the_shop_server_can_be_the_source(client) -> None:
    res = client.post(URL, json=_envelope(source="orchestrator"), headers=AUTH)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "EVENT_SOURCE_NOT_ALLOWED"
    assert events.MOCK_PUBLISHED == []


def test_a_customer_cannot_pick_the_incident_number(client) -> None:
    res = client.post(URL, json=_envelope(context_id="ctx_inc_x1"), headers=AUTH)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "CONTEXT_ID_NOT_ALLOWED"
    assert events.MOCK_PUBLISHED == []


def test_data_must_match_the_event_kind(client) -> None:
    res = client.post(URL, json=_envelope(data={"component": "printer"}), headers=AUTH)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "EVENT_DATA_INVALID"
    assert events.MOCK_PUBLISHED == []


def test_a_bus_failure_is_503_so_the_shop_server_retries(client, monkeypatch) -> None:
    async def broken(_envelope) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr(shop_events.events, "publish", broken)
    res = client.post(URL, json=_envelope(), headers=AUTH)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "EVENT_BUS_UNAVAILABLE"


def test_human_tokens_are_not_needed_for_this_write(client, monkeypatch) -> None:
    """사람 토큰 확인(require_human_for_writes)을 거치지 않고 회사 키만 본다."""
    monkeypatch.delenv("DASHBOARD_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    assert client.post(URL, json=_envelope(), headers=AUTH).status_code == 202
