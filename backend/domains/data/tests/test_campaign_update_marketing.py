"""성과 결과를 기획에 보낼 때 광고 숫자·유튜브 성과를 같이 담는다 (11/23, `marketing_reads`).

실제 마케팅·기획·safety는 부르지 않는다(가짜 call_agent).

- 의뢰 영상(wr_) 갈래·캠페인(cmp_) 갈래 둘 다, 같은 7일 시점·같은 플래그
- 광고 숫자: `get_campaign_ad_metrics`(의뢰는 cmp_ + 제안 번호). None은 "모름", 0은 0
- 유튜브 성과: 올린 목록(그 번호에 연결된 것) + 등록된 유튜브 영상 → `youtube_video_stats`
- 못 읽으면 "못 읽음: 사유"(숫자를 지어내지 않음), 그래도 기획에는 보낸다
- 기존 근거 줄·summary·direction은 그대로, "한계" 줄은 마지막
"""

from __future__ import annotations

import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.data import (
    CampaignResultOutput,
    CampaignSalesEffect,
    CampaignSalesVerdict,
    VideoCheckpoint,
    VideoPerformanceOutput,
)
from ax_contracts.schemas.execute import ExecuteDecision
from data_agent import campaign_update as cu
from data_agent import marketing_reads as mr
from data_agent import request_result as rr

_spec = importlib.util.spec_from_file_location(
    "request_video_result_fakes", Path(__file__).with_name("test_request_video_result.py")
)
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

YT = "ytVIDEO0001"
AD_OK = {"status": "ok", "rows": [], "combos": {},
         "totals": {"impressions": 12000, "clicks": 340, "spend_krw": None,
                    "missing_dates": {"spend_krw": ["2026-11-18"]}}}  # fmt: skip
STATS_OK = {"status": "ok", "checked_at": "2026-11-23T03:00:00+00:00", "items": [
    {"video_id": YT, "status": "ok", "views": 1500, "likes": 30, "comments": None,
     "url": f"https://youtu.be/{YT}"}]}  # fmt: skip


class FakeMarketing:
    """마케팅 read skill 셋. `fail`에 든 skill은 연결 실패로 답한다."""

    def __init__(self, ad: dict | None = None, uploads: list[dict] | None = None,
                 stats: dict | None = None, fail: tuple[str, ...] = ()) -> None:  # fmt: skip
        self.out = {
            "get_campaign_ad_metrics": AD_OK if ad is None else ad,
            "youtube_uploads_list": {"items": [{"record_id": "CR-0123456789ab", "video_id": YT,
                                                "campaign_id": "wr_abc0000001"}]
                                     if uploads is None else uploads, "total": 1},
            "youtube_video_stats": STATS_OK if stats is None else stats,
        }  # fmt: skip
        self.fail = fail
        self.asked: list[AgentRequest] = []

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "marketing"
        self.asked.append(request)
        if request.skill in self.fail:
            return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome="unreachable")
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED,
                           output=self.out[request.skill])  # fmt: skip

    def skills(self) -> list[str]:
        return [r.skill for r in self.asked]


def _request_week(tmp_path: Path, marketing: FakeMarketing, flag: bool = True) -> Any:
    """의뢰 영상 등록 → 7일 시점을 4,500회로 잼 → tick(read=가짜 마케팅)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    env = rv._env(tmp_path, flag=flag)  # noqa: SLF001
    graph = rv.FakeGraph()
    rv._run(env, graph, rv.PUB + timedelta(minutes=10))  # noqa: SLF001
    graph.now = rv.PUB + timedelta(hours=168.5)
    graph.targets = ("TGT1",)
    store = rr.vp.store_of(env)
    try:
        asyncio.run(rv.ig.measure_due(store, rv._Fixed(graph, 4500), env, graph.now))  # noqa: SLF001
    finally:
        store.close()
    planning = rv.FakePlanning()
    calls = asyncio.run(cu.tick(env, graph.now, send=planning, gate=rv._executed,  # noqa: SLF001
                                read=marketing))  # fmt: skip
    return planning, calls


def _facts(planning: Any) -> dict[str, dict]:
    return {f["label"]: f for f in planning.got[0].input["facts"]}


def test_request_result_carries_ad_numbers_and_youtube_stats(tmp_path: Path) -> None:
    marketing = FakeMarketing()
    planning, calls = _request_week(tmp_path, marketing)
    assert calls == 1 and len(planning.got) == 1
    body = planning.got[0].input
    # 기존 값은 그대로
    assert body["direction"] == "늘었음"
    assert body["summary"] == "의뢰 영상 7일 조회 4,500회 · 우리 릴스 중앙값의 1.50배"
    assert body["facts"][-1]["label"] == "한계"
    facts = _facts(planning)
    assert facts[mr.AD_LABEL]["value"] == "노출 12,000 · 클릭 340 · 광고비 모름 (빈 날짜 있음)"
    assert facts[mr.AD_LABEL]["source"] == mr.AD_SOURCE
    yt = facts[mr.YOUTUBE_LABEL]
    assert yt["value"] == "조회 1,500회 · 좋아요 30 · 댓글 모름"
    assert (yt["source"], yt["link"]) == (mr.YOUTUBE_SOURCE, f"https://youtu.be/{YT}")
    assert facts["7일 조회·좋아요·댓글"]["value"] == "4,500회 · 좋아요 50 · 댓글 7"
    # 마케팅 read 셋을 data로 부르고, 의뢰는 cmp_ + 제안 번호로 광고 숫자를 찾는다
    assert marketing.skills() == ["get_campaign_ad_metrics", "youtube_uploads_list",
                                  "youtube_video_stats"]  # fmt: skip
    ad, uploads, stats = marketing.asked
    assert ad.input == {"campaign_id": "cmp_PROP-7"}
    assert uploads.input == {"campaign_id": "wr_abc0000001"}
    assert stats.input == {"video_ids": [YT]}
    assert all(r.meta.requester == "data" for r in marketing.asked)
    assert all(r.meta.context_id == planning.got[0].meta.context_id for r in marketing.asked)


def test_no_ad_input_is_unknown_and_zero_stays_zero(tmp_path: Path) -> None:
    unknown = FakeMarketing(ad={"status": "unknown", "reason": "아직 입력 없음"})
    planning, _ = _request_week(tmp_path / "a", unknown)
    assert _facts(planning)[mr.AD_LABEL]["value"] == "모름 (아직 입력 없음)"
    zero = FakeMarketing(ad={"status": "ok", "totals": {"impressions": 0, "clicks": 0,
                                                        "spend_krw": 0}})  # fmt: skip
    planning, _ = _request_week(tmp_path / "b", zero)
    assert _facts(planning)[mr.AD_LABEL]["value"] == "노출 0 · 클릭 0 · 광고비 0원"


def test_reads_that_fail_say_why_and_the_result_is_still_sent(tmp_path: Path) -> None:
    marketing = FakeMarketing(fail=("get_campaign_ad_metrics", "youtube_uploads_list"))
    planning, calls = _request_week(tmp_path, marketing)
    assert calls == 1
    facts = _facts(planning)
    assert facts[mr.AD_LABEL]["value"] == "못 읽음: 마케팅이 답하지 않음 (unreachable)"
    assert facts[mr.YOUTUBE_LABEL]["value"] == "못 읽음: 마케팅이 답하지 않음 (unreachable)"
    assert "youtube_video_stats" not in marketing.skills()  # 읽을 영상이 없어 부르지 않음


@pytest.mark.parametrize(("uploads", "stats", "want"), [
    ([], None, "못 읽음: 연결된 유튜브 영상 없음"),
    (None, {"status": "unknown", "reason": "missing_keys", "checked_at": "x"},
     "못 읽음: 유튜브 채널 연결 안 됨"),
    (None, {"status": "ok", "checked_at": "x",
            "items": [{"video_id": YT, "status": "not_found"}]},
     "못 읽음: 영상을 찾지 못함 (지웠거나 비공개)"),
])  # fmt: skip
def test_youtube_unknown_reasons(tmp_path: Path, uploads: list | None, stats: dict | None,
                                 want: str) -> None:  # fmt: skip
    planning, _ = _request_week(tmp_path, FakeMarketing(uploads=uploads, stats=stats))
    yt = _facts(planning)[mr.YOUTUBE_LABEL]
    assert yt["value"] == want and yt["link"] is None


def test_flag_off_reads_nothing(tmp_path: Path) -> None:
    marketing = FakeMarketing()
    planning, calls = _request_week(tmp_path, marketing, flag=False)
    assert calls == 0 and planning.got == [] and marketing.asked == []


# ---------------------------------------------------------------- 캠페인(cmp_) 갈래

PUB = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
DONE = PUB + timedelta(days=7, hours=1)


def _campaign(cells: int = 0) -> CampaignResultOutput:
    video = VideoPerformanceOutput(
        video_id=YT,
        platform="youtube",
        campaign_id="cmp_1",
        verdict="achieved",
        checkpoints=[VideoCheckpoint(checkpoint="7d", status="known", target_ratio=1.4)],
    )
    sales = CampaignSalesEffect(status="known", verdict=CampaignSalesVerdict(
        status="known", verdict="more", label="더 팔림", incremental_gross_krw=1_200_000,
        incremental_orders=40))  # fmt: skip
    return CampaignResultOutput(status="known", campaign_id="cmp_1", campaign_name="책추천",
                                insight_id="ins_1", proposal_id="PROP-3", window_from=PUB,
                                window_to=PUB + timedelta(days=7), videos=[video],
                                sales=sales)  # fmt: skip


async def _executed(action, params, meta, evidence):  # noqa: ANN001, ANN202
    return ExecuteDecision(execution_id="exe_test", grade="A", status="executed")


def test_campaign_result_carries_ad_numbers_and_youtube_stats(tmp_path: Path) -> None:
    marketing = FakeMarketing(uploads=[])
    planning = rv.FakePlanning()
    store = cu.SendStore(tmp_path / "sends.db")
    try:
        calls = asyncio.run(cu.send_ready([_campaign()], store, {cu.FLAG_ENV: "1"}, DONE,
                                          send=planning, gate=_executed,
                                          read=marketing))  # fmt: skip
    finally:
        store.close()
    assert calls == 1
    body = planning.got[0].input
    assert body["direction"] == "늘었음"
    labels = [f["label"] for f in body["facts"]]
    assert labels[0] == "조회 판정" and labels[-2:] == [mr.AD_LABEL, mr.YOUTUBE_LABEL]
    assert len(body["facts"]) <= 12
    ad, _uploads, stats = marketing.asked
    assert ad.input == {"campaign_id": "cmp_1"}
    assert stats.input == {"video_ids": [YT]}  # 등록된 유튜브 영상을 읽는다


def test_facts_stay_within_twelve_and_keep_the_two_new_lines() -> None:
    extras = [
        mr.InsightFact(label=mr.AD_LABEL, value="모름", source=mr.AD_SOURCE),
        mr.InsightFact(label=mr.YOUTUBE_LABEL, value="모름", source=mr.YOUTUBE_SOURCE),
    ]
    facts = cu.facts_of(_campaign(), extras)
    assert facts[-2:] == extras and len(facts) <= 12
    crowded = cu.facts_of(_campaign(), extras * 5)  # 자리가 모자라면 앞 줄을 줄인다
    assert len(crowded) == 12 and crowded[-10:] == extras * 5
    assert crowded[0].label == "조회 판정"
    assert mr.ad_campaign_of("wr_abc0000001", None) is None  # 제안 번호 없으면 모름
    assert mr.ad_campaign_of("wr_abc0000001", "PROP-7") == "cmp_PROP-7"


def test_request_video_uploaded_from_records_tab_counts(tmp_path: Path) -> None:
    """의뢰 영상을 제작 기록의 유튜브 칸으로 올렸으면(request_items) 그 영상 성과도 담는다."""
    marketing = FakeMarketing(uploads=[])
    marketing.out["youtube_uploads_list"]["request_items"] = [
        {"post_draft_id": "pd_req_1", "request_id": "wr_abc0000001", "video_id": YT}
    ]
    planning, _ = _request_week(tmp_path, marketing)
    yt = _facts(planning)[mr.YOUTUBE_LABEL]
    assert yt["value"] == "조회 1,500회 · 좋아요 30 · 댓글 모름"
    assert marketing.asked[-1].input == {"video_ids": [YT]}
