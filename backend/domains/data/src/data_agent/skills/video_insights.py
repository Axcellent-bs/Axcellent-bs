"""video_insights: 게시한 영상의 깊은 지표 (유지율·유입 경로·시청층·노출 클릭률). grade read.

- 수집(하루 1번, `DATA_YT_ANALYTICS_EVERY_HOURS` 기본 24): 1시간 수집기가 video_performance 추적
  목록의 유튜브 영상을 YouTube Analytics API(채널 주인 OAuth)로 읽어 `video_performance.db`의 새 표
  `insights`(영상 key 기준)에 저장한다. skill 요청·화면은 저장분만 읽는다(외부 호출 없음)
  - 영상별: views·estimatedMinutesWatched·averageViewDuration·averageViewPercentage·likes·shares·
    subscribersGained (게시일 ~ 어제)
  - 유지 곡선: elapsedVideoTimeRatio × audienceWatchRatio (최대 20점으로 고르게 줄임)
  - 유입 경로: insightTrafficSourceType × views (ADVERTISING = 광고, 비율은 코드)
  - 시청층: ageGroup·gender × viewerPercentage (비면 "표본 부족", 채우지 않는다)
  - 노출·클릭률: Reporting API `channel_reach_basic_a1` 최신 리포트 CSV의 그 영상 줄 (job은 만들지
    않는다: 없으면 "예약 필요", 리포트가 아직 없으면 "리포트 기다리는 중")
  - Analytics가 실패하면 조회·시청 시간만 `channel_basic_a3` 최신 리포트로 대신한다
- 회사: 영상 회사의 `tenant.toml [youtube_channel] channel_id`가 토큰 채널 id와 다르면 그 영상은
  unknown "이 채널 토큰이 아님". 비어 있으면 토큰 채널 handle이 `@axcellent7`(대소문자 무시)일 때만
  맞는 채널로 본다 (지금 토큰은 AXcellent 채널 하나)
- env가 없으면 unknown + "연결 대기" + 필요한 것(env 이름만, 값·일부 글자 없음)
- 외부 값은 받은 그대로 저장한다. 비율·합계·가중 평균만 코드가 낸다. 숫자는 모두 facts에
"""

from __future__ import annotations

import sqlite3
import threading
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    DataFact,
    VideoInsightsAudience,
    VideoInsightsOutput,
    VideoInsightsReach,
    VideoInsightsRequest,
    VideoInsightsRetentionPoint,
    VideoInsightsSource,
    VideoInsightsSourceId,
    VideoInsightsTraffic,
    VideoInsightsWatch,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors import youtube_analytics as yta
from data_agent.skills import video_performance as vp

SCHEMA_VERSION = "video_insights.v1"
EVERY_ENV, DEFAULT_EVERY_HOURS = "DATA_YT_ANALYTICS_EVERY_HOURS", 24
OUR_HANDLE = "@axcellent7"  # channel_id가 비었을 때 맞는 채널로 보는 토큰 채널 handle
REACH_REPORT = "channel_reach_basic_a1"
BASIC_REPORT = "channel_basic_a3"
RETENTION_POINTS = 20
WAITING = "연결 대기"
SAMPLE_SHORT = "표본 부족 (플랫폼이 시청층 비율을 주지 않음)"
NOT_OUR_CHANNEL = "이 채널 토큰이 아님"
NO_CHANNEL_ID = "회사 설정에 채널 id가 없음 ([youtube_channel] channel_id)"
RESERVE_NEEDED = "예약 필요 (Reporting job channel_reach_basic_a1)"
REPORT_PENDING = "리포트 기다리는 중 (첫 리포트는 예약 다음 날 이후)"
NOT_COLLECTED = "아직 수집 전 (하루 1번 수집기)"
AD_SOURCE = "ADVERTISING"
VIDEO_METRICS = ("views", "estimatedMinutesWatched", "averageViewDuration",
                 "averageViewPercentage", "likes", "shares", "subscribersGained")  # fmt: skip
NEEDS_ANALYTICS = [
    *[f"서버 env {names[0]} (옛 이름 {names[1]})" for names in yta.KEY_ENVS],
    "채널 주인 계정의 OAuth 동의 (yt-analytics.readonly)",
]
NEEDS_REPORTING = ["YouTube Reporting job channel_reach_basic_a1 예약 (채널 주인)"]
# 인스타 (11/26 H): 데이터 인스타 토큰으로 우리 계정(DATA_IG_USER_ID) 미디어 인사이트·계정 시청층
IG_EVERY_ENV, DEFAULT_IG_EVERY_HOURS = "DATA_IG_INSIGHTS_EVERY_HOURS", 24
IG_METRICS = ("views", "reach", "saved", "shares", "total_interactions",
              "ig_reels_avg_watch_time")  # fmt: skip
IG_MEDIA_LIMIT = 50
IG_PERMISSION = "instagram_manage_insights 권한 필요"
NOT_OUR_ACCOUNT = "우리 계정(DATA_IG_USER_ID)이 아닌 게시 계정이라 공개값만 (video_performance)"
NO_PUBLISH_ACCOUNT = "회사 설정에 게시 계정 아이디가 없음 ([instagram] publish_username)"
IG_NOT_FOUND = "우리 계정 최근 50개에서 이 게시물을 찾지 못함"
IG_SAMPLE_SHORT = "표본 부족 (팔로워 100명 미만이면 플랫폼이 계정 시청층을 주지 않음)"
NEEDS_IG = [
    "서버 env DATA_IG_ACCESS_TOKEN", "서버 env DATA_IG_USER_ID",
    "instagram_manage_insights 권한 (instagram_basic·pages_read_engagement와 같이)",
]


# ---------------------------------------------------------------- 저장


class InsightStore:
    """영상 key → 깊은 지표(계약 VideoInsightsOutput JSON)와 출처 상태. video_performance.db 안."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS insights (key TEXT PRIMARY KEY, payload TEXT NOT NULL,"
                " collected_at TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS insight_sources (source TEXT PRIMARY KEY,"
                " status TEXT NOT NULL, reason TEXT, at TEXT NOT NULL);"
            )

    def close(self) -> None:
        self._conn.close()

    def save(self, key: str, out: VideoInsightsOutput, at: datetime) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO insights VALUES (?,?,?)",
                               (key, out.model_dump_json(), at.isoformat()))  # fmt: skip

    def get(self, key: str) -> VideoInsightsOutput | None:
        with self._lock:
            row = self._conn.execute("SELECT payload FROM insights WHERE key = ?",
                                     (key,)).fetchone()  # fmt: skip
        return VideoInsightsOutput.model_validate_json(row[0]) if row else None

    def collected_at(self, key: str) -> datetime | None:
        with self._lock:
            row = self._conn.execute("SELECT collected_at FROM insights WHERE key = ?",
                                     (key,)).fetchone()  # fmt: skip
        return datetime.fromisoformat(row[0]) if row else None

    def set_source(self, source: str, status: str, reason: str | None, at: datetime) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO insight_sources VALUES (?,?,?,?)",
                               (source, status, reason, at.isoformat()))  # fmt: skip

    def source(self, source: str) -> tuple[str, str | None] | None:
        with self._lock:
            row = self._conn.execute("SELECT status, reason FROM insight_sources WHERE source = ?",
                                     (source,)).fetchone()  # fmt: skip
        return (row[0], row[1]) if row else None

    def tenant_of(self, key: str) -> str | None:
        """그 영상을 등록한 회사 (video_performance videos 표)."""
        with self._lock:
            try:
                row = self._conn.execute("SELECT tenant_id FROM videos WHERE key = ?",
                                         (key,)).fetchone()  # fmt: skip
            except sqlite3.OperationalError:
                return None
        return str(row[0]) if row and row[0] else None


def store_of(env: Mapping[str, str]) -> InsightStore:
    return InsightStore(env.get(vp.DB_ENV) or vp.DEFAULT_DB)


# ---------------------------------------------------------------- 회사 채널


def tenant_value(tenant_id: str | None, section: str, key: str) -> str:
    """그 회사 설정 `tenant.toml [section] key` 글 (없거나 못 읽으면 "")."""
    from ax_agent_template import tenant

    if not tenant_id or not tenant.is_tenant(tenant_id):
        return ""
    path = tenant.tenants_dir() / tenant_id / tenant.TENANT_FILE
    try:
        with path.open("rb") as f:
            got = tomllib.load(f).get(section)
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    value = got.get(key) if isinstance(got, dict) else None
    return value.strip() if isinstance(value, str) else ""


def channel_id_of(tenant_id: str | None) -> str:
    """회사 설정 `[youtube_channel] channel_id` (없거나 못 읽으면 "")."""
    return tenant_value(tenant_id, "youtube_channel", "channel_id")


def channel_problem(want: str, mine: Mapping[str, str]) -> str | None:
    """회사 채널과 토큰 채널이 맞으면 None, 아니면 이유."""
    if want:
        return None if want == mine.get("id") else NOT_OUR_CHANNEL
    if (mine.get("handle") or "").lower() == OUR_HANDLE:
        return None
    return NO_CHANNEL_ID


# ---------------------------------------------------------------- 값 (코드)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v))
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return int(n) if n is not None and n >= 0 else None


def retention_of(rows: Sequence[Mapping[str, Any]]) -> list[VideoInsightsRetentionPoint]:
    """유지 곡선 (받은 점을 길이 순으로, 최대 20점이 되게 고르게 고른다. 마지막 점은 남긴다)."""
    raw = [(_num(x.get("elapsedVideoTimeRatio")), _num(x.get("audienceWatchRatio"))) for x in rows]
    pts = sorted((r, w) for r, w in raw
                 if r is not None and w is not None and 0 <= r <= 1 and w >= 0)  # fmt: skip
    if len(pts) > RETENTION_POINTS:
        step = len(pts) / RETENTION_POINTS
        picked = [pts[int(i * step)] for i in range(RETENTION_POINTS - 1)] + [pts[-1]]
        pts = picked
    return [VideoInsightsRetentionPoint(ratio=r, watch_ratio=w) for r, w in pts]


def traffic_of(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[list[VideoInsightsTraffic], float | None]:
    """유입 경로 (조회 많은 순)와 광고 비율. 비율 = 그 줄 조회 ÷ 합계 (코드)."""
    got = [(str(r.get("insightTrafficSourceType") or ""), _int(r.get("views"))) for r in rows]
    got = [(s[:60], v) for s, v in got if s and v is not None]
    total = sum(v for _, v in got)
    out = [VideoInsightsTraffic(source=s, views=v, share=round(v / total, 4) if total else None,
                                ad=s == AD_SOURCE) for s, v in sorted(got, key=lambda x: -x[1])]
    ad = sum(v for s, v in got if s == AD_SOURCE)
    return out[:30], (round(ad / total, 4) if total else None)


def audience_of(rows: Sequence[Mapping[str, Any]]) -> list[VideoInsightsAudience]:
    out = []
    for r in rows:
        p = _num(r.get("viewerPercentage"))
        if p is None or not 0 <= p <= 100:
            continue
        out.append(VideoInsightsAudience(age=str(r.get("ageGroup") or "")[:20],
                                         gender=str(r.get("gender") or "")[:20], percent=p))
    return out[:60]


def reach_of(rows: Sequence[Mapping[str, str]], video_id: str) -> VideoInsightsReach:
    """Reporting CSV의 그 영상 줄: 노출 합계와 노출로 가중 평균한 클릭률 (코드)."""
    mine = [r for r in rows if r.get("video_id") == video_id]
    pairs = [(_int(r.get("video_thumbnail_impressions")),
              _num(r.get("video_thumbnail_impressions_ctr"))) for r in mine]  # fmt: skip
    pairs = [(i, c) for i, c in pairs if i is not None]
    if not pairs:
        why = "최신 리포트에 이 영상 줄이 없음 (노출 없음 또는 리포트 날짜 밖)"
        return VideoInsightsReach(reason=why)
    total = sum(i for i, _ in pairs)
    weighted = [(i, c) for i, c in pairs if c is not None]
    ctr = (sum(i * c for i, c in weighted) / total) if total and weighted else None
    days = sorted({r.get("date") or "" for r in mine} - {""})
    period = f"{days[0]}~{days[-1]}" if days else None
    return VideoInsightsReach(status="known", impressions=total,
                              ctr=round(ctr, 6) if ctr is not None else None, period=period)


def basic_of(rows: Sequence[Mapping[str, str]], video_id: str) -> VideoInsightsWatch | None:
    """Analytics가 실패할 때만: channel_basic_a3 최신 리포트의 그 영상 줄 (합계·가중 평균 코드)."""
    mine = [r for r in rows if r.get("video_id") == video_id]
    views = [_int(r.get("views")) or 0 for r in mine]
    if not mine or not sum(views):
        return None
    minutes = sum(_num(r.get("watch_time_minutes")) or 0.0 for r in mine)
    secs = [(_num(r.get("average_view_duration_seconds")), v) for r, v in zip(mine, views,
                                                                              strict=True)]
    known = [(s, v) for s, v in secs if s is not None]
    avg = sum(s * v for s, v in known) / sum(v for _, v in known) if known and sum(
        v for _, v in known) else None
    return VideoInsightsWatch(views=sum(views), watch_minutes=round(minutes, 2),
                              avg_view_seconds=round(avg, 2) if avg is not None else None,
                              source="youtube_reporting")  # fmt: skip


def watch_of(row: Mapping[str, Any]) -> VideoInsightsWatch:
    return VideoInsightsWatch(
        views=_int(row.get("views")), watch_minutes=_num(row.get("estimatedMinutesWatched")),
        avg_view_seconds=_num(row.get("averageViewDuration")),
        avg_view_percent=_num(row.get("averageViewPercentage")),
        likes=_int(row.get("likes")), shares=_int(row.get("shares")),
        subscribers_gained=_int(row.get("subscribersGained")), source="youtube_analytics",
    )  # fmt: skip


def facts_of(out: VideoInsightsOutput) -> list[DataFact]:
    """응답 숫자 전부 (받은 값과 코드 계산)."""
    key, link = out.video_key or "", out.video_url
    period = out.as_of.isoformat() if out.as_of else None
    rows: list[DataFact] = []

    def add(label: str, value: float | None, unit: str, source: str) -> None:
        if value is not None:
            rows.append(DataFact(fact_id=vp.fact_id(key, f"insight {label}"), label=label,
                                 value=float(value), unit=unit, source=source, link=link,
                                 period=period, about=key))  # fmt: skip

    w = out.watch
    if w is not None:
        src = w.source or "youtube_analytics"
        for label, value, unit in (
            ("조회", w.views, "회"), ("평균 시청 시간", w.avg_view_seconds, "초"),
            ("평균 시청 비율", w.avg_view_percent, "%"), ("총 시청 시간", w.watch_minutes, "분"),
            ("좋아요", w.likes, "개"), ("공유", w.shares, "회"),
            ("구독자 증가", w.subscribers_gained, "명"), ("도달", w.reach, "명"),
            ("저장", w.saved, "회"), ("반응 합계", w.total_interactions, "회"),
        ):  # fmt: skip
            add(label, value, unit, src)
    for t in out.traffic:
        add(f"유입 {t.source} 조회", t.views, "회", "youtube_analytics")
    add("광고 유입 비율", out.ad_share, "비율", "code")
    if out.reach is not None and out.reach.status == "known":
        add("노출", out.reach.impressions, "회", "youtube_reporting")
        add("노출 클릭률", out.reach.ctr, "비율", "code")
    return rows


# ---------------------------------------------------------------- 출처 상태


def _saved_sources(
    store: InsightStore | None, rows: Sequence[tuple[VideoInsightsSourceId, list[str]]]
) -> list[VideoInsightsSource]:
    out = []
    for source, needs in rows:
        saved = store.source(source) if store is not None else None
        status, reason = saved if saved else ("waiting", NOT_COLLECTED)
        out.append(VideoInsightsSource(
            source=source, status=status if status in ("connected", "waiting", "check")
            else "check", reason=reason, needs=[] if status == "connected" else list(needs),
        ))  # fmt: skip
    return out


def youtube_sources(
    env: Mapping[str, str], store: InsightStore | None
) -> list[VideoInsightsSource]:
    """유튜브 출처 연결 상태 (env가 없으면 연결 대기 + 필요한 것)."""
    lack = yta.missing(env)
    if lack:
        why = f"{WAITING}: {', '.join(lack)} 없음"
        return [VideoInsightsSource(source="youtube_analytics", status="waiting", reason=why,
                                    needs=list(NEEDS_ANALYTICS)),
                VideoInsightsSource(source="youtube_reporting", status="waiting", reason=why,
                                    needs=[*NEEDS_ANALYTICS[:3], *NEEDS_REPORTING])]  # fmt: skip
    return _saved_sources(store, (("youtube_analytics", NEEDS_ANALYTICS[3:]),
                                  ("youtube_reporting", NEEDS_REPORTING)))  # fmt: skip


def ig_missing(env: Mapping[str, str]) -> list[str]:
    from data_agent.connectors.instagram_graph import TOKEN_ENV, USER_ENV

    return [name for name in (TOKEN_ENV, USER_ENV) if not (env.get(name) or "").strip()]


def instagram_sources(
    env: Mapping[str, str], store: InsightStore | None
) -> list[VideoInsightsSource]:
    """인스타 출처 연결 상태 (미디어 인사이트·계정 시청층)."""
    lack = ig_missing(env)
    if lack:
        why = f"{WAITING}: {', '.join(lack)} 없음"
        return [VideoInsightsSource(source=s, status="waiting", reason=why, needs=list(NEEDS_IG))
                for s in ("instagram_insights", "instagram_audience")]  # fmt: skip
    return _saved_sources(store, (("instagram_insights", NEEDS_IG[2:]),
                                  ("instagram_audience", NEEDS_IG[2:])))  # fmt: skip


def sources(
    env: Mapping[str, str], store: InsightStore | None, platform: str | None = None
) -> list[VideoInsightsSource]:
    """출처 연결 상태 (platform이 없으면 유튜브·인스타 모두)."""
    out: list[VideoInsightsSource] = []
    if platform in (None, "youtube"):
        out += youtube_sources(env, store)
    if platform in (None, "instagram"):
        out += instagram_sources(env, store)
    return out


# ---------------------------------------------------------------- 수집 (하루 1번)


def every_hours(env: Mapping[str, str]) -> int:
    from data_agent.connectors.naver_searchad import limit_of

    return limit_of(env, EVERY_ENV, DEFAULT_EVERY_HOURS)


@dataclass
class CollectResult:
    saved: int = 0
    problems: list[str] | None = None

    def line(self) -> str:
        tail = f", {'; '.join(self.problems)}" if self.problems else ""
        return f"[insights] 깊은 지표 {self.saved}개{tail}"


async def _latest_csv(conn: yta.YouTubeAnalytics, job_id: str) -> list[dict[str, str]] | None:
    """그 job의 가장 늦게 만든 리포트 CSV. 리포트가 없으면 None."""
    reports = await conn.reports(job_id)
    if not reports:
        return None
    latest = max(reports, key=lambda r: r.get("createTime") or "")
    return await conn.download_csv(latest["downloadUrl"])


async def collect_due(
    videos: vp.VideoStore, store: InsightStore, conn: yta.YouTubeAnalytics | None,
    env: Mapping[str, str], now: datetime | None = None,
) -> CollectResult:  # fmt: skip
    """등록된 유튜브 영상 중 마지막 수집에서 `every_hours`가 지난 것만 읽어 저장한다."""
    now = now or datetime.now(UTC)
    result = CollectResult(problems=[])
    if conn is None or not conn.configured:
        return result  # 연결 대기 (출처 상태는 env로 바로 보인다)
    gap = timedelta(hours=every_hours(env))
    due = [v for v in videos.videos(all_companies=True) if v.platform == "youtube"
           and v.published_at is not None
           and ((at := store.collected_at(v.key)) is None or now - at >= gap)]  # fmt: skip
    if not due:
        return result
    try:
        mine = await conn.my_channel()
    except ConnectorUnavailable as error:
        store.set_source("youtube_analytics", "check", f"토큰 채널을 읽지 못함 [{error.code}]", now)
        result.problems = [f"토큰 채널을 읽지 못함 [{error.code}]"]
        return result
    reach_rows, basic_rows, reach_why = await _reporting(conn, store, now)
    ok_any, bad = False, ""
    for v in due:
        out = await _one(conn, v, store, mine, reach_rows, basic_rows, reach_why, now)
        store.save(v.key, out, now)
        result.saved += 1
        if out.watch is not None and out.watch.source == "youtube_analytics":
            ok_any = True
        elif out.reason:
            bad = out.reason
    store.set_source("youtube_analytics", "connected" if ok_any or not bad else "check",
                     None if ok_any or not bad else bad, now)  # fmt: skip
    return result


async def _reporting(
    conn: yta.YouTubeAnalytics, store: InsightStore, now: datetime
) -> tuple[list[dict[str, str]] | None, list[dict[str, str]] | None, str | None]:
    """(노출 리포트 줄, 기본 리포트 줄, 노출이 없을 때 이유). job은 만들지 않는다."""
    try:
        jobs = {j["reportTypeId"]: j["id"] for j in await conn.jobs()}
    except ConnectorUnavailable as error:
        why = f"Reporting job 목록을 읽지 못함 [{error.code}]"
        store.set_source("youtube_reporting", "check", why, now)
        return None, None, why
    basic: list[dict[str, str]] | None = None
    try:
        if BASIC_REPORT in jobs:
            basic = await _latest_csv(conn, jobs[BASIC_REPORT])
    except ConnectorUnavailable:
        basic = None
    if REACH_REPORT not in jobs:
        store.set_source("youtube_reporting", "check", RESERVE_NEEDED, now)
        return None, basic, RESERVE_NEEDED
    try:
        reach = await _latest_csv(conn, jobs[REACH_REPORT])
    except ConnectorUnavailable as error:
        why = f"리포트를 받지 못함 [{error.code}]"
        store.set_source("youtube_reporting", "check", why, now)
        return None, basic, why
    if reach is None:
        store.set_source("youtube_reporting", "waiting", REPORT_PENDING, now)
        return None, basic, REPORT_PENDING
    store.set_source("youtube_reporting", "connected", None, now)
    return reach, basic, None


async def _one(
    conn: yta.YouTubeAnalytics, v: vp.Video, store: InsightStore, mine: Mapping[str, str],
    reach_rows: list[dict[str, str]] | None, basic_rows: list[dict[str, str]] | None,
    reach_why: str | None, now: datetime,
) -> VideoInsightsOutput:  # fmt: skip
    out = VideoInsightsOutput(platform="youtube", video_key=v.key, video_id=v.video_id,
                              video_url=v.video_url, campaign_id=v.campaign_id,
                              collected_at=now)  # fmt: skip
    problem = channel_problem(channel_id_of(store.tenant_of(v.key)), mine)
    if problem is not None:
        out.reason = problem
        out.summary = f"깊은 지표 없음: {problem}"
        return out
    end = (now - timedelta(days=1)).date()
    start = min(v.published_at.date(), end) if v.published_at else end
    out.as_of = end
    s, e = start.isoformat(), end.isoformat()
    try:
        rows = await conn.report(s, e, VIDEO_METRICS, video_id=v.video_id)
        out.watch = watch_of(rows[0]) if rows else None
        out.retention = retention_of(await conn.report(
            s, e, ("audienceWatchRatio",), dimensions=("elapsedVideoTimeRatio",),
            video_id=v.video_id))  # fmt: skip
        out.traffic, out.ad_share = traffic_of(await conn.report(
            s, e, ("views",), dimensions=("insightTrafficSourceType",), video_id=v.video_id))
        out.audience = audience_of(await conn.report(
            s, e, ("viewerPercentage",), dimensions=("ageGroup", "gender"), video_id=v.video_id))
        out.audience_basis = "video"
        out.audience_reason = None if out.audience else SAMPLE_SHORT
    except ConnectorUnavailable as error:
        out.reason = f"YouTube Analytics를 읽지 못함 [{error.code}]"
        fallback = basic_of(basic_rows or [], v.video_id)
        if fallback is not None:
            out.watch = fallback
    out.reach = reach_of(reach_rows, v.video_id) if reach_rows is not None else (
        VideoInsightsReach(reason=reach_why))  # fmt: skip
    out.status = "known" if out.watch is not None or out.traffic else "unknown"
    if out.status == "unknown" and out.reason is None:
        out.reason = "YouTube Analytics에 아직 이 영상 값이 없음 (게시 다음 날부터)"
    out.facts = facts_of(out)
    w = out.watch
    shown = w is not None and w.avg_view_percent is not None
    out.summary = (f"평균 시청 비율 {w.avg_view_percent:.1f}%" if w is not None and shown
                   else out.reason or "")[:300]  # fmt: skip
    return out


# ---------------------------------------------------------------- 인스타 (하루 1번, 11/26 H)


def _handle(name: str) -> str:
    return name.strip().lstrip("@").lower()


def ig_audience_of(
    ages: Sequence[tuple[str, float]], genders: Sequence[tuple[str, float]]
) -> list[VideoInsightsAudience]:
    """계정 시청층: 연령대 줄(성별 빈칸)과 성별 줄(연령대 빈칸).

    percent = 값 ÷ 합계 × 100 (코드)."""
    out: list[VideoInsightsAudience] = []
    for rows, is_age in ((ages, True), (genders, False)):
        total = sum(n for _, n in rows)
        for name, n in rows:
            if total > 0:
                out.append(VideoInsightsAudience(age=name if is_age else "",
                                                 gender="" if is_age else name,
                                                 percent=round(n / total * 100, 2)))  # fmt: skip
    return out[:60]


def ig_watch_of(got: Mapping[str, float]) -> VideoInsightsWatch | None:
    """미디어 인사이트 → 시청 값. ig_reels_avg_watch_time은 밀리초라 ÷ 1000 (단위 변환만)."""
    if not got:
        return None

    def n(key: str) -> int | None:
        v = got.get(key)
        return int(v) if v is not None and v >= 0 else None

    ms = got.get("ig_reels_avg_watch_time")
    return VideoInsightsWatch(
        views=n("views"), reach=n("reach"), saved=n("saved"), shares=n("shares"),
        total_interactions=n("total_interactions"),
        avg_view_seconds=round(ms / 1000, 3) if ms is not None and ms >= 0 else None,
        source="instagram_insights",
    )  # fmt: skip


async def collect_instagram_due(
    videos: vp.VideoStore, store: InsightStore, graph: Any, env: Mapping[str, str],
    now: datetime | None = None,
) -> CollectResult:  # fmt: skip
    """등록된 인스타 영상 중 마지막 수집에서 `DATA_IG_INSIGHTS_EVERY_HOURS`가 지난 것만, 우리 계정
    (DATA_IG_USER_ID) 미디어 인사이트와 계정 시청층을 읽어 저장한다 (Graph 하루 상한 그대로).

    회사 게시 계정(publish_username)이 우리 계정이 아니면 그 회사 영상은 공개값만(이유만 저장)."""
    from data_agent.connectors.naver_searchad import limit_of

    now = now or datetime.now(UTC)
    result = CollectResult(problems=[])
    if graph is None or not getattr(graph, "configured", False):
        return result
    gap = timedelta(hours=limit_of(env, IG_EVERY_ENV, DEFAULT_IG_EVERY_HOURS))
    due = [v for v in videos.videos(all_companies=True) if v.platform == "instagram"
           and ((at := store.collected_at(v.key)) is None or now - at >= gap)]  # fmt: skip
    if not due:
        return result

    def why_of(error: ConnectorUnavailable) -> str:
        if error.code == "PERMISSION":
            return IG_PERMISSION
        return f"인스타 인사이트를 읽지 못함 [{error.code}]"

    try:
        ours = _handle(await graph.own_username())
        media = await graph.own_media(IG_MEDIA_LIMIT)
    except ConnectorUnavailable as error:
        store.set_source("instagram_insights", "check", why_of(error), now)
        result.problems = [why_of(error)]
        return result
    by_code = {}
    for m in media:
        parsed = vp.parse_video(str(m.get("permalink") or ""))
        if parsed and parsed[0] == "instagram":
            by_code[parsed[1]] = str(m["id"])
    audience, audience_why = await _ig_audience(graph, store, now, why_of)
    ok_any, bad = False, ""
    for v in due:
        out = VideoInsightsOutput(platform="instagram", video_key=v.key, video_id=v.video_id,
                                  video_url=v.video_url, campaign_id=v.campaign_id,
                                  collected_at=now, as_of=now.date())  # fmt: skip
        account = _handle(tenant_value(store.tenant_of(v.key), "instagram", "publish_username"))
        if not account:
            out.reason = NO_PUBLISH_ACCOUNT
        elif account != ours:
            out.reason = NOT_OUR_ACCOUNT
        elif v.video_id not in by_code:
            out.reason = IG_NOT_FOUND
        else:
            try:
                out.watch = ig_watch_of(await graph.media_insights(by_code[v.video_id], IG_METRICS))
            except ConnectorUnavailable as error:
                out.reason = why_of(error)
                bad = out.reason
            out.audience, out.audience_basis = audience, "account"
            out.audience_reason = audience_why
            if out.watch is not None:
                ok_any = True
            elif out.reason is None:
                out.reason = "인스타가 이 게시물 인사이트를 아직 주지 않음"
        out.status = "known" if out.watch is not None else "unknown"
        out.facts = facts_of(out)
        w = out.watch
        out.summary = (f"도달 {w.reach:,}명" if w is not None and w.reach is not None
                       else out.reason or "")[:300]  # fmt: skip
        store.save(v.key, out, now)
        result.saved += 1
    if ok_any or bad:
        store.set_source("instagram_insights", "connected" if ok_any and not bad else "check",
                         None if ok_any and not bad else bad, now)  # fmt: skip
    return result


async def _ig_audience(
    graph: Any, store: InsightStore, now: datetime, why_of: Any
) -> tuple[list[VideoInsightsAudience], str | None]:
    """계정 시청층 (한 번에 연령대·성별 두 번). 비면 표본 부족, 권한 없음이면 그 이유."""
    try:
        ages = await graph.account_demographics("age")
        genders = await graph.account_demographics("gender")
    except ConnectorUnavailable as error:
        store.set_source("instagram_audience", "check", why_of(error), now)
        return [], why_of(error)
    rows = ig_audience_of(ages, genders)
    store.set_source("instagram_audience", "connected", None if rows else IG_SAMPLE_SHORT, now)
    return rows, None if rows else IG_SAMPLE_SHORT


# ---------------------------------------------------------------- skill (저장분만)


@dataclass
class InsightsResult:
    output: VideoInsightsOutput
    evidence: Evidence


def placeholder(v: vp.Video, env: Mapping[str, str], store: InsightStore) -> VideoInsightsOutput:
    """저장분이 없는 영상: 이유와 필요한 것 (지어내지 않는다)."""
    out = VideoInsightsOutput(platform=v.platform, video_key=v.key, video_id=v.video_id,
                              video_url=v.video_url, campaign_id=v.campaign_id)  # fmt: skip
    lack = (yta.missing(env) if v.platform == "youtube"
            else ig_missing(env) if v.platform == "instagram" else None)  # fmt: skip
    if lack is None:
        out.reason = f"{WAITING}: {v.platform} 깊은 지표를 읽는 연결이 아직 없음"
    else:
        out.reason = f"{WAITING}: {', '.join(lack)} 없음" if lack else NOT_COLLECTED
    out.summary = f"깊은 지표 없음: {out.reason}"
    return out


def describe(v: vp.Video, env: Mapping[str, str], store: InsightStore) -> VideoInsightsOutput:
    got = store.get(v.key)
    out = got if got is not None else placeholder(v, env, store)
    out.campaign_id = v.campaign_id or out.campaign_id
    if v.platform in ("youtube", "instagram"):
        out.needs = [s for s in sources(env, store, v.platform) if s.status != "connected"]
    return out


def video_insights(
    request: VideoInsightsRequest, *, env: Mapping[str, str],
    videos: vp.VideoStore | None = None, store: InsightStore | None = None,
    now: datetime | None = None,
) -> InsightsResult:  # fmt: skip
    """저장분만 읽는다. list_all이면 그 회사 등록 영상 전부와 출처 연결 상태."""
    now = now or datetime.now(UTC)
    own_v, own_s = videos is None, store is None
    videos = videos or vp.store_of(env)
    store = store or store_of(env)
    try:
        if request.list_all:
            rows = [describe(v, env, store) for v in videos.videos()]
            out = VideoInsightsOutput(videos=rows, needs=sources(env, store),
                                      status="known" if any(r.status == "known" for r in rows)
                                      else "unknown")  # fmt: skip
            known = sum(r.status == "known" for r in rows)
            out.summary = f"영상 {len(rows)}개 중 깊은 지표 {known}개"
            if out.status == "unknown":
                out.reason = "아직 깊은 지표를 저장한 영상이 없습니다"
        else:
            found = _find(request, videos)
            if found is None:
                why = "등록된 영상이 없음 (video_performance에 영상 주소를 먼저 등록)"
                out = VideoInsightsOutput(reason=why, needs=sources(env, store))
                out.summary = f"깊은 지표 없음: {out.reason}"
            else:
                out = describe(found, env, store)
        return InsightsResult(out, Evidence(queries=["video_performance.db insights (저장분)"],
                                            data_summary=out.summary, collected_at=now))
    finally:
        if own_v:
            videos.close()
        if own_s:
            store.close()


def _find(request: VideoInsightsRequest, videos: vp.VideoStore) -> vp.Video | None:
    if request.video_url:
        parsed = vp.parse_video(request.video_url)
        if parsed is None:
            return None
        return videos.find(key=f"{parsed[0]}:{parsed[1]}")
    return videos.find(campaign_id=request.campaign_id)
