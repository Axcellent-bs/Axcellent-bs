"""유튜브 영상 ↔ 캠페인 연결, 재무 결과의 유튜브 조회수, 자동 전송 광고비 고르기 (11/23).

- 유튜브 칸(제작 기록·의뢰 영상)에서 고른 캠페인이 render.youtube.campaign_id(의뢰 영상은 그 상태)에
  남고 `youtube_uploads_list(campaign_id=…)`가 찾는다. 올린 뒤에도 바꿀 수 있고 ""면 푼다
- 마케팅이 모르는 캠페인은 거절(unknown_campaign)
- `record_campaign_result`가 연결된 영상의 조회수 합을 `youtube_views`로 따로 싣는다(가짜 구글).
  못 읽거나 한 영상이라도 모르면 비운다. 데이터에 등록된 같은 유튜브 영상은 두 번 세지 않는다
- 자동 전송: 기간 광고 숫자 합계가 있고 빈 날짜가 없으면 그 값 + `marketing.auto.ad_metrics`,
  아니면 승인 예산 + `marketing.auto.approved_budget`
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.ad_metrics import RecordAdMetricsInput
from marketing_agent import ad_metrics, campaign, campaign_auto, handlers, sns
from marketing_agent import creative_records as cr
from marketing_agent import request_video_youtube as rvy
from marketing_agent import youtube as yt
from marketing_agent import youtube_stats as ys
from marketing_agent.campaign import StartedCampaign

sys.path.insert(0, str(Path(__file__).parent))
from test_campaign import CALLER, Finance, VideoData, _checkpoint, _req, _video  # noqa: E402
from test_request_video_youtube import _draft as _rv_draft  # noqa: E402
from test_request_video_youtube import _with_labeled_video  # noqa: E402

VID_A, VID_B, VID_GONE = "vidAAAAAAAA", "vidBBBBBBBB", "vidGONEGONE"
VIEWS = {VID_A: "1500", VID_B: "700"}


class Google:
    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.stats_calls: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == yt.TOKEN_URL:
            return httpx.Response(200, json={"access_token": "ya29.DUMMY"})
        if url.startswith(ys.STATS_URL):
            ids = dict(request.url.params)["id"]
            self.stats_calls.append(ids)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"message": "quota"}})
            items = [
                {"id": v, "statistics": {"viewCount": VIEWS[v]}}
                for v in ids.split(",")
                if v in VIEWS
            ]
            return httpx.Response(200, json={"items": items})
        return httpx.Response(404)


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(media))
    for key in yt.KEYS:
        monkeypatch.setenv(key, f"DUMMY-{key}")
    monkeypatch.delenv("AX_SECRET_STORE", raising=False)
    handlers.reset_state()
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    sns_store = sns.SnsStore(str(tmp_path / "sns.db"))
    monkeypatch.setattr(handlers.STATE, "sns", sns_store)
    google = Google()
    monkeypatch.setattr(
        yt, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google.handle))
    )
    ys.clear_cache()
    handlers.STATE.campaigns.track(_started())
    yield {"store": store, "sns": sns_store, "google": google, "media": media.resolve()}
    ys.clear_cache()
    cr.set_store(None)


def _started(**kw: Any) -> StartedCampaign:
    base = {
        "campaign_id": "cmp_1",
        "campaign_name": "가을 세일",
        "channel": "유튜브",
        "budget_krw": 300_000,
        "content_category": "아재개그",
        "format": "shorts",
        "production_cost_krw": 40_000,
        "ends_on": "2026-11-18",
        "started_on": "2026-11-16",
    }
    return StartedCampaign(**(base | kw))


def _record(store: cr.CreativeRecords, video_id: str, **youtube: Any) -> str:
    rid = store.add_sheet({"summary_ko": "흰 슈트", "prompt_en": "p", "negative_prompt_en": "n"},
                          None, None)  # fmt: skip
    url = f"https://youtu.be/{video_id}"
    store.set_render(rid, {"youtube": {"status": "done", "video_id": video_id, "url": url,
                                       **youtube}})  # fmt: skip
    return rid


def _call(skill: str, body: dict[str, Any]) -> Any:
    meta = RequestMeta(context_id="ctx_test_link", token="t", requester="dashboard-api",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return asyncio.run(handlers.HANDLERS[skill](AgentRequest(meta=meta, skill=skill, input=body)))


def _linked(campaign_id: str) -> list[str]:
    out = _call("youtube_uploads_list", {"campaign_id": campaign_id}).output
    return [i["video_id"] for i in out["items"]] + [i["video_id"] for i in out["request_items"]]


# ---------------------------------------------------------------- 캠페인 고르기·목록 필터
def test_record_campaign_is_saved_after_upload_and_found_by_the_list(env: dict[str, Any]) -> None:
    rid = _record(env["store"], VID_A, title="올린 영상")
    got = _call("creative_youtube_draft", {"record_id": rid, "campaign_id": "cmp_1"})
    assert got.status == TaskStatus.COMPLETED
    youtube = got.output["youtube"]
    # 올린 뒤(done)에도 연결만 바꾸고, 올린 값은 그대로다
    assert (youtube["campaign_id"], youtube["status"]) == ("cmp_1", "done")
    assert youtube["title"] == "올린 영상"
    assert _linked("cmp_1") == [VID_A]
    items = _call("youtube_uploads_list", {}).output["items"]
    assert items[0]["campaign_id"] == "cmp_1"
    # ""면 연결을 푼다
    _call("creative_youtube_draft", {"record_id": rid, "campaign_id": ""})
    assert env["store"].get_render(rid)["youtube"]["campaign_id"] is None
    assert _linked("cmp_1") == []


def test_campaign_choice_alongside_draft_edits_keeps_both(env: dict[str, Any]) -> None:
    rid = env["store"].add_sheet({"summary_ko": "x", "prompt_en": "p", "negative_prompt_en": "n"},
                                 None, None)  # fmt: skip
    got = _call("creative_youtube_draft", {"record_id": rid, "campaign_id": "cmp_1",
                                           "title": "새 제목", "description": "설명"})  # fmt: skip
    youtube = got.output["youtube"]
    assert (youtube["campaign_id"], youtube["status"]) == ("cmp_1", "draft")
    assert youtube["title"] == "새 제목"


def test_unknown_campaign_is_refused_and_nothing_changes(env: dict[str, Any]) -> None:
    rid = _record(env["store"], VID_A)
    got = _call("creative_youtube_draft", {"record_id": rid, "campaign_id": "cmp_nobody"})
    assert (got.status, got.outcome) == (TaskStatus.FAILED, "unknown_campaign")
    assert "campaign_id" not in env["store"].get_render(rid)["youtube"]
    bad = _call("creative_youtube_draft", {"record_id": rid, "campaign_id": "wr_abc"})
    assert bad.outcome == "invalid_input"  # 캠페인 번호(cmp_)만 고를 수 있다


def test_request_video_campaign_is_saved_and_filtered(env: dict[str, Any]) -> None:
    draft = _rv_draft()
    env["sns"].save(draft)
    _with_labeled_video({"media": env["media"], "sns": env["sns"]}, draft)
    got = _call("request_video_youtube_draft", {"post_draft_id": draft.post_draft_id,
                                                "campaign_id": "cmp_1"})  # fmt: skip
    assert got.status == TaskStatus.COMPLETED
    assert rvy.get(draft.post_draft_id)["campaign_id"] == "cmp_1"
    assert "title" not in rvy.get(draft.post_draft_id)  # 연결만 했고 초안은 만들지 않았다
    # 올리기가 끝난 상태에서도 바꿀 수 있고, 의뢰 번호·캠페인 번호 둘 다로 찾는다
    state = rvy.get(draft.post_draft_id) | {"status": "done", "video_id": VID_B}
    env["store"].set_request_youtube(draft.post_draft_id, "wr_abc123", state)
    assert _linked("cmp_1") == [VID_B] and _linked("wr_abc123") == [VID_B]
    assert _call("youtube_uploads_list", {}).output["request_items"][0]["campaign_id"] == "cmp_1"
    _call("request_video_youtube_draft", {"post_draft_id": draft.post_draft_id,
                                          "campaign_id": ""})  # fmt: skip
    assert _linked("cmp_1") == [] and _linked("wr_abc123") == [VID_B]


def test_list_marketing_campaigns_returns_the_started_values(env: dict[str, Any]) -> None:
    handlers.STATE.campaigns.mark_canceled("cmp_none")  # 없는 캠페인은 그대로
    out = _call("list_marketing_campaigns", {}).output
    assert out["total"] == 1
    item = out["items"][0]
    assert (item["campaign_id"], item["status"], item["content_category"]) == (
        "cmp_1",
        "active",
        "아재개그",
    )
    assert (item["format"], item["production_cost_krw"], item["ends_on"]) == (
        "shorts",
        40_000,
        "2026-11-18",
    )


# ---------------------------------------------------------------- 재무 결과의 유튜브 조회수
def _finance() -> Finance:
    return Finance(output={"status": "recorded"}, outcome="recorded")


async def _record_result(monkeypatch: pytest.MonkeyPatch, video: Finance, asked: dict) -> dict:
    finance = _finance()
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, video))
    result = await campaign.record_campaign_result(_req("record_campaign_result", asked))
    assert result.outcome == "recorded"  # 유튜브 조회수 때문에 결과 전달이 막히지 않는다
    return finance.calls[0][1].input


@pytest.mark.asyncio
async def test_result_carries_youtube_views_of_linked_videos(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(env["store"], VID_A, campaign_id="cmp_1")
    _record(env["store"], VID_B, campaign_id="cmp_1")
    _record(env["store"], "vidOTHERxxx", campaign_id="cmp_other")
    sent = await _record_result(monkeypatch, _video([_checkpoint("7d", 20_000)]), CALLER)
    assert sent["views"] == 20_000 and sent["views_point"] == "7d"  # 영상 성과는 그대로 따로
    assert sent["youtube_views"] == 2_200
    assert set(sent["youtube_video_ids"]) == {VID_A, VID_B}
    assert sent["youtube_views_at"]


@pytest.mark.asyncio
async def test_registered_youtube_video_is_not_counted_twice(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(env["store"], VID_A, campaign_id="cmp_1")
    _record(env["store"], VID_B, campaign_id="cmp_1")
    video = _video([_checkpoint("7d", 20_000)], platform="youtube", video_id=VID_A)
    sent = await _record_result(monkeypatch, video, CALLER)
    assert sent["views"] == 20_000
    assert (sent["youtube_views"], sent["youtube_video_ids"]) == (700, [VID_B])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_link", "google_down", "one_gone", "not_connected"])
async def test_youtube_views_left_blank_when_unreadable(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    if case != "no_link":
        _record(env["store"], VID_A, campaign_id="cmp_1")
    if case == "one_gone":
        _record(env["store"], VID_GONE, campaign_id="cmp_1")
    if case == "google_down":
        env["google"].status = 500
    if case == "not_connected":
        monkeypatch.delenv(yt.KEYS[2])
    sent = await _record_result(monkeypatch, _video([_checkpoint("7d", 20_000)]), CALLER)
    assert sent["views"] == 20_000
    assert not {"youtube_views", "youtube_views_at", "youtube_video_ids"} & set(sent)


@pytest.mark.asyncio
async def test_caller_youtube_views_win_and_google_is_not_called(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(env["store"], VID_A, campaign_id="cmp_1")
    sent = await _record_result(monkeypatch, _video([]), CALLER | {"youtube_views": 5})
    assert sent["youtube_views"] == 5 and env["google"].stats_calls == []


@pytest.mark.asyncio
async def test_auto_send_also_carries_youtube_views(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _record(env["store"], VID_A, campaign_id="cmp_1")
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, _video([])))
    tracker = campaign_auto.CampaignTracker(":memory:")
    tracker.track(_started(), grace_h=6)
    cfg = campaign_auto.AutoConfig(enabled=True, poll_s=1, grace_h=6, retry_h=1, max_attempts=3)
    now = datetime(2026, 11, 20, tzinfo=UTC)
    assert await campaign_auto.auto_record_once(tracker, cfg, now) == 1
    sent = [r.input for _t, r in finance.calls if r.skill == "record_campaign_result"][0]
    assert sent["youtube_views"] == 1_500 and sent["youtube_video_ids"] == [VID_A]


# ---------------------------------------------------------------- 자동 전송 광고비 고르기
def _ad(store: ad_metrics.AdMetricsStore, day: str, spend: int | None, cid: str = "cmp_1") -> None:
    store.record(RecordAdMetricsInput(campaign_id=cid, date=day, source="google", medium="cpc",
                                      spend_krw=spend, entered_by="human"))  # fmt: skip


async def _auto_sent(monkeypatch: pytest.MonkeyPatch, store: ad_metrics.AdMetricsStore) -> dict:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, _video([])))
    tracker = campaign_auto.CampaignTracker(":memory:")
    tracker.track(_started(), grace_h=6)
    cfg = campaign_auto.AutoConfig(enabled=True, poll_s=1, grace_h=6, retry_h=1, max_attempts=3)
    await campaign_auto.auto_record_once(
        tracker,
        cfg,
        datetime(2026, 11, 20, tzinfo=UTC),
        ad_spend=lambda item: campaign_auto.ad_metrics_spend(store, item),
    )
    sent = [r.input for _t, r in finance.calls if r.skill == "record_campaign_result"][0]
    log = tracker.log("cmp_1")[0]["detail"]["sent"]
    assert (log["spend_krw"], log["source"]) == (sent["spend_krw"], sent["source"])
    return sent


@pytest.mark.asyncio
async def test_auto_spend_is_the_ad_metrics_total_inside_the_period(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ad_metrics.AdMetricsStore(":memory:")
    _ad(store, "2026-11-16", 50_000)
    _ad(store, "2026-11-17", 0)  # 0원은 0
    _ad(store, "2026-11-18", 70_000)
    _ad(store, "2026-11-19", 999_999)  # 기간 밖
    _ad(store, "2026-11-17", 5_000, cid="cmp_other")  # 다른 캠페인
    sent = await _auto_sent(monkeypatch, store)
    assert (sent["spend_krw"], sent["source"]) == (120_000, campaign_auto.AD_METRICS_SOURCE)


@pytest.mark.asyncio
async def test_auto_spend_falls_back_to_budget_when_a_day_is_unknown(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ad_metrics.AdMetricsStore(":memory:")
    _ad(store, "2026-11-16", 50_000)
    _ad(store, "2026-11-17", None)  # 광고비 모르는 날
    sent = await _auto_sent(monkeypatch, store)
    assert (sent["spend_krw"], sent["source"]) == (300_000, campaign_auto.AUTO_SOURCE)


@pytest.mark.asyncio
async def test_auto_spend_falls_back_to_budget_without_rows_or_store(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent = await _auto_sent(monkeypatch, ad_metrics.AdMetricsStore(":memory:"))
    assert (sent["spend_krw"], sent["source"]) == (300_000, campaign_auto.AUTO_SOURCE)

    class Broken:
        def query(self, ask: Any) -> list:
            raise ad_metrics.AdMetricsStorageError("disk")

    assert campaign_auto.ad_metrics_spend(Broken(), _started()) is None


def test_human_save_still_wins_over_the_auto_send(env: dict[str, Any]) -> None:
    """사람이 결과를 저장한 캠페인은 광고 숫자가 있어도 자동으로 보내지 않는다(규칙 그대로)."""
    store = ad_metrics.AdMetricsStore(":memory:")
    _ad(store, "2026-11-16", 50_000)
    tracker = campaign_auto.CampaignTracker(":memory:")
    tracker.track(_started(), grace_h=6)
    tracker.mark_human_recorded("cmp_1")
    assert tracker.due(datetime(2026, 11, 20, tzinfo=UTC)) == []
