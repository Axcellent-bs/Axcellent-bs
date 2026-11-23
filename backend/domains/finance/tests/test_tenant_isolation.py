"""재무 기록은 회사마다 따로 (여러 회사 5단계 = 요청마다 회사로 저장소 고르기, 11/23).

A 회사 요청은 B 회사 기록을 보지 못한다(없는 것처럼). 요청 밖(화면·루틴)은 캣북스 기록 그대로.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from ax_contracts.schemas import EventEnvelope, EventType
from finance_agent import handlers, persist
from finance_agent.main import app
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_STATE_FILE", raising=False)
    monkeypatch.delenv("AX_COMPANIES_DIR", raising=False)
    handlers.reset_state(MockSources())


def _ask(client: TestClient, tenant: str | None, category: str = "상황극") -> dict[str, Any]:
    meta: dict[str, Any] = {
        "context_id": "ctx_iso_0001",
        "token": "mock:planning",
        "path": [],
        "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
    }
    if tenant is not None:
        meta["tenant_id"] = tenant
    body = {
        "meta": meta,
        "skill": "category_budget_advice",
        "input": {"content_category": category},
    }
    res = client.post("/a2a", json=body)
    assert res.status_code == 200
    return res.json()


def _asks(company: str | None) -> list[dict[str, Any]]:
    if company is None:
        return handlers.state().records.budget_asks
    with company_scope.using(company):
        return handlers.state().records.budget_asks


def test_a2a_write_lands_only_in_the_request_company() -> None:
    client = TestClient(app)
    assert _ask(client, "nubitour")["status"] == "completed"
    assert len(_asks("nubitour")) == 1
    assert _asks("studyon") == []
    assert _asks(None) == [] and _asks("catbooks") == []  # 요청 밖 = 캣북스 기록


def test_alias_and_missing_tenant_use_the_catbooks_records() -> None:
    client = TestClient(app)
    _ask(client, "shop-demo")
    _ask(client, None)
    assert len(_asks(None)) == 2
    assert _asks("nubitour") == []


def test_company_a_cannot_see_company_b_campaigns() -> None:
    with company_scope.using("studyon"):
        handlers.state().records.campaigns["cmp_b"] = {"campaign_id": "cmp_b", "spend_krw": 1}
    with company_scope.using("nubitour"):
        assert "cmp_b" not in handlers.state().records.campaigns
    assert "cmp_b" not in handlers.state().records.campaigns  # 캣북스도 못 본다
    with company_scope.using("studyon"):
        assert "cmp_b" in handlers.state().records.campaigns


def test_events_go_to_the_envelope_company_ledger() -> None:
    import asyncio

    envelope = EventEnvelope.model_validate(
        {
            "event_id": "evt_iso_1",
            "idempotency_key": "evt_iso_1",
            "type": EventType.PAYMENT_REFUNDED.value,
            "source": "shop-api",
            "tenant_id": "nubitour",
            "occurred_at": datetime.now(UTC).isoformat(),
            "data": {"order_id": "ord_1", "refund_amount": 5000},
        }
    )
    asyncio.run(handlers.on_event(envelope))
    with company_scope.using("nubitour"):
        assert handlers.state().ledger.refunds == {"ord_1": 5000}
    assert handlers.state().ledger.refunds == {}


def test_company_records_are_saved_and_loaded_per_company(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINANCE_STATE_FILE", str(tmp_path / "finance_state.json"))
    with company_scope.using("nubitour"):
        handlers.state().records.recon_notes["pay_1"] = "누비투어 메모"
    persist.save_companies({})
    saved = tmp_path / "finance_state.nubitour.json"
    assert saved.exists() and not (tmp_path / "finance_state.json").exists()

    handlers.reset_state(MockSources())  # 다시 켠 것처럼
    with company_scope.using("nubitour"):
        assert handlers.state().records.recon_notes == {"pay_1": "누비투어 메모"}
    with company_scope.using("studyon"):
        assert handlers.state().records.recon_notes == {}
    assert handlers.state().records.recon_notes == {}
