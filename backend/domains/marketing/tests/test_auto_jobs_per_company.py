"""기획안 자동 넘김·캠페인 결과 자동 전달을 회사마다 (회사 분리 11/26).

요청 밖에서 도는 두 루프가 캣북스 저장소만 봐서 누비투어 기획안은 자동으로 넘겨지지 않았고
캠페인 결과도 자동으로 기록되지 않았다. 이제 회차마다 회사(캣북스 + 회사 연결이 켜진 회사)를 돌며
그 회사 칸만 보고, 다른 에이전트를 부르는 요청에는 그 회사 id를 싣는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from ax_agent_template import companies, company_connection, company_scope
from ax_contracts.schemas import AgentRequest, AgentResult
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import brief_auto_handoff, campaign_auto, handlers
from marketing_agent.campaign_auto import StartedCampaign

NUBI = "c-aaaaaa"
NOW = datetime(2026, 11, 19, 3, 0, tzinfo=UTC)


@dataclass
class _Conn:
    enabled: bool = True


class _Stop(Exception):  # noqa: N818 - 루프를 한 회차에서 멈추는 표시
    pass


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state()
    monkeypatch.setattr(companies, "company_ids", lambda: ["catbooks", NUBI])
    monkeypatch.setattr(
        company_connection, "read_connection", lambda cid=None: _Conn() if cid == NUBI else None
    )


def _add(pid: str) -> None:
    brief = CampaignBriefInput(
        schema_version="1",
        proposal_id=pid,
        title="릴스",
        idea="릴스로 알린다",
        why="검색이 늘었다",
        facts=[],
        target=None,
        products=[],
        approved_by="tester",
    )
    handlers.STATE.briefs.add(brief, NOW)


@pytest.mark.asyncio
async def test_brief_handoff_runs_per_company_with_that_company_id(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv(brief_auto_handoff.ENABLE_ENV, "1")
    _add("P_CAT")
    with company_scope.using(NUBI):
        _add("P_NUBI")
    seen: list[tuple[str | None, str, str]] = []

    async def handoff(request: AgentRequest) -> AgentResult:
        seen.append((company_scope.active(), request.meta.tenant_id, request.input["proposal_id"]))
        raise RuntimeError("넘기지 않고 기록만")  # 한 건 실패는 다음 건을 막지 않는다

    async def stop(_seconds: float) -> None:
        raise _Stop

    monkeypatch.setattr(brief_auto_handoff.RT, "sleep", stop)
    with pytest.raises(_Stop):
        await brief_auto_handoff.auto_handoff_loop(lambda: handlers.STATE.briefs, handoff)

    # 캣북스는 예전처럼(요청 회사 없음·예전 meta 값) 자기 기획안만, 누비투어는 그 회사 기획안만
    # 캣북스 차례도 캣북스 범위(11/27), meta 값은 예전 그대로
    assert seen == [("catbooks", TENANT_ID_DEFAULT, "P_CAT"), (NUBI, NUBI, "P_NUBI")]
    assert handlers.STATE.briefs.auto_attempts("P_CAT")[0] == 1
    assert handlers.STATE.briefs.auto_attempts("P_NUBI")[0] == 0  # 캣북스 칸에는 없다
    with company_scope.using(NUBI):
        assert handlers.STATE.briefs.auto_attempts("P_NUBI")[0] == 1


@pytest.mark.asyncio
async def test_campaign_auto_record_runs_per_company(monkeypatch) -> None:  # noqa: ANN001
    seen: list[str | None] = []

    async def once(tracker, brief_content=None, ad_spend=None) -> int:  # noqa: ANN001
        seen.append(company_scope.active())
        return 0

    calls = 0

    async def sleep(_seconds: float) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise _Stop

    monkeypatch.setattr(campaign_auto, "auto_record_once", once)
    monkeypatch.setattr(campaign_auto.RT, "sleep", sleep)
    with pytest.raises(_Stop):
        await campaign_auto.auto_record_loop(handlers.STATE.campaigns)
    assert seen == ["catbooks", NUBI]


def test_campaign_auto_request_carries_the_company() -> None:
    item = StartedCampaign(
        campaign_id="cmp_1", campaign_name="가을", channel="sns", budget_krw=1000,
        ends_on="2026-11-18", started_on="2026-11-16",
    )  # fmt: skip
    assert campaign_auto._request(item, NOW).meta.tenant_id == TENANT_ID_DEFAULT  # noqa: SLF001
    with company_scope.using(NUBI):
        assert campaign_auto._request(item, NOW).meta.tenant_id == NUBI  # noqa: SLF001
