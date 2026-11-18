"""영상 캠페인 필수 입력(다)과 종료 뒤 결과 자동 전달(가)."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from marketing_agent import campaign, campaign_auto, handlers
from marketing_agent.campaign import StartedCampaign
from marketing_agent.campaign_auto import AutoConfig, CampaignTracker

sys.path.insert(0, str(Path(__file__).parent))
from test_campaign import START, Finance, _data, _req, _wire  # noqa: E402

VIDEO = START | {
    "campaign_kind": "video",
    "content_category": "아재개그",
    "format": "shorts",
    "production_cost_krw": 0,
}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state()


# ---- (다) 필수 입력 ----


@pytest.mark.parametrize("drop", ["content_category", "format", "production_cost_krw"])
@pytest.mark.asyncio
async def test_video_start_without_required_field_is_refused(
    monkeypatch: pytest.MonkeyPatch, drop: str
) -> None:
    finance = Finance()
    gate = _wire(monkeypatch, finance)
    body = {k: v for k, v in VIDEO.items() if k != drop}
    result = await campaign.start_campaign(_req("start_campaign", body))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "video_fields_required")
    assert result.output["missing"] == [drop]
    assert drop in result.output["message"]
    assert finance.calls == [] and gate.seen == []


@pytest.mark.asyncio
async def test_video_url_alone_stays_compatible(monkeypatch: pytest.MonkeyPatch) -> None:
    """옛 호출(video_url만, campaign_kind 없음)은 영상 필수 검사를 받지 않는다."""
    _wire(monkeypatch, Finance())
    body = START | {"video_url": "https://youtube.com/shorts/x", "test": True}
    assert (await campaign.start_campaign(_req("start_campaign", body))).outcome == "executed"


@pytest.mark.asyncio
async def test_cancel_removes_from_tracker(monkeypatch: pytest.MonkeyPatch) -> None:
    handlers.STATE.campaigns.track(_started())
    out = {"campaign_id": "cmp_1", "status": "canceled", "released_krw": 5}
    monkeypatch.setattr(campaign, "call_agent", Finance(output=out, outcome="canceled"))
    req = _req("cancel_campaign", {"campaign_id": "cmp_1", "reason": "중단"})
    await handlers.HANDLERS["cancel_campaign"](req)
    assert handlers.STATE.campaigns.status("cmp_1") == "canceled"


@pytest.mark.asyncio
async def test_zero_production_cost_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, Finance())
    result = await campaign.start_campaign(_req("start_campaign", VIDEO))
    assert result.outcome == "executed"


@pytest.mark.asyncio
async def test_brief_fills_category_and_format_before_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _wire(monkeypatch, Finance())
    body = {k: v for k, v in VIDEO.items() if k not in ("content_category", "format")}
    result = await campaign.start_campaign(
        _req("start_campaign", body), brief_content=lambda _id: ("몸개그", "reels")
    )
    assert result.outcome == "executed"


@pytest.mark.asyncio
async def test_general_campaign_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, Finance())
    assert (await campaign.start_campaign(_req("start_campaign", START))).outcome == "executed"


@pytest.mark.asyncio
async def test_ends_on_in_past_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    body = START | {"ends_on": "2020-01-01"}
    result = await campaign.start_campaign(_req("start_campaign", body))
    assert result.outcome == "ends_on_in_past" and finance.calls == []


# ---- (가) 자동 전달 ----

NOW = datetime(2026, 11, 20, 0, 0, tzinfo=UTC)


def _started(**kw) -> StartedCampaign:
    base = {
        "campaign_id": "cmp_1",
        "campaign_name": "가을 세일",
        "channel": "sns",
        "budget_krw": 300_000,
        "content_category": "아재개그",
        "format": "shorts",
        "production_cost_krw": 40_000,
        "ends_on": "2026-11-18",
        "started_on": "2026-11-16",
    }
    return StartedCampaign(**(base | kw))


def _cfg(enabled: bool = True, **kw) -> AutoConfig:
    base = {"enabled": enabled, "poll_s": 1, "grace_h": 6, "retry_h": 1, "max_attempts": 3}
    return AutoConfig(**(base | kw))


class Recorded(Finance):
    def __init__(self, outcome: str = "recorded") -> None:
        super().__init__(output={"status": outcome, "profit_krw": 1}, outcome=outcome)


class _Routed:
    def __init__(self, finance: Finance) -> None:
        self.finance, self.data = finance, _data()

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        return await (self.data if target == "data" else self.finance)(target, req)


def _wire_auto(monkeypatch: pytest.MonkeyPatch, finance: Finance) -> None:
    monkeypatch.setattr(campaign, "call_agent", _Routed(finance))


def _sent(finance: Finance) -> list[dict]:
    return [r.input for _t, r in finance.calls if r.skill == "record_campaign_result"]


def _tracker() -> CampaignTracker:
    tracker = CampaignTracker(":memory:")
    tracker.track(_started(), grace_h=6)
    return tracker


@pytest.mark.asyncio
async def test_sends_after_end_with_started_values_and_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Recorded()
    _wire_auto(monkeypatch, finance)
    tracker = _tracker()
    assert await campaign_auto.auto_record_once(tracker, _cfg(), NOW) == 1
    sent = _sent(finance)[0]
    assert sent["spend_krw"] == 300_000 and sent["production_cost_krw"] == 40_000
    assert (sent["content_category"], sent["format"]) == ("아재개그", "shorts")
    assert tracker.status("cmp_1") == "recorded_auto"
    entry = tracker.log("cmp_1")[0]
    assert entry["result"] == "recorded_auto" and entry["detail"]["outcome"] == "recorded"


@pytest.mark.asyncio
async def test_not_sent_before_end_plus_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Recorded()
    _wire_auto(monkeypatch, finance)
    tracker = _tracker()
    # 종료일 11/18 -> 11/19 00:00 KST(= 11/18 15:00 UTC) + 6h = 11/18 21:00 UTC
    early = datetime(2026, 11, 18, 20, 59, tzinfo=UTC)
    assert await campaign_auto.auto_record_once(tracker, _cfg(), early) == 0
    assert finance.calls == []
    assert await campaign_auto.auto_record_once(tracker, _cfg(), early + timedelta(minutes=2)) == 1


@pytest.mark.asyncio
async def test_duplicate_send_is_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 캠페인은 한 번만 가고, 재무가 already_recorded로 답해도 성공으로 닫는다."""
    finance = Recorded()
    _wire_auto(monkeypatch, finance)
    tracker = _tracker()
    await campaign_auto.auto_record_once(tracker, _cfg(), NOW)
    await campaign_auto.auto_record_once(tracker, _cfg(), NOW)
    assert len(_sent(finance)) == 1
    again = Recorded("already_recorded")
    _wire_auto(monkeypatch, again)
    other = _tracker()
    await campaign_auto.auto_record_once(other, _cfg(), NOW)
    assert other.status("cmp_1") == "recorded_auto"


@pytest.mark.asyncio
async def test_disabled_sends_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Recorded()
    _wire_auto(monkeypatch, finance)
    tracker = _tracker()
    assert await campaign_auto.auto_record_once(tracker, _cfg(False), NOW) == 0
    assert finance.calls == [] and tracker.status("cmp_1") == "active"


def test_default_is_off_and_env_turns_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(campaign_auto.ENABLE_ENV, raising=False)
    assert AutoConfig.from_env().enabled is False
    monkeypatch.setenv(campaign_auto.ENABLE_ENV, "1")
    assert AutoConfig.from_env().enabled is True


@pytest.mark.asyncio
async def test_human_saved_or_canceled_is_not_overwritten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Recorded()
    _wire_auto(monkeypatch, finance)
    human = _tracker()
    human.mark_human_recorded("cmp_1")
    canceled = _tracker()
    canceled.mark_canceled("cmp_1")
    assert await campaign_auto.auto_record_once(human, _cfg(), NOW) == 0
    assert await campaign_auto.auto_record_once(canceled, _cfg(), NOW) == 0
    assert finance.calls == []


@pytest.mark.asyncio
async def test_retry_then_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Recorded("waiting_revenue")
    _wire_auto(monkeypatch, finance)
    tracker = _tracker()
    now = NOW
    for expected in ("active", "active", "gave_up"):
        await campaign_auto.auto_record_once(tracker, _cfg(), now)
        assert tracker.status("cmp_1") == expected
        now += timedelta(hours=2)
    assert [e["result"] for e in tracker.log()] == ["retry", "retry", "gave_up"]


@pytest.mark.asyncio
async def test_start_registers_and_human_save_marks(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, Finance())
    end = (campaign.kst_today() + timedelta(days=3)).isoformat()
    result = await handlers.start_campaign_skill(_req("start_campaign", VIDEO | {"ends_on": end}))
    assert result.outcome == "executed" and result.output["auto_record"] == {"tracked": True}
    got = handlers.STATE.campaigns.get("cmp_1")
    assert got and got.production_cost_krw == 0 and got.ends_on == end
    # 사람이 결과를 저장하면 시작 때 값으로 빈 칸을 채우고 자동 전달에서 빠진다
    fin = Recorded()
    _wire_auto(monkeypatch, fin)
    human = {"campaign_id": "cmp_1", "campaign_name": "n", "channel": "c", "spend_krw": 5}
    await handlers.record_campaign_result_skill(_req("record_campaign_result", human))
    sent = _sent(fin)[0]
    assert sent["production_cost_krw"] == 0 and sent["content_category"] == "아재개그"
    assert handlers.STATE.campaigns.status("cmp_1") == "recorded_human"
