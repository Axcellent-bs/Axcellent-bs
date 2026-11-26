"""video_insights: 유튜브 깊은 지표 (Analytics·Reporting, 채널 주인 OAuth). 실제 호출 없음.

가짜 응답(httpx.MockTransport)으로 영상별 값·유지 곡선·유입(광고 비율)·시청층·표본 부족·env 없음
(needs)·채널 불일치·Reporting job 없음/리포트 없음/있음·하루 1번·토큰 값이 응답·저장분·오류에 없음을
본다."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from ax_contracts.schemas.data import VideoInsightsRequest
from data_agent.connectors import youtube_analytics as yta
from data_agent.connectors.naver_searchad import JsonDailyCache
from data_agent.skills import video_insights as vi
from data_agent.skills import video_performance as vp

PUB = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
NOW = datetime(2026, 11, 26, 6, 0, tzinfo=UTC)
VID = "abcdefghijk"
URL = f"https://www.youtube.com/shorts/{VID}"
SECRET_ID, SECRET, REFRESH, ACCESS = "cid-123", "sec-456", "rt-789", "at-000-access"
KEYS = {"DATA_YT_OAUTH_CLIENT_ID": SECRET_ID, "DATA_YT_OAUTH_CLIENT_SECRET": SECRET,
        "DATA_YT_REFRESH_TOKEN": REFRESH}  # fmt: skip
REACH_CSV = (
    "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
    f"20261124,UC1,{VID},1000,0.05\n"
    f"20261125,UC1,{VID},3000,0.03\n"
    "20261125,UC1,zzzzzzzzzzz,500,0.10\n"
)
BASIC_CSV = (
    "date,channel_id,video_id,views,watch_time_minutes,average_view_duration_seconds\n"
    f"20261125,UC1,{VID},40,10.5,12\n"
)


class FakeGoogle:
    """구글 OAuth·Analytics·Reporting 가짜. 받은 요청을 모두 적는다."""

    def __init__(self, *, handle: str = "@Axcellent7", channel: str = "UC1",
                 jobs: list[dict[str, str]] | None = None, reports: bool = True,
                 audience: bool = True, analytics_status: int = 200) -> None:  # fmt: skip
        self.handle, self.channel = handle, channel
        reach = [{"id": "j1", "reportTypeId": "channel_reach_basic_a1"}]
        self.jobs = reach if jobs is None else jobs
        self.has_reports, self.audience = reports, audience
        self.analytics_status = analytics_status
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        url = str(request.url)
        if url.startswith(yta.TOKEN_URL):
            return httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
        assert request.headers.get("Authorization") == f"Bearer {ACCESS}"
        if url.startswith(yta.CHANNELS_URL):
            return httpx.Response(200, json={"items": [{"id": self.channel,
                                                        "snippet": {"customUrl": self.handle}}]})
        if url.startswith(yta.ANALYTICS_URL):
            if self.analytics_status != 200:
                return httpx.Response(self.analytics_status, json={"error": {"message": "no"}})
            q = parse_qs(request.url.query.decode())
            assert q["ids"] == ["channel==MINE"] and q["filters"] == [f"video=={VID}"]
            return httpx.Response(200, json=self._analytics(q))
        if url.endswith("/jobs"):
            return httpx.Response(200, json={"jobs": self.jobs})
        if "/jobs/" in url and url.endswith("/reports"):
            job = url.rsplit("/jobs/", 1)[1].split("/")[0]
            if not self.has_reports:
                return httpx.Response(200, json={})
            return httpx.Response(200, json={"reports": [
                {"id": "old", "createTime": "2026-11-24T00:00:00Z",
                 "downloadUrl": f"https://youtubereporting.googleapis.com/v1/media/{job}-old"},
                {"id": "new", "createTime": "2026-11-26T00:00:00Z",
                 "downloadUrl": f"https://youtubereporting.googleapis.com/v1/media/{job}-new"},
            ]})  # fmt: skip
        if "/v1/media/" in url:
            if url.endswith("-old"):
                return httpx.Response(200, text="date,channel_id,video_id\n")
            return httpx.Response(200, text=BASIC_CSV if "basic" in url else REACH_CSV)
        return httpx.Response(404)

    def _analytics(self, q: dict[str, list[str]]) -> dict[str, Any]:
        dims = q.get("dimensions", [""])[0]
        metrics = q["metrics"][0].split(",")
        if not dims:
            return {"columnHeaders": [{"name": m} for m in metrics],
                    "rows": [[120, 33.5, 21, 45.2, 10, 3, 2]]}  # fmt: skip
        if dims == "elapsedVideoTimeRatio":
            rows = [[round(i / 100, 2), round(1.2 - i / 100, 3)] for i in range(1, 101)]
            return {"columnHeaders": [{"name": "elapsedVideoTimeRatio"},
                                      {"name": "audienceWatchRatio"}], "rows": rows}  # fmt: skip
        if dims == "insightTrafficSourceType":
            return {"columnHeaders": [{"name": dims}, {"name": "views"}],
                    "rows": [["SHORTS", 80], ["ADVERTISING", 20]]}  # fmt: skip
        heads = [{"name": "ageGroup"}, {"name": "gender"}, {"name": "viewerPercentage"}]
        if not self.audience:
            return {"columnHeaders": heads}  # 표본 부족: 플랫폼이 rows를 안 준다
        return {"columnHeaders": heads,
                "rows": [["age18-24", "female", 60.0], ["age25-34", "male", 40.0]]}


@pytest.fixture
def tenants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "tenants"
    (root / "catbooks").mkdir(parents=True)
    monkeypatch.setenv("AX_TENANTS_DIR", str(root))
    _channel(root, "")
    return root


def _channel(root: Path, cid: str) -> None:
    (root / "catbooks" / "tenant.toml").write_text(
        f'name = "캣북스"\n[youtube_channel]\nchannel_id = "{cid}"\n', encoding="utf-8"
    )


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {vp.DB_ENV: str(tmp_path / "perf.db"), **extra}


def _register(env: dict[str, str]) -> None:
    store = vp.store_of(env)
    try:
        store.upsert(vp.Video(key=f"youtube:{VID}", platform="youtube", video_id=VID,
                              video_url=URL, campaign_id="CMP-1", insight_id=None,
                              proposal_id=None, test=False, published_at=PUB, title="숏츠",
                              registered_at=PUB, category=None, shorts_format=None))  # fmt: skip
    finally:
        store.close()


def _collect(env: dict[str, str], fake: FakeGoogle, now: datetime = NOW) -> vi.CollectResult:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    conn = yta.YouTubeAnalytics(env, client=client, calls=JsonDailyCache())
    videos, store = vp.store_of(env), vi.store_of(env)
    try:
        return asyncio.run(vi.collect_due(videos, store, conn, env, now))
    finally:
        videos.close()
        store.close()


def _read(env: dict[str, str], **req: Any) -> Any:
    return vi.video_insights(VideoInsightsRequest(**(req or {"video_url": URL})), env=env, now=NOW)


def test_video_values_curve_traffic_audience_and_reach(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **KEYS)
    _register(env)
    assert _collect(env, FakeGoogle()).saved == 1
    out = _read(env).output
    assert out.status == "known" and out.platform == "youtube" and out.campaign_id == "CMP-1"
    assert out.as_of is not None and out.as_of.isoformat() == "2026-11-25"  # 어제까지
    w = out.watch
    got = (w.views, w.watch_minutes, w.avg_view_seconds, w.avg_view_percent)
    assert got == (120, 33.5, 21, 45.2)
    assert (w.likes, w.shares, w.subscribers_gained) == (10, 3, 2)
    assert len(out.retention) == 20 and out.retention[-1].ratio == 1.0  # 100점 → 20점, 끝 점 남김
    assert out.retention[0].watch_ratio == 1.19  # 받은 값 그대로
    assert [(t.source, t.views, t.share, t.ad) for t in out.traffic] == [
        ("SHORTS", 80, 0.8, False), ("ADVERTISING", 20, 0.2, True)]
    assert out.ad_share == 0.2
    assert [(a.age, a.gender, a.percent) for a in out.audience] == [
        ("age18-24", "female", 60.0), ("age25-34", "male", 40.0)]
    assert out.audience_basis == "video" and out.audience_reason is None
    r = out.reach
    assert r.status == "known" and r.impressions == 4000  # 그 영상 줄 합계 (다른 영상 줄 뺌)
    assert r.ctr == pytest.approx((1000 * 0.05 + 3000 * 0.03) / 4000)  # 노출로 가중 평균
    labels = {f.label for f in out.facts}
    assert {"평균 시청 비율", "광고 유입 비율", "노출", "노출 클릭률"} <= labels
    assert out.needs == []  # 둘 다 연결됨


def test_empty_audience_is_sample_short_not_zero(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **KEYS)
    _register(env)
    _collect(env, FakeGoogle(audience=False))
    out = _read(env).output
    assert out.audience == [] and out.audience_reason == vi.SAMPLE_SHORT


def test_without_env_it_waits_with_needs_and_no_call(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path)
    _register(env)
    fake = FakeGoogle()
    assert _collect(env, fake).saved == 0 and fake.seen == []
    out = _read(env).output
    assert out.status == "unknown" and out.reason.startswith(vi.WAITING)
    assert {s.source: s.status for s in out.needs} == {"youtube_analytics": "waiting",
                                                       "youtube_reporting": "waiting"}
    assert any("DATA_YT_REFRESH_TOKEN" in n for n in out.needs[0].needs)
    listed = _read(env, list_all=True).output
    assert len(listed.videos) == 1 and listed.needs[0].status == "waiting"


def test_old_env_names_also_connect_and_new_names_win(tmp_path: Path, tenants: Path) -> None:
    old = {"YT_CLIENT_ID": "old-id", "YT_CLIENT_SECRET": "old-sec", "YT_REFRESH_TOKEN": "old-rt"}
    assert yta.YouTubeAnalytics(old).configured and yta.missing(old) == []
    env = _env(tmp_path, **old, DATA_YT_OAUTH_CLIENT_ID=SECRET_ID)
    _register(env)
    fake = FakeGoogle()
    _collect(env, fake)
    body = parse_qs(fake.seen[0].content.decode())
    assert body["client_id"] == [SECRET_ID] and body["refresh_token"] == ["old-rt"]


def test_channel_mismatch_and_missing_channel_id(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **KEYS)
    _register(env)
    _channel(tenants, "UCother")  # 회사 설정 채널 ≠ 토큰 채널
    _collect(env, FakeGoogle())
    out = _read(env).output
    assert out.status == "unknown" and out.reason == vi.NOT_OUR_CHANNEL and out.watch is None
    _channel(tenants, "")  # 비어 있고 handle도 우리 채널이 아님
    _collect(env, FakeGoogle(handle="@someone"), NOW + timedelta(days=1))
    assert _read(env).output.reason == vi.NO_CHANNEL_ID
    _channel(tenants, "UC1")  # 같은 채널이면 handle은 보지 않는다
    _collect(env, FakeGoogle(handle="@someone"), NOW + timedelta(days=2))
    assert _read(env).output.status == "known"


def test_reporting_job_missing_or_report_pending(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **KEYS)
    _register(env)
    _collect(env, FakeGoogle(jobs=[]))
    out = _read(env).output
    assert out.status == "known" and out.reach.status == "unknown"
    assert out.reach.reason == vi.RESERVE_NEEDED and out.reach.impressions is None
    src = {s.source: s for s in out.needs}
    assert src["youtube_reporting"].status == "check" and "youtube_analytics" not in src
    _collect(env, FakeGoogle(reports=False), NOW + timedelta(days=1))
    out = _read(env).output
    assert out.reach.reason == vi.REPORT_PENDING
    assert {s.source: s.status for s in out.needs} == {"youtube_reporting": "waiting"}


def test_basic_report_only_when_analytics_fails(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **KEYS)
    _register(env)
    jobs = [{"id": "reach", "reportTypeId": "channel_reach_basic_a1"},
            {"id": "basic", "reportTypeId": "channel_basic_a3"}]  # fmt: skip
    _collect(env, FakeGoogle(jobs=jobs, analytics_status=403))
    out = _read(env).output
    assert out.watch.source == "youtube_reporting" and out.watch.views == 40
    assert out.watch.avg_view_percent is None  # 리포트에 없는 값은 채우지 않는다
    assert out.reason.startswith("YouTube Analytics를 읽지 못함 [PERMISSION]")
    assert {s.source: s.status for s in out.needs} == {"youtube_analytics": "check"}


def test_once_a_day_per_video(tmp_path: Path, tenants: Path) -> None:
    env = _env(tmp_path, **KEYS)
    _register(env)
    _collect(env, FakeGoogle())
    again = FakeGoogle()
    assert _collect(env, again, NOW + timedelta(hours=5)).saved == 0 and again.seen == []
    assert _collect(env, FakeGoogle(), NOW + timedelta(hours=24)).saved == 1
    assert _collect(env, FakeGoogle(), NOW + timedelta(hours=26),).saved == 0
    env[vi.EVERY_ENV] = "1"
    assert _collect(env, FakeGoogle(), NOW + timedelta(hours=26)).saved == 1


def test_token_values_never_leave(tmp_path: Path, tenants: Path, capsys: pytest.CaptureFixture[str]
                                  ) -> None:  # fmt: skip
    env = _env(tmp_path, **KEYS)
    _register(env)
    _collect(env, FakeGoogle())
    texts = [json.dumps(_read(env).output.model_dump(mode="json"), ensure_ascii=False),
             json.dumps(_read(env, list_all=True).output.model_dump(mode="json"),
                        ensure_ascii=False), repr(yta.YouTubeAnalytics(env))]  # fmt: skip
    conn = sqlite3.connect(env[vp.DB_ENV])
    texts += [str(r) for r in conn.execute("SELECT * FROM insights").fetchall()]
    texts += [str(r) for r in conn.execute("SELECT * FROM insight_sources").fetchall()]
    conn.close()

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant",
                                         "error_description": f"bad {REFRESH}"})  # fmt: skip

    bad = yta.YouTubeAnalytics(env, client=httpx.AsyncClient(transport=httpx.MockTransport(refuse)),
                               calls=JsonDailyCache())  # fmt: skip
    with pytest.raises(yta.YtaError) as caught:
        asyncio.run(bad.my_channel())
    texts.append(str(caught.value))
    texts.append(capsys.readouterr().out)
    for secret in (SECRET_ID, SECRET, REFRESH, ACCESS):
        assert all(secret not in t for t in texts), secret
    assert "invalid_grant" in str(caught.value)


def test_collector_keeps_going_when_it_breaks(tmp_path: Path, tenants: Path) -> None:
    from data_agent import youtube_collect

    env = _env(tmp_path, **KEYS)
    _register(env)

    class Broken(yta.YouTubeAnalytics):
        async def my_channel(self) -> dict[str, str]:
            raise RuntimeError("boom")

    assert asyncio.run(youtube_collect.collect_insights(env, NOW, Broken(env))) == 0


def test_the_connector_only_reads() -> None:
    """쓰기 경로 없음: 보내는 POST는 토큰 갱신 하나, job 만들기·수정·삭제 경로가 없다."""
    source = Path(yta.__file__).read_text(encoding="utf-8")
    assert source.count('"POST"') == 1 and "TOKEN_URL" in source
    for word in ('"PUT"', '"PATCH"', '"DELETE"', "jobs.create", ".post(", ".put(", ".delete("):
        assert word not in source
