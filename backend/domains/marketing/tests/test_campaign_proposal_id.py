"""받은 기획안으로 시작한 캠페인 번호(`cmp_<proposal_id>`)와 같은 번호 다시 시작 금지 (11/23).

- 기획안 번호로 시작하면 자동 전달 대장·광고 숫자가 같은 번호라, 자동 전송이 광고 숫자 합계를 쓴다
- 대장에 이미 있는 번호는 상태와 상관없이(진행 중·취소 모두) `already_started`로 거절하고
  재무·관문을 부르지 않는다
- 대장을 못 읽으면 `campaign_ledger_unavailable`로 멈춘다(fail-closed)
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.ad_metrics import RecordAdMetricsInput
from marketing_agent import ad_metrics, campaign, campaign_auto, handlers
from marketing_agent.briefs import campaign_id_for

sys.path.insert(0, str(Path(__file__).parent))
from test_campaign import Gate, _req, _review, _video  # noqa: E402

pytestmark = pytest.mark.asyncio
PROPOSAL = "prop_20261123_a"
CID = campaign_id_for(PROPOSAL)


class Departments:
    """`call_agent` 대역: 재무 예산 의견·결과 기록, 데이터 영상 성과를 skill로 나눠 답한다."""

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.calls.append(req)
        if req.skill == "review_campaign_budget":
            out, outcome = _review() | {"campaign_id": req.input["campaign_id"]}, "approved"
        elif req.skill == "record_campaign_result":
            out, outcome = {"status": "recorded"}, "recorded"
        elif req.skill == "video_performance":
            return await _video([])(target, req)
        else:
            out = {"campaign_id": req.input.get("campaign_id"), "finance_record": None}
            outcome = "completed"
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, outcome=outcome, output=out)

    def skills(self) -> list[str]:
        return [r.skill for r in self.calls]


def _start(**kw: Any) -> dict[str, Any]:
    today = campaign.kst_today().isoformat()
    return {
        "campaign_id": CID,
        "campaign_name": "기획안 캠페인",
        "channel": "유튜브",
        "budget_krw": 300_000,
        "ends_on": today,
    } | kw


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[Departments, Gate]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state()
    depts, gate = Departments(), Gate()
    monkeypatch.setattr(campaign, "call_agent", depts)
    monkeypatch.setattr(campaign, "execute", gate)
    return depts, gate


async def test_campaign_id_from_a_proposal_is_cmp_plus_the_proposal_id() -> None:
    assert CID == f"cmp_{PROPOSAL}"


async def test_starting_the_same_number_again_is_refused_before_finance_and_gate(
    wired: tuple[Departments, Gate],
) -> None:
    depts, gate = wired
    first = await handlers.start_campaign_skill(_req("start_campaign", _start()))
    assert first.outcome == "executed"
    assert handlers.STATE.campaigns.status(CID) == campaign_auto.ACTIVE
    asked_before = len(depts.calls)

    again = await handlers.start_campaign_skill(_req("start_campaign", _start(budget_krw=100_000)))
    assert (again.status, again.outcome) == (TaskStatus.FAILED, "already_started")
    assert again.output["campaign_id"] == CID and again.output["status"] == "active"
    assert len(depts.calls) == asked_before and len(gate.seen) == 1
    # 앞 캠페인의 대장 값은 그대로다
    assert handlers.STATE.campaigns.get(CID).budget_krw == 300_000


async def test_a_canceled_number_is_not_started_again_either(
    wired: tuple[Departments, Gate],
) -> None:
    _depts, gate = wired
    assert (await handlers.start_campaign_skill(_req("start_campaign", _start()))).outcome == (
        "executed"
    )
    handlers.STATE.campaigns.mark_canceled(CID)
    again = await handlers.start_campaign_skill(_req("start_campaign", _start()))
    assert again.outcome == "already_started" and again.output["status"] == campaign_auto.CANCELED
    assert len(gate.seen) == 1


async def test_another_proposal_or_a_timestamp_number_still_starts(
    wired: tuple[Departments, Gate],
) -> None:
    _depts, gate = wired
    assert (await handlers.start_campaign_skill(_req("start_campaign", _start()))).outcome == (
        "executed"
    )
    for other in (campaign_id_for("prop_other"), "cmp_1763856000000"):
        asked = _req("start_campaign", _start(campaign_id=other))
        result = await handlers.start_campaign_skill(asked)
        assert result.outcome == "executed", other
    assert len(gate.seen) == 3


async def test_unreadable_ledger_stops_the_start(wired: tuple[Departments, Gate]) -> None:
    depts, gate = wired

    def broken(_cid: str) -> str | None:
        raise OSError("disk")

    result = await campaign.start_campaign(_req("start_campaign", _start()), started_status=broken)
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "campaign_ledger_unavailable")
    assert depts.calls == [] and gate.seen == []


async def test_auto_result_uses_the_ad_metrics_total_of_the_proposal_campaign(
    wired: tuple[Departments, Gate],
) -> None:
    depts, _gate = wired
    assert (await handlers.start_campaign_skill(_req("start_campaign", _start()))).outcome == (
        "executed"
    )
    started = handlers.STATE.campaigns.get(CID)
    store = ad_metrics.AdMetricsStore(":memory:")
    # 광고 숫자 칸은 받은 기획안 번호(cmp_<proposal_id>)로 적는다
    row = RecordAdMetricsInput(
        campaign_id=CID,
        date=started.started_on,
        source="google",
        medium="cpc",
        spend_krw=87_000,
        entered_by="human",
    )
    store.record(row)
    cfg = campaign_auto.AutoConfig(enabled=True, poll_s=1, grace_h=6, retry_h=1, max_attempts=3)
    # 기본 유예 6시간을 넉넉히 지난 뒤
    now = campaign_auto.ends_at(started.ends_on) + timedelta(hours=30)
    sent_count = await campaign_auto.auto_record_once(
        handlers.STATE.campaigns,
        cfg,
        now,
        ad_spend=lambda item: campaign_auto.ad_metrics_spend(store, item),
    )
    assert sent_count == 1
    sent = [r.input for r in depts.calls if r.skill == "record_campaign_result"][0]
    assert sent["campaign_id"] == CID
    assert (sent["spend_krw"], sent["source"]) == (87_000, campaign_auto.AD_METRICS_SOURCE)
