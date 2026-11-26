"""교대 인수인계 타이머를 회사마다 (회사 분리 11/26).

요청 밖에서 도는 타이머가 캣북스 조직도만 봐서 누비투어는 교대 인수인계가 자동으로 시작되지 않았다.
이제 회차마다 회사(캣북스 + 회사 연결이 켜진 회사)를 돌며 그 회사 저장소·조직도로 시작한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from ax_agent_template import companies, company_connection, company_scope
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from hr_agent.handover import service as handover_service
from hr_agent.roster import timer
from hr_agent.roster.seed import seed_roster

NUBI = "c-aaaaaa"


@dataclass
class _Conn:
    enabled: bool = True


@pytest.fixture
def nubi_connected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(companies, "company_ids", lambda: ["catbooks", NUBI])
    monkeypatch.setattr(
        company_connection, "read_connection", lambda cid=None: _Conn() if cid == NUBI else None
    )


def _handovers(svc, company: str | None) -> list[dict]:  # noqa: ANN001
    with company_scope.using(company):
        return svc.store.all("SELECT owner_id, area, context_id FROM handovers")


@pytest.mark.asyncio
async def test_timer_starts_handovers_per_company(svc, clock, nubi_connected, monkeypatch) -> None:  # noqa: ANN001
    with company_scope.using(NUBI):
        seed_roster(svc.store, clock)  # 누비투어 조직도(그 회사 저장소에만)
        svc.store.execute("DELETE FROM shifts WHERE area != 'payment'")  # 캣북스와 모양이 다르게
    calls: list[tuple[str | None, str]] = []
    original = handover_service.start_handover

    async def spy(s, request):  # noqa: ANN001, ANN202
        calls.append((company_scope.active(), request.meta.tenant_id))
        return await original(s, request)

    monkeypatch.setattr(handover_service, "start_handover", spy)
    started = await timer.start_due_handovers_all(horizon_minutes=780)

    assert set(started) == {"catbooks", NUBI}
    cat, nubi = _handovers(svc, None), _handovers(svc, NUBI)
    assert len(cat) == len(started["catbooks"]) and len(cat) > len(nubi) > 0
    assert {r["area"] for r in nubi} == {"payment"}  # 누비투어는 자기 교대만
    # 캣북스는 예전처럼(요청 회사 없음·예전 meta 값), 누비투어는 그 회사 범위·그 회사 id
    assert (None, TENANT_ID_DEFAULT) in calls and (NUBI, NUBI) in calls
    assert all(tenant == NUBI for active, tenant in calls if active == NUBI)


@pytest.mark.asyncio
async def test_catbooks_only_install_runs_as_before(svc, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(companies, "company_ids", lambda: ["catbooks"])
    started = await timer.start_due_handovers_all(horizon_minutes=780)
    alone = await timer.start_due_handovers(svc, horizon_minutes=780)
    assert list(started) == ["catbooks"]
    assert [r["handover_id"] for r in started["catbooks"]] == [r["handover_id"] for r in alone]
