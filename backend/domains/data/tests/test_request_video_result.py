"""의뢰 영상(wr_) 인스타 성과 → 7일 뒤 기획에 결과 (11/23). 실제 인스타·기획·safety는 부르지 않는다.

- business_discovery 응답(가짜)에서 shortcode로 게시물을 찾아 시점 값을 저장
- publish_username 없음 · 50개 안에서 못 찾음 · 프로페셔널 계정 아님 → unknown + 이유
- 비교 기준: 우리 릴스(이 영상 빼고, 최대 20개) 중앙값 대비 배수, 3개 미만이면 low_sample
- 같은 계정 영상 둘은 한 번 읽기로 같이 잰다
- campaign_update 의뢰 갈래: direction 네 경우·idempotency 키·플래그 꺼짐·캠페인 경로와 섞이지 않음
- 다음 인사이트 "지난번 결과"(past_of)
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.data import VideoPerformanceRequest
from ax_contracts.schemas.execute import ExecuteDecision
from data_agent import campaign_update as cu
from data_agent import request_result as rr
from data_agent.connectors.instagram_graph import IgError
from data_agent.skills import instagram_video as ig
from data_agent.skills import video_performance as vp

PUB = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
URL = "https://www.instagram.com/reel/TGT1/"
URL2 = "https://www.instagram.com/reel/TGT2/"


def _reel(code: str, views: int | None, *, kind: str = "REELS", at: datetime = PUB) -> dict:
    return {"id": f"m_{code}", "media_product_type": kind,
            "permalink": f"https://www.instagram.com/reel/{code}/",
            "timestamp": at.strftime("%Y-%m-%dT%H:%M:%S+0000"),
            "view_count": views, "like_count": 50, "comments_count": 7}  # fmt: skip


def _others(n: int, base: int = 1000) -> list[dict]:
    return [_reel(f"R{i}", base * (i + 1), at=PUB - timedelta(days=i + 1)) for i in range(n)]


class FakeGraph:
    """business_media만. 부른 계정을 센다. views는 게시 후 시간 → 조회 곡선."""

    configured = True

    def __init__(self, others: list[dict] | None = None, error: str | None = None,
                 targets: tuple[str, ...] = ("TGT1",)) -> None:  # fmt: skip
        self.others = _others(5) if others is None else others
        self.error = error
        self.targets = targets
        self.asked: list[tuple[str, int]] = []
        self.now = PUB

    async def business_media(self, username: str, limit: int = 20) -> list[dict]:
        self.asked.append((username, limit))
        if self.error:
            raise IgError(self.error, "가짜 오류")
        hours = (self.now - PUB).total_seconds() / 3600
        mine = [_reel(t, int(1000 * hours)) for t in self.targets]
        return [*mine, *self.others]


def _env(tmp_path: Path, username: str | None = "ourshop", *, flag: bool = True) -> dict[str, str]:
    toml = tmp_path / "tenant.toml"
    toml.write_text('name = "가게"\n' + (f'[instagram]\npublish_username = "@{username}"\n'
                                         if username else ""), encoding="utf-8")  # fmt: skip
    env = {vp.DB_ENV: str(tmp_path / "perf.db"), cu.DB_ENV: str(tmp_path / "sends.db"),
           "DATA_INSIGHT_BRIEF_DB": str(tmp_path / "brief.db"),
           "DATA_YOUTUBE_DB": str(tmp_path / "youtube.db"),
           "DATA_SHORTS_DB": str(tmp_path / "shorts.db"),
           ig.TENANT_CONFIG_ENV: str(toml), ig.TOKEN_ENV: "t", ig.USER_ENV: "1"}  # fmt: skip
    if flag:
        env[cu.FLAG_ENV] = "1"
    return env


def _run(env: dict[str, str], graph: FakeGraph, at: datetime, url: str = URL, **req: Any):  # noqa: ANN202
    graph.now = at
    request = VideoPerformanceRequest(**({"video_url": url, "campaign_id": "wr_abc0000001",
                                          "proposal_id": "PROP-7"} | req))  # fmt: skip
    return asyncio.run(vp.video_performance(request, None, env=env, now=at,
                                            instagram=graph)).output  # fmt: skip


def _measure(env: dict[str, str], graph: FakeGraph, at: datetime) -> int:
    graph.now = at
    store = vp.store_of(env)
    try:
        return asyncio.run(ig.measure_due(store, graph, env, at))[0]
    finally:
        store.close()


# ---------------------------------------------------------------- 인스타 측정


def test_finds_the_post_by_shortcode_and_saves_the_checkpoint(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph()  # 다른 릴스 5개: 1000·2000·3000·4000·5000 → 중앙값 3000
    out = _run(env, graph, PUB + timedelta(hours=1, minutes=30))
    assert graph.asked == [("ourshop", 50)]  # 계정 1번, 최근 50개
    assert out.platform == "instagram" and out.tracking and out.status == "known"
    assert out.published_at == PUB  # 게시물 timestamp
    h1 = next(c for c in out.checkpoints if c.checkpoint == "1h")
    assert (h1.views, h1.likes, h1.comments) == (1500, 50, 7)
    assert h1.peer_ratio == 0.5 and out.peer_median_views == 3000.0
    assert h1.line == "1시간 · 1,500회 · 좋아요 50 · 우리 릴스 대비 0.50배"
    assert "@ourshop 최근 릴스 5개" in (out.peer_basis or "")
    sources = {f.source for f in out.facts if "배수" not in f.label}
    assert sources == {ig.SOURCE}
    assert rr.SALES_LIMIT in out.limits
    later = next(c for c in out.checkpoints if c.checkpoint == "1d")
    assert later.status == "unknown" and later.reason.startswith(vp.MEASURING)


def test_one_day_is_saved_at_its_own_checkpoint_and_window(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph()
    _run(env, graph, PUB + timedelta(hours=1, minutes=5))
    assert _measure(env, graph, PUB + timedelta(hours=10)) == 0  # 잴 시점이 아님 → 부르지 않음
    assert len(graph.asked) == 1
    assert _measure(env, graph, PUB + timedelta(hours=25)) == 1
    out = _run(env, graph, PUB + timedelta(hours=25, minutes=10))
    d1 = next(c for c in out.checkpoints if c.checkpoint == "1d")
    assert d1.views == 25000 and d1.hours_after == 25.0


def test_no_publish_username_says_why_without_calling(tmp_path: Path) -> None:
    env = _env(tmp_path, username=None)
    graph = FakeGraph()
    out = _run(env, graph, PUB + timedelta(hours=1, minutes=30))
    assert graph.asked == [] and out.status == "unknown"
    assert out.reason == "회사 설정에 게시 계정 아이디가 없음 ([instagram] publish_username)"
    assert all(c.reason == ig.NO_USERNAME for c in out.checkpoints)


def test_post_not_in_the_last_50_says_why(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph(targets=())
    out = _run(env, graph, PUB + timedelta(hours=1, minutes=30))
    assert out.status == "unknown" and out.reason == "게시물을 최근 50개에서 찾지 못함"


def test_not_found_is_not_read_again_within_the_hour(tmp_path: Path) -> None:
    """화면이 1분마다 읽어도 못 찾은 영상 때문에 인스타를 다시 부르지 않는다 (다음 수집 회차에)."""
    env = _env(tmp_path)
    graph = FakeGraph(targets=())
    _run(env, graph, PUB + timedelta(minutes=10))
    _run(env, graph, PUB + timedelta(minutes=11), campaign_id=None)
    assert len(graph.asked) == 1
    graph.targets = ("TGT1",)
    assert _measure(env, graph, PUB + timedelta(hours=1, minutes=10)) == 1  # 한 시간 뒤 다시 → 찾음
    assert len(graph.asked) == 2


def test_personal_account_says_not_professional(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph(error="NOT_FOUND")
    out = _run(env, graph, PUB + timedelta(hours=1, minutes=30))
    assert out.status == "unknown" and out.reason == "프로페셔널 계정이 아님"


def test_without_the_data_token_it_still_waits_for_a_link(tmp_path: Path) -> None:
    env = _env(tmp_path)
    env.pop(ig.TOKEN_ENV)
    out = _run(env, FakeGraph(), PUB)
    assert out.reason.startswith("연결 대기") and not out.tracking


def test_baseline_median_skips_this_video_non_reels_and_keeps_20() -> None:
    media = [_reel("TGT1", 999_999), _reel("P1", 50_000, kind="FEED"), _reel("X", None),
             *_others(25)]  # fmt: skip
    median, n = ig.baseline_of(media, "TGT1")
    assert n == 20 and median == 10500.0  # 1000..20000 중앙값
    assert ig.baseline_of(_others(2), "TGT1") == (None, 2)


def test_under_three_reels_is_low_sample_without_a_ratio(tmp_path: Path) -> None:
    env = _env(tmp_path)
    out = _run(env, FakeGraph(others=_others(2)), PUB + timedelta(hours=1, minutes=30))
    h1 = next(c for c in out.checkpoints if c.checkpoint == "1h")
    assert h1.views == 1500 and h1.peer_ratio is None
    assert (out.peer_basis or "").startswith("표본 적음(low_sample)")


def test_two_videos_on_one_account_are_read_once(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph(targets=("TGT1", "TGT2"))
    _run(env, graph, PUB + timedelta(minutes=10))  # 등록: 게시 시각만 채움 (잴 시점 아님)
    _run(env, graph, PUB + timedelta(minutes=20), url=URL2, campaign_id="wr_abc0000002")
    graph.asked.clear()
    assert _measure(env, graph, PUB + timedelta(hours=1, minutes=10)) == 2
    assert graph.asked == [("ourshop", 50)]


def test_collector_measures_instagram_once_per_tick(tmp_path: Path) -> None:
    from data_agent import youtube_collect

    env = _env(tmp_path)
    graph = FakeGraph()
    _run(env, graph, PUB + timedelta(minutes=10))
    graph.asked.clear()
    graph.now = PUB + timedelta(hours=1, minutes=10)
    saved = asyncio.run(youtube_collect.measure_instagram_videos(env, graph.now, graph))
    assert saved == 1 and len(graph.asked) == 1


# ---------------------------------------------------------------- 7일 뒤 기획에 결과


def test_direction_thresholds_are_fixed() -> None:
    assert (rr.UP, rr.DOWN) == (1.5, 0.67)
    assert rr.direction_of(4500, 3000.0) == "늘었음"  # 1.5배
    assert rr.direction_of(2010, 3000.0) == "줄었음"  # 0.67배
    assert rr.direction_of(3000, 3000.0) == "같음"
    assert rr.direction_of(None, 3000.0) == "확인 불가"  # 못 잼
    assert rr.direction_of(3000, None) == "확인 불가"  # low_sample


async def _executed(action, params, meta, evidence):  # noqa: ANN001, ANN202
    return ExecuteDecision(execution_id="exe_test", grade="A", status="executed")


class FakePlanning:
    def __init__(self) -> None:
        self.got: list[AgentRequest] = []

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "planning"
        self.got.append(request)
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, outcome="applied",
                           output={"outcome": "applied"})  # fmt: skip


def _week(tmp_path: Path, seven_views: int | None, others: list[dict] | None = None,
          flag: bool = True) -> tuple[dict[str, str], FakePlanning, int]:  # fmt: skip
    """등록 → (7일 조회가 seven_views가 되게) 7일 시점 측정 → tick 한 번."""
    env = _env(tmp_path, flag=flag)
    graph = FakeGraph(others=others)
    _run(env, graph, PUB + timedelta(minutes=10))
    hours = 168.5
    if seven_views is not None:
        graph.now = PUB + timedelta(hours=hours)
        graph.targets = ("TGT1",)
        store = vp.store_of(env)
        try:
            asyncio.run(ig.measure_due(store, _Fixed(graph, seven_views), env, graph.now))
        finally:
            store.close()
    planning = FakePlanning()
    at = PUB + timedelta(hours=hours if seven_views is not None else 173)
    calls = asyncio.run(cu.tick(env, at, send=planning, gate=_executed))
    return env, planning, calls


class _Fixed:
    """7일 조회를 정한 값으로 주는 가짜 (기준 릴스는 FakeGraph 그대로)."""

    configured = True

    def __init__(self, graph: FakeGraph, views: int) -> None:
        self.graph, self.views = graph, views

    async def business_media(self, username: str, limit: int = 20) -> list[dict]:
        return [_reel("TGT1", self.views), *self.graph.others]


@pytest.mark.parametrize(("views", "others", "direction"), [
    (4500, None, "늘었음"),
    (2000, None, "줄었음"),
    (3000, None, "같음"),
    (3000, _others(2), "확인 불가"),  # low_sample
    (None, None, "확인 불가"),  # 7일을 놓침
])  # fmt: skip
def test_request_video_result_goes_to_planning_after_seven_days(
    tmp_path: Path, views: int | None, others: list[dict] | None, direction: str
) -> None:
    _env_, planning, calls = _week(tmp_path, views, others)
    assert calls == 1 and len(planning.got) == 1
    sent = planning.got[0]
    assert sent.skill == "receive_campaign_update"
    assert sent.meta.idempotency_key == "wr_abc0000001:result"
    body = sent.input
    assert body["campaign_id"] == "wr_abc0000001" and body["proposal_id"] == "PROP-7"
    assert body["stage"] == "result" and body["direction"] == direction
    if views is not None and others is None:
        want = f"의뢰 영상 7일 조회 {views:,}회 · 우리 릴스 중앙값의 {views / 3000:.2f}배"
        assert body["summary"] == want
    labels = [f["label"] for f in body["facts"]]
    assert labels[-1] == "한계"
    assert body["facts"][-1]["value"] == "매출은 프로모션 링크가 없어 측정 불가"
    assert all(f["source"] == rr.SOURCE for f in body["facts"][:-1])
    assert not any("매출" in f["label"] for f in body["facts"])


def test_seven_day_facts_have_views_likes_comments_and_ratio(tmp_path: Path) -> None:
    _env_, planning, _calls = _week(tmp_path, 4500)
    facts = {f["label"]: f["value"] for f in planning.got[0].input["facts"]}
    assert facts["7일 조회·좋아요·댓글"] == "4,500회 · 좋아요 50 · 댓글 7"
    assert facts["7일 우리 릴스 대비 배수"] == "1.50배 (중앙값 3,000회, 릴스 5개)"


def test_not_sent_before_seven_days_and_sent_only_once(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph()
    _run(env, graph, PUB + timedelta(minutes=10))
    planning = FakePlanning()
    assert asyncio.run(cu.tick(env, PUB + timedelta(days=3), send=planning, gate=_executed)) == 0
    late = PUB + timedelta(hours=173)
    assert asyncio.run(cu.tick(env, late, send=planning, gate=_executed)) == 1
    assert asyncio.run(cu.tick(env, late, send=planning, gate=_executed)) == 0  # 다시 안 보냄


def test_flag_off_sends_nothing(tmp_path: Path) -> None:
    _env_, planning, calls = _week(tmp_path, 4500, flag=False)
    assert calls == 0 and planning.got == []


def test_request_videos_do_not_go_through_campaign_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from data_agent.skills import campaign_result as cr

    async def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("의뢰 영상은 campaign_result로 계산하지 않는다")

    monkeypatch.setattr(cr, "campaign_result", boom)
    _env_, planning, calls = _week(tmp_path, 4500)
    assert calls == 1


def test_test_videos_and_missing_proposal_are_not_sent(tmp_path: Path) -> None:
    env = _env(tmp_path)
    graph = FakeGraph(targets=("TGT1", "TGT2"))
    _run(env, graph, PUB + timedelta(minutes=10), test=True)
    _run(env, graph, PUB + timedelta(minutes=10), url=URL2, campaign_id="wr_abc0000002",
         proposal_id=None)  # fmt: skip
    planning = FakePlanning()
    assert asyncio.run(cu.tick(env, PUB + timedelta(hours=173), send=planning,
                               gate=_executed)) == 0  # fmt: skip


def test_sent_results_become_next_insight_past(tmp_path: Path) -> None:
    env, _planning, _calls = _week(tmp_path, 4500)
    past = rr.past_of(env)
    assert len(past) == 1
    p = past[0]
    assert p.campaign_id == "wr_abc0000001" and p.views == 4500
    assert p.views_label == "조회 늘었음" and p.sales_label == rr.SALES_LIMIT
    assert p.summary_line.startswith("의뢰 영상 7일 조회 4,500회")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rr.past_of(_env(empty)) == []  # 보낸 기록 파일이 없으면 빈 목록 (만들지 않음)
    assert not (empty / "sends.db").exists()


def test_trend_report_insight_carries_the_past_request_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """총괄 홍보 영상 흐름 ②(trend_report_insight → 기획 receive_insight)의 past에 들어간다."""
    import importlib.util

    from ax_contracts.schemas import RequestMeta
    from data_agent import handlers

    spec = importlib.util.spec_from_file_location(
        "trend_report_fakes_rv", Path(__file__).with_name("test_trend_report.py"))
    assert spec is not None and spec.loader is not None
    fakes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fakes)
    env, _planning, _calls = _week(tmp_path, 4500)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    service = fakes._service(tmp_path, [])  # noqa: SLF001
    report = fakes._filled(tmp_path)  # noqa: SLF001
    service.store.create(report.report_id, "h", fakes.NOW.date(), {}, "human", None,
                         report.model_dump(mode="json"), fakes.NOW)  # fmt: skip
    monkeypatch.setattr(handlers, "_TREND_REPORTS", service)
    meta = RequestMeta(context_id="ctx_wr_abc0000009", token="x", requester="orchestrator",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    got = asyncio.run(handlers.HANDLERS["trend_report_insight"](AgentRequest(
        meta=meta, skill="trend_report_insight", input={"report_id": report.report_id})))
    past = got.output["insight"]["past"]
    assert [p["campaign_id"] for p in past] == ["wr_abc0000001"]
    assert past[0]["views"] == 4500 and past[0]["views_label"] == "조회 늘었음"
