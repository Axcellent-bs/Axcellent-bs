"""실제 모드 진단은 결제 서버 이상이 확인돼야 원인으로 본다 (`confirm_with_server`).

배포 후보가 있어도 서버가 정상이거나 상태를 못 읽으면 `undetermined` → 총괄이 롤백하지 않고
사람에게 넘긴다 (고객 문의 급증만으로 열린 장애 등, #소통 2026-11-09 합의).
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import AgentRequest
from infra_agent import handlers
from infra_agent.clients import MockShop
from infra_agent.diagnosis import server_state


class BrokenShop(MockShop):
    async def fault_state(self) -> dict:
        raise ConnectionError("shop-api down")


@pytest.fixture
def real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handlers, "is_mock", lambda _name=None: False)


def _diagnose(a2a_body) -> dict:
    request = AgentRequest.model_validate(a2a_body("diagnose_incident", {"component": "payment"}))
    return asyncio.run(handlers.diagnose_incident(request)).model_dump(mode="json")


@pytest.mark.usefixtures("real_mode")
def test_server_fault_keeps_the_deploy_suspect(runtime, a2a_body) -> None:
    res = _diagnose(a2a_body)  # conftest: v2.4.1 배포 8분 뒤, 결제 서버 실패 중
    assert res["outcome"] == "suspected"
    [suspect] = res["output"]["suspects"]
    assert (suspect["version"], suspect["confidence"]) == ("v2.4.1", "high")
    assert runtime.diagnoses["ctx_inc_0001"].server == "fault"


@pytest.mark.usefixtures("real_mode")
@pytest.mark.parametrize(
    ("shop", "seen"),
    [(MockShop(), "정상"), (BrokenShop(), "확인할 수 없음")],
    ids=["server-ok", "server-unreadable"],
)
def test_without_server_fault_it_is_undetermined(runtime, a2a_body, shop, seen) -> None:
    runtime.shop = shop
    res = _diagnose(a2a_body)
    assert res["outcome"] == "undetermined"
    assert res["output"]["suspects"] == []
    # 배포 후보는 근거로 남는다
    assert res["evidence"]["ref_ids"] and seen in res["evidence"]["data_summary"]
    assert runtime.diagnoses["ctx_inc_0001"].output.status == "undetermined"


def test_mock_mode_does_not_check_the_server(runtime, a2a_body) -> None:
    runtime.shop = MockShop()  # 서버 정상이어도 목 모드는 SCN-A 체크리스트대로 high 후보
    assert _diagnose(a2a_body)["outcome"] == "suspected"
    assert runtime.diagnoses["ctx_inc_0001"].server is None


def test_server_state_reduces_the_fault_switch() -> None:
    assert server_state({"failing": True}) == "fault"
    assert server_state({"degraded": True, "failing": False}) == "fault"
    assert server_state({"active": False, "failing": False, "degraded": False}) == "ok"
    assert server_state(None) == "unknown"
