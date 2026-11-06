"""video_performance: 게시한 영상의 성과 (게시 후 1시간·1일·3일·7일). 읽기 전용(grade read).

- 등록: 요청에 video_url이 있으면 영상을 추적 목록(`video_performance.db`)에 넣는다 (campaign_id·
  insight_id·proposal_id·test). campaign_id만 오면 등록된 영상을 찾는다
- 측정(코드): 시점(게시 시각 + 1시간·1일·3일·7일)이 지나고 허용 창(`WINDOW_HOURS`) 안이면
  YouTube `videos.list`로 조회수·좋아요·댓글을 받아 저장한다. 수집기(1시간마다)와 skill 요청이 같은
  함수를 부른다. YouTube 장부·하루 상한 안에서, 영상 50개당 1단위(영상당 1단위 이하)
- 시점이 아직이면 unknown + "측정 중", 창을 넘겨 못 쟀으면 unknown + "놓침"
- 배수: 같은 카테고리·형식 숏츠 조회 중앙값(숏츠 통계 저장분) 대비는 모든 시점, 목표 참고값(그
  인사이트의 target_views_reference) 대비는 목표 참고값이 정의된 시점(`TARGET_AT`, 7일)에만.
  숫자는 모두 facts에, 시점마다 화면 한 줄(`line`)
- 인스타그램·틱톡: 읽는 길이 없어 unknown + "연결 대기"와 필요한 것
- 시험 영상(test)은 화면·브리프 집계(성과 모으기)에서 뺀다
- 성과 모으기는 시점(1일·3일·7일)마다 따로: 그 시점을 잰 영상만 넣고, 측정 중·놓침은 빠진 수로
  센다. n이 3보다 작으면 low_sample
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import statistics
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    DataFact,
    ShortsStatsOutput,
    TrendInsight,
    VideoCheckpoint,
    VideoCheckpointName,
    VideoPerformanceGroup,
    VideoPerformanceHorizonGroup,
    VideoPerformanceOutput,
    VideoPerformanceRequest,
    VideoPlatform,
)
from data_agent.connectors import ConnectorUnavailable

DB_ENV = "DATA_VIDEO_PERF_DB"
DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "video_performance.db"  # backend/.data
SCHEMA_VERSION = "video_performance.v2"  # v2: 목표 대비는 7일에만, 시점별 성과 모으기, line
# 게시 후 시간과, 그 시점이 지난 뒤 이 시간 안에 재면 그 시점 값으로 보는 허용 창
# (수집기가 1시간마다라 1시간 시점의 창은 1시간)
CHECKPOINTS: dict[VideoCheckpointName, int] = {"1h": 1, "1d": 24, "3d": 72, "7d": 168}
WINDOW_HOURS: dict[VideoCheckpointName, int] = {"1h": 1, "1d": 2, "3d": 3, "7d": 4}
NAMES: dict[VideoCheckpointName, str] = {"1h": "1시간", "1d": "1일", "3d": "3일", "7d": "7일"}
# 목표 참고값(인사이트 target_views_reference)이 정의된 시점. 목표 대비 배수는 이 시점에만 낸다
TARGET_AT: VideoCheckpointName = "7d"
HORIZONS: tuple[VideoCheckpointName, ...] = ("1d", "3d", "7d")  # 성과 모으기 시점
LOW_SAMPLE = 3  # 집계에 들어간 영상이 이보다 적으면 low_sample
MEASURING = "측정 중"
MISSED = "놓침"
WAITING = "연결 대기"
NEEDS: dict[str, list[str]] = {
    "instagram": [
        "인스타그램 비즈니스 계정과 Instagram Graph API 접근 토큰 (instagram_basic·"
        "instagram_manage_insights 권한)",
        "게시물 id(media id) 또는 게시물 주소를 계정 미디어 목록과 잇는 방법",
    ],
    "tiktok": [
        "틱톡 비즈니스 계정과 TikTok API 접근 토큰 (video.list 권한)",
        "영상 id를 계정 영상 목록과 잇는 방법",
    ],
}

_YT = (
    re.compile(r"youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|live/|embed/)([A-Za-z0-9_-]{11})"),
    re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})"),
)
_IG = re.compile(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)")
_TT = re.compile(r"tiktok\.com/(?:@[^/]+/video|v)/(\d+)")


def parse_video(url: str) -> tuple[VideoPlatform, str] | None:
    """영상 주소 → (플랫폼, 영상 id). 모르는 주소면 None."""
    for pattern in _YT:
        if m := pattern.search(url):
            return "youtube", m.group(1)
    if m := _IG.search(url):
        return "instagram", m.group(1)
    if m := _TT.search(url):
        return "tiktok", m.group(1)
    return None


def _platform(value: str) -> VideoPlatform:
    for p in get_args(VideoPlatform):
        if p == value:
            return p
    raise ValueError(f"모르는 플랫폼 {value}")


def _checkpoint(value: str) -> VideoCheckpointName:
    for c in CHECKPOINTS:
        if c == value:
            return c
    raise ValueError(f"모르는 시점 {value}")


# ---------------------------------------------------------------- 저장


@dataclass
class Video:
    key: str  # 플랫폼:영상 id
    platform: VideoPlatform
    video_id: str
    video_url: str
    campaign_id: str | None
    insight_id: str | None
    proposal_id: str | None
    test: bool
    published_at: datetime | None
    title: str | None
    registered_at: datetime
    category: str | None
    shorts_format: str | None


@dataclass
class Point:
    checkpoint: VideoCheckpointName
    measured_at: datetime
    views: int | None
    likes: int | None
    comments: int | None


class VideoStore:
    """추적 목록(영상)과 시점 값."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS videos (key TEXT PRIMARY KEY, platform TEXT NOT NULL,"
                " video_id TEXT NOT NULL, video_url TEXT NOT NULL, campaign_id TEXT,"
                " insight_id TEXT, proposal_id TEXT, test INTEGER NOT NULL DEFAULT 0,"
                " published_at TEXT, title TEXT, registered_at TEXT NOT NULL, category TEXT,"
                " shorts_format TEXT);"
                "CREATE TABLE IF NOT EXISTS points (key TEXT NOT NULL, checkpoint TEXT NOT NULL,"
                " measured_at TEXT NOT NULL, views INTEGER, likes INTEGER, comments INTEGER,"
                " PRIMARY KEY (key, checkpoint));"
            )

    def close(self) -> None:
        self._conn.close()

    def upsert(self, v: Video) -> None:
        """등록 (있으면 비어 있던 칸만 채운다. 시험 표시는 한 번 붙으면 그대로)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(key) DO UPDATE"
                " SET campaign_id = COALESCE(videos.campaign_id, excluded.campaign_id),"
                " insight_id = COALESCE(videos.insight_id, excluded.insight_id),"
                " proposal_id = COALESCE(videos.proposal_id, excluded.proposal_id),"
                " test = MAX(videos.test, excluded.test),"
                " published_at = COALESCE(videos.published_at, excluded.published_at),"
                " title = COALESCE(videos.title, excluded.title),"
                " category = COALESCE(videos.category, excluded.category),"
                " shorts_format = COALESCE(videos.shorts_format, excluded.shorts_format)",
                (v.key, v.platform, v.video_id, v.video_url, v.campaign_id, v.insight_id,
                 v.proposal_id, int(v.test), _iso(v.published_at), v.title,
                 v.registered_at.isoformat(), v.category, v.shorts_format),  # fmt: skip
            )

    def videos(self, *, include_test: bool = True) -> list[Video]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM videos ORDER BY registered_at").fetchall()
        out = [_video(r) for r in rows]
        return out if include_test else [v for v in out if not v.test]

    def find(self, *, key: str | None = None, campaign_id: str | None = None) -> Video | None:
        with self._lock:
            if key is not None:
                row = self._conn.execute("SELECT * FROM videos WHERE key = ?", (key,)).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT * FROM videos WHERE campaign_id = ? ORDER BY registered_at DESC",
                    (campaign_id,),
                ).fetchone()
        return _video(row) if row else None

    def points(self, key: str) -> dict[VideoCheckpointName, Point]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT checkpoint, measured_at, views, likes, comments FROM points"
                " WHERE key = ?",
                (key,),
            ).fetchall()
        out: dict[VideoCheckpointName, Point] = {}
        for c, at, v, lk, cm in rows:
            name = _checkpoint(c)
            out[name] = Point(name, datetime.fromisoformat(at), v, lk, cm)
        return out

    def save_point(self, key: str, p: Point) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO points VALUES (?,?,?,?,?,?)",
                (key, p.checkpoint, p.measured_at.isoformat(), p.views, p.likes, p.comments),
            )


def _iso(at: datetime | None) -> str | None:
    return at.astimezone(UTC).isoformat() if at else None


def _video(r: Sequence[Any]) -> Video:
    return Video(
        key=r[0], platform=_platform(r[1]), video_id=r[2], video_url=r[3], campaign_id=r[4],
        insight_id=r[5], proposal_id=r[6], test=bool(r[7]),
        published_at=datetime.fromisoformat(r[8]) if r[8] else None, title=r[9],
        registered_at=datetime.fromisoformat(r[10]), category=r[11], shorts_format=r[12],
    )  # fmt: skip


def store_of(env: Mapping[str, str]) -> VideoStore:
    return VideoStore(env.get(DB_ENV) or DEFAULT_DB)


# ---------------------------------------------------------------- 측정 (코드)


def due_checkpoints(
    published_at: datetime, now: datetime, done: set[VideoCheckpointName]
) -> list[VideoCheckpointName]:
    """지금 재야 할 시점 (지났고 허용 창 안이고 아직 안 잰 것)."""
    age = now - published_at
    return [
        c for c, h in CHECKPOINTS.items()
        if c not in done and timedelta(hours=h) <= age < timedelta(hours=h + WINDOW_HOURS[c])
    ]  # fmt: skip


async def measure_due(
    store: VideoStore, youtube: Any, now: datetime | None = None
) -> tuple[int, list[str]]:
    """추적 중인 YouTube 영상 중 지금 잴 시점이 있는 것만 videos.list로 잰다. (잰 값 수, 문제)."""
    now = now or datetime.now(UTC)
    want: dict[str, tuple[Video, list[VideoCheckpointName]]] = {}
    for v in store.videos():
        if v.platform != "youtube" or v.published_at is None:
            continue
        due = due_checkpoints(v.published_at, now, set(store.points(v.key)))
        if due:
            want[v.video_id] = (v, due)
    if not want or youtube is None or not hasattr(youtube, "videos"):
        return 0, []
    try:
        got = await youtube.videos(list(want))
    except ConnectorUnavailable as error:
        return 0, [f"영상 성과를 재지 못함 [{error.code}]"]
    saved = 0
    for stat in got:
        if stat.video_id not in want:
            continue
        v, due = want[stat.video_id]
        for c in due:
            store.save_point(v.key, Point(c, now, stat.view_count, stat.like_count,
                                          stat.comment_count))  # fmt: skip
            saved += 1
    gone = sorted(set(want) - {s.video_id for s in got})
    return saved, [f"응답에 없는 영상(지워짐·비공개) {', '.join(gone)}"] if gone else []


# ---------------------------------------------------------------- 비교 기준


def find_insight(env: Mapping[str, str], insight_id: str, days: int = 14) -> TrendInsight | None:
    """최근 브리프 저장분에서 그 인사이트 (예전 id로도 찾는다). 없으면 None."""
    from data_agent.skills.trend_insight_brief import DB_ENV as BRIEF_DB_ENV
    from data_agent.skills.trend_insight_brief import DEFAULT_DB as BRIEF_DB

    path = Path(env.get(BRIEF_DB_ENV) or BRIEF_DB)
    if not path.is_file():
        return None
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        rows = conn.execute(
            "SELECT output FROM briefs ORDER BY day DESC, created_at DESC LIMIT ?", (days * 3,)
        ).fetchall()
    finally:
        conn.close()
    for (raw,) in rows:
        for i in json.loads(raw).get("insights", []):
            ids = [i.get("insight_id"), *(i.get("legacy_insight_ids") or [])]
            if insight_id in ids:
                return TrendInsight.model_validate(i)
    return None


def peer_median(
    shorts: ShortsStatsOutput | None, category: str | None, shorts_format: str | None
) -> tuple[float | None, str]:
    """같은 형식(먼저)·카테고리 숏츠 조회 중앙값과 근거. 표본이 known이 아니면 다음 것."""
    if shorts is None:
        return None, "숏츠 통계 저장분이 없음"
    if not category and not shorts_format:
        return None, "insight_id가 없어 같은 카테고리·형식을 모름"
    for kind, name, groups in (("형식", shorts_format, shorts.by_format),
                               ("카테고리", category, shorts.by_category)):  # fmt: skip
        g = next((x for x in groups if name and x.name == name), None)
        if g is not None and g.status == "known" and g.median_views:
            at = shorts.snapshot_at.isoformat() if shorts.snapshot_at else "시각 모름"
            return g.median_views, f"숏츠 통계 {kind} '{name}' 조회 중앙값 (표본 {g.count}개, {at})"
    return None, "숏츠 통계에 그 형식·카테고리의 중앙값이 없음 (표본 부족)"


# ---------------------------------------------------------------- 시점 값 (코드)


def fact_id(about: str, label: str) -> str:
    return "f_" + hashlib.sha256(f"{about}|{label}".encode()).hexdigest()[:12]


def _rate(part: int | None, views: int | None) -> float | None:
    return round(part / views, 4) if part is not None and views else None


def checkpoint_values(
    published_at: datetime, p: Point, target: float | None, peer: float | None
) -> VideoCheckpoint:
    """시점 하나의 값과 배수. 숨긴 값·0 조회는 비율을 내지 않는다 (0으로 채우지 않음)."""
    hours = max((p.measured_at - published_at).total_seconds() / 3600, 0.0)
    views = p.views
    return VideoCheckpoint(
        checkpoint=p.checkpoint,
        due_at=published_at + timedelta(hours=CHECKPOINTS[p.checkpoint]),
        status="known" if views is not None else "unknown",
        reason=None if views is not None else "조회수를 받지 못함",
        measured_at=p.measured_at, hours_after=round(hours, 2),
        views=views, likes=p.likes, comments=p.comments,
        views_per_hour=round(views / hours, 2) if views is not None and hours > 0 else None,
        like_rate=_rate(p.likes, views), comment_rate=_rate(p.comments, views),
        target_ratio=round(views / target, 3)
        if views is not None and target and p.checkpoint == TARGET_AT else None,
        peer_ratio=round(views / peer, 3) if views is not None and peer else None,
    )  # fmt: skip


def peer_kind(basis: str | None) -> str | None:
    """같은 숏츠 중앙값이 형식 것인지 카테고리 것인지 (peer_median 근거에서)."""
    for kind in ("형식", "카테고리"):
        if basis and f"숏츠 통계 {kind} '" in basis:
            return kind
    return None


def line_of(c: VideoCheckpoint, kind: str | None) -> str:
    """화면 한 줄: "1일 · 33,263회 · 같은 형식 대비 16.63배". 기준값이 없으면 숫자만."""
    if c.status != "known" or c.views is None:
        return f"{NAMES[c.checkpoint]} · {c.reason or MEASURING}"
    parts = [NAMES[c.checkpoint], f"{c.views:,}회"]
    if c.target_ratio is not None:
        parts.append(f"목표 대비 {c.target_ratio:.2f}배")
    if c.peer_ratio is not None:
        parts.append(f"같은 {kind or '숏츠'} 대비 {c.peer_ratio:.2f}배")
    return " · ".join(parts)


def pending_checkpoint(
    c: VideoCheckpointName, published_at: datetime | None, now: datetime, registered_at: datetime
) -> VideoCheckpoint:
    """아직 값이 없는 시점: 측정 중 또는 놓침."""
    if published_at is None:
        return VideoCheckpoint(checkpoint=c, reason="게시 시각을 아직 모름")
    due = published_at + timedelta(hours=CHECKPOINTS[c])
    end = due + timedelta(hours=WINDOW_HOURS[c])
    if now < due:
        why = f"{MEASURING} ({(due - now).total_seconds() / 3600:.1f}시간 뒤 잼)"
    elif now < end:
        why = f"{MEASURING} (다음 측정에서 잼)"
    elif registered_at > end:
        why = f"{MISSED}: 등록 전에 그 시점이 지남"
    else:
        why = f"{MISSED}: 그 시점에 재지 못함 (데이터 끊김)"
    return VideoCheckpoint(checkpoint=c, due_at=due, reason=why)


def facts_of(
    v: Video, cps: Sequence[VideoCheckpoint], target: float | None, peer: float | None
) -> list[DataFact]:
    """응답 숫자 전부 (시점마다 fact_ids로 가리킨다)."""
    rows: list[DataFact] = []

    def add(label: str, value: float | None, unit: str, source: str, period: str | None) -> str:
        if value is None:
            return ""
        fid = fact_id(v.key, label)
        rows.append(
            DataFact(
                fact_id=fid, label=label, value=float(value), unit=unit, source=source,
                link=v.video_url, period=period, about=v.key,
            )
        )  # fmt: skip
        return fid

    add("목표 참고값 (숏츠 조회 중앙값)", target, "회", "trend_insight_brief", None)
    add("같은 카테고리·형식 숏츠 조회 중앙값", peer, "회", "shorts_stats", None)
    for c in cps:
        if c.status != "known":
            continue
        at = c.measured_at.isoformat() if c.measured_at else None
        ids = [
            add(f"{c.checkpoint} 조회수", c.views, "회", "youtube", at),
            add(f"{c.checkpoint} 좋아요", c.likes, "개", "youtube", at),
            add(f"{c.checkpoint} 댓글", c.comments, "개", "youtube", at),
            add(f"{c.checkpoint} 시간당 조회", c.views_per_hour, "회/시간", "code", at),
            add(f"{c.checkpoint} 좋아요 비율", c.like_rate, "비율", "code", at),
            add(f"{c.checkpoint} 댓글 비율", c.comment_rate, "비율", "code", at),
            add(f"{c.checkpoint} 목표 대비 배수", c.target_ratio, "배", "code", at),
            add(f"{c.checkpoint} 같은 숏츠 중앙값 대비 배수", c.peer_ratio, "배", "code", at),
        ]
        c.fact_ids = [i for i in ids if i]
    return rows


def checkpoints_of(
    video: Video, points: Mapping[VideoCheckpointName, Point], now: datetime,
    target: float | None, peer: float | None,
) -> list[VideoCheckpoint]:  # fmt: skip
    """시점 네 개: 잰 값이면 값과 배수, 아니면 측정 중·놓침."""
    out: list[VideoCheckpoint] = []
    for c in CHECKPOINTS:
        p = points.get(c)
        if p is not None and video.published_at is not None:
            out.append(checkpoint_values(video.published_at, p, target, peer))
        else:
            out.append(pending_checkpoint(c, video.published_at, now, video.registered_at))
    return out


# ---------------------------------------------------------------- skill


@dataclass
class PerformanceResult:
    output: VideoPerformanceOutput
    evidence: Evidence


def _target_of(env: Mapping[str, str], insight_id: str | None) -> tuple[TrendInsight | None, str]:
    if not insight_id:
        return None, "insight_id가 없어 목표 참고값을 모름"
    found = find_insight(env, insight_id)
    if found is None:
        return None, f"최근 14일 브리프에 인사이트 {insight_id}가 없음"
    if found.target_views_reference is None:
        why = found.shorts_fit_reason or "이유 없음"
        return found, f"인사이트 {insight_id}에 목표 참고값이 없음 ({why})"
    return found, f"인사이트 '{found.topic}' ({insight_id}): {found.target_reference_basis}"


def _done(
    out: VideoPerformanceOutput, queries: list[str], now: datetime, store: VideoStore,
    env: Mapping[str, str], shorts: ShortsStatsOutput | None,
) -> PerformanceResult:  # fmt: skip
    """결과 마무리: 성과 모으기(시점별, 시험 영상 제외)와 예전 표(목표 시점 줄)를 같이 준다."""
    out.performance_by_horizon = performance_by_horizon(store, env, now, shorts)
    out.performance_table = table_of(out.performance_by_horizon)
    return PerformanceResult(out, Evidence(queries=queries, data_summary=out.summary,
                                           collected_at=now))  # fmt: skip


async def video_performance(
    request: VideoPerformanceRequest,
    youtube: Any,
    *,
    env: Mapping[str, str],
    shorts: ShortsStatsOutput | None = None,
    store: VideoStore | None = None,
    now: datetime | None = None,
) -> PerformanceResult:
    """영상 성과. video_url이 있으면 등록(추적)하고, 지금 잴 시점이 있으면 잰다."""
    now = now or datetime.now(UTC)
    own = store is None
    store = store or store_of(env)
    queries: list[str] = []
    out = VideoPerformanceOutput(campaign_id=request.campaign_id, insight_id=request.insight_id,
                                 proposal_id=request.proposal_id, test=request.test,
                                 schema_version=SCHEMA_VERSION)  # fmt: skip
    try:
        video = await _register(request, youtube, store, env, now, out, queries)
        if video is None:
            out.summary = f"영상 성과 없음: {out.reason}"
            return _done(out, queries, now, store, env, shorts)
        out.platform, out.video_id, out.video_url = video.platform, video.video_id, video.video_url
        out.campaign_id = out.campaign_id or video.campaign_id
        out.insight_id = out.insight_id or video.insight_id
        out.proposal_id = out.proposal_id or video.proposal_id
        out.test, out.title, out.published_at = video.test, video.title, video.published_at
        if video.platform != "youtube":
            out.reason = f"{WAITING}: {video.platform} 성과를 읽는 연결이 아직 없음"
            out.needs = list(NEEDS[video.platform])
            out.checkpoints = [VideoCheckpoint(checkpoint=c, reason=WAITING) for c in CHECKPOINTS]
            out.summary = f"영상 성과 없음: {out.reason}"
            return _done(out, queries, now, store, env, shorts)
        out.tracking = True
        n, problems = await measure_due(store, youtube, now)
        if n:
            queries.append(f"youtube videos.list video_performance points={n}")
        out.limits += problems
        insight, why = _target_of(env, out.insight_id)
        out.target_views_reference = insight.target_views_reference if insight else None
        out.target_reference_basis = why
        out.category = video.category or (insight.category if insight else None)
        out.shorts_format = video.shorts_format or (insight.shorts_format if insight else None)
        out.peer_median_views, out.peer_basis = peer_median(shorts, out.category,
                                                            out.shorts_format)  # fmt: skip
        target, peer = out.target_views_reference, out.peer_median_views
        out.target_checkpoint = TARGET_AT
        out.checkpoints = checkpoints_of(video, store.points(video.key), now, target, peer)
        kind = peer_kind(out.peer_basis)
        for c in out.checkpoints:
            c.line = line_of(c, kind)
        out.facts = facts_of(video, out.checkpoints, out.target_views_reference,
                             out.peer_median_views)  # fmt: skip
        known = [c for c in out.checkpoints if c.status == "known"]
        out.status = "known" if known else "unknown"
        out.reason = None if known else (out.checkpoints[0].reason or MEASURING)
        out.limits.append("시점 값은 그 시점 뒤 허용 창(1시간 시점 1시간·1일 2시간·3일 3시간·"
                          "7일 4시간) 안에 잰 값이다 (hours_after)")  # fmt: skip
        out.limits.append("같은 숏츠 중앙값은 인기·검색 숏츠의 지금 조회수라 게시 후 시간이 다르다")
        out.limits.append(f"목표 대비 배수는 목표 참고값이 정의된 {NAMES[TARGET_AT]} 시점에만 낸다")
        if video.test:
            out.limits.append("시험 영상: 화면·브리프 집계(성과 모으기)에서 뺀다")
        last = known[-1] if known else None
        out.summary = (last.line or "") if last else f"영상 성과 {out.reason}"
        return _done(out, queries, now, store, env, shorts)
    finally:
        if own:
            store.close()


async def _register(
    request: VideoPerformanceRequest, youtube: Any, store: VideoStore, env: Mapping[str, str],
    now: datetime, out: VideoPerformanceOutput, queries: list[str],
) -> Video | None:  # fmt: skip
    """video_url이면 등록(처음이면 YouTube 게시 시각·제목을 1단위로 받는다), campaign_id면 찾기."""
    if not request.video_url:
        found = store.find(campaign_id=request.campaign_id)
        if found is None:
            out.reason = (f"캠페인 {request.campaign_id}에 등록된 영상이 없음 (마케팅 게시 알림에"
                          " 영상 주소가 아직 없음, video_url로 등록)")  # fmt: skip
        return found
    parsed = parse_video(request.video_url)
    if parsed is None:
        out.reason = "영상 주소를 알아보지 못함 (YouTube·인스타그램·틱톡 주소만)"
        return None
    platform, vid = parsed
    key = f"{platform}:{vid}"
    old = store.find(key=key)
    insight = find_insight(env, request.insight_id) if request.insight_id else None
    video = Video(
        key=key, platform=platform, video_id=vid, video_url=request.video_url,
        campaign_id=request.campaign_id, insight_id=request.insight_id,
        proposal_id=request.proposal_id, test=request.test, published_at=None, title=None,
        registered_at=old.registered_at if old else now,
        category=insight.category if insight else None,
        shorts_format=insight.shorts_format if insight else None,
    )  # fmt: skip
    if platform == "youtube" and (old is None or old.published_at is None):
        if youtube is None or not hasattr(youtube, "videos"):
            out.limits.append("YouTube 커넥터가 없어 게시 시각을 받지 못함")
        else:
            try:
                got = await youtube.videos([vid])
                queries.append("youtube videos.list video_performance register=1")
            except ConnectorUnavailable as error:
                got = []
                out.limits.append(f"게시 시각을 받지 못함 [{error.code}]")
            if got:
                video.published_at, video.title = got[0].published_at, got[0].title or None
            elif not out.limits:
                out.limits.append("YouTube 응답에 이 영상이 없음 (지워짐·비공개)")
    store.upsert(video)
    return store.find(key=key)


# ---------------------------------------------------------------- 성과 모으기 (표만)


def _median(values: list[float], digits: int) -> float | None:
    return round(statistics.median(values), digits) if values else None


def performance_by_horizon(
    store: VideoStore, env: Mapping[str, str], now: datetime,
    shorts: ShortsStatsOutput | None = None,
) -> list[VideoPerformanceHorizonGroup]:  # fmt: skip
    """인사이트·형식·카테고리별, 시점(1일·3일·7일)별 영상 성과. 시험 영상은 뺀다.

    한 줄에는 그 시점을 잰 영상만 넣는다 (시점을 섞지 않는다). 측정 중·놓침·게시 시각 모름은
    빠진 수로 센다. 목표 대비는 목표 시점(7일) 줄에만. 브리프 점수에는 넣지 않는다 (쌓는 중).
    """
    targets: dict[str, float | None] = {}
    groups: dict[tuple[str, str], list[dict[VideoCheckpointName, VideoCheckpoint]]] = {}
    for v in store.videos(include_test=False):
        target: float | None = None
        if v.insight_id:
            if v.insight_id not in targets:
                found = find_insight(env, v.insight_id)
                targets[v.insight_id] = found.target_views_reference if found else None
            target = targets[v.insight_id]
        peer, _ = peer_median(shorts, v.category, v.shorts_format)
        cps = {c.checkpoint: c for c in checkpoints_of(v, store.points(v.key), now, target, peer)}
        for key, value in (("insight", v.insight_id), ("format", v.shorts_format),
                           ("category", v.category)):  # fmt: skip
            if value:
                groups.setdefault((key, value), []).append(cps)
    out: list[VideoPerformanceHorizonGroup] = []
    for (key, value), videos in sorted(groups.items()):
        for h in HORIZONS:
            known = [c[h] for c in videos if c[h].status == "known" and c[h].views is not None]
            measuring = sum(c[h].status != "known" and (c[h].reason or "").startswith(MEASURING)
                            for c in videos)  # fmt: skip
            peers = [c.peer_ratio for c in known if c.peer_ratio is not None]
            tars = [c.target_ratio for c in known if c.target_ratio is not None]
            out.append(VideoPerformanceHorizonGroup(
                key=key, value=value, checkpoint=h, videos=len(videos), n=len(known),
                excluded=len(videos) - len(known), excluded_measuring=measuring,
                excluded_missed=len(videos) - len(known) - measuring,
                low_sample=len(known) < LOW_SAMPLE,
                median_views=_median([float(c.views or 0) for c in known], 1),
                median_peer_ratio=_median(peers, 3), peer_n=len(peers),
                median_target_ratio=_median(tars, 3), target_n=len(tars),
            ))  # fmt: skip
    return out


def table_of(rows: Sequence[VideoPerformanceHorizonGroup]) -> list[VideoPerformanceGroup]:
    """예전 표(performance_table): 목표 시점(7일) 줄만 옮긴다."""
    return [
        VideoPerformanceGroup(key=r.key, value=r.value, videos=r.videos, measured=r.target_n,
                              median_target_ratio=r.median_target_ratio)  # fmt: skip
        for r in rows if r.checkpoint == TARGET_AT
    ]


def performance_table(
    store: VideoStore, env: Mapping[str, str], now: datetime,
    shorts: ShortsStatsOutput | None = None,
) -> list[VideoPerformanceGroup]:  # fmt: skip
    """인사이트·형식·카테고리별 목표 대비 배수 (목표 시점 7일 값만). 시험 영상은 뺀다."""
    return table_of(performance_by_horizon(store, env, now, shorts))


def counted_videos(env: Mapping[str, str]) -> int:
    """등록된 영상 수 (시험 영상 제외). 저장 파일이 없으면 0 (만들지 않는다)."""
    path = env.get(DB_ENV) or DEFAULT_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return 0
    store = VideoStore(path)
    try:
        return len(store.videos(include_test=False))
    finally:
        store.close()
