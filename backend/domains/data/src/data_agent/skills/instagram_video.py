"""인스타그램 게시 영상 성과 (11/23, 의뢰 영상 게시 → 성과 → 다음 기획). 읽기 전용.

- 대상: 회사 설정(tenant.toml) `[instagram] publish_username` 계정 = 우리 게시 계정
- 읽기: 시점(1시간·1일·3일·7일)이 된 영상이 있으면 그 계정을 business_discovery로 **한 번** 읽는다
  (최근 미디어 50개, 경쟁사 읽기와 같은 연결·같은 토큰·같은 하루 상한·장부). 같은 계정 영상은 그
  한 번으로 같이 잰다. 게시물은 영상 주소(permalink)의 shortcode로 찾는다
- 값: view_count(릴스)·like_count·comments_count를 유튜브와 같은 시점·허용 창 규칙으로 저장한다.
  게시 시각은 게시물 timestamp (처음 읽을 때 채운다)
- 비교 기준: 같은 계정 최근 릴스(이 영상 빼고, 최대 20개) view_count 중앙값. 그 시점에 잰 값을
  같이 저장하고, 3개 미만이면 low_sample(배수 없음). 유튜브 숏츠 통계 기준은 쓰지 않는다
- 못 잼: publish_username 없음 · 최근 50개에서 못 찾음 · 프로페셔널 계정 아님 → unknown + 이유
"""

from __future__ import annotations

import statistics
import tomllib
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template.tenant import setting as tenant_setting
from ax_contracts.schemas.data import DataFact, VideoCheckpoint, VideoPerformanceOutput
from data_agent.connectors import ConnectorUnavailable
from data_agent.skills import video_performance as vp

TENANT_CONFIG_ENV = "DATA_TENANT_CONFIG"
SECTION, KEY = "instagram", "publish_username"
TOKEN_ENV, USER_ENV = "DATA_IG_ACCESS_TOKEN", "DATA_IG_USER_ID"  # connectors/instagram_graph.py
MEDIA_LIMIT = 50
BASELINE_MAX = 20
LOW_SAMPLE = 3
RETRY_AFTER = timedelta(minutes=55)  # 수집기(1시간마다)는 다음 회차에 다시 본다
SOURCE = "instagram business_discovery"
NO_USERNAME = "회사 설정에 게시 계정 아이디가 없음 ([instagram] publish_username)"
NOT_FOUND = "게시물을 최근 50개에서 찾지 못함"
NOT_PRO = "프로페셔널 계정이 아님"
SALES_LIMIT = "매출은 프로모션 링크가 없어 측정 불가"


def connected(env: Mapping[str, str]) -> bool:
    """데이터 인스타 토큰·계정 id가 있음 (없으면 예전처럼 연결 대기)."""
    return bool(env.get(TOKEN_ENV) and env.get(USER_ENV))


def publish_username(env: Mapping[str, str]) -> str | None:
    """회사 설정 [instagram] publish_username (@ 없이). 없거나 읽지 못하면 None."""
    path = env.get(TENANT_CONFIG_ENV) or tenant_setting(TENANT_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return None
    try:
        with Path(path).open("rb") as f:
            section = tomllib.load(f).get(SECTION)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    value = section.get(KEY) if isinstance(section, dict) else None
    if not isinstance(value, str):
        return None
    name = value.strip().lstrip("@")
    return name or None


def shortcode_of(url: object) -> str | None:
    parsed = vp.parse_video(url) if isinstance(url, str) else None
    return parsed[1] if parsed and parsed[0] == "instagram" else None


def baseline_of(media: Sequence[Mapping[str, Any]], exclude: str) -> tuple[float | None, int]:
    """우리 릴스(이 영상 빼고, 최근 20개) 조회 중앙값과 표본 수. 3개 미만이면 중앙값 None."""
    views = [
        m["view_count"]
        for m in media
        if m.get("media_product_type") == "REELS"
        and shortcode_of(m.get("permalink")) != exclude
        and isinstance(m.get("view_count"), int)
    ][:BASELINE_MAX]
    if len(views) < LOW_SAMPLE:
        return None, len(views)
    return float(statistics.median(views)), len(views)


def _when(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _int(raw: object) -> int | None:
    return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0 else None


async def measure_due(
    store: vp.VideoStore, graph: Any, env: Mapping[str, str], now: datetime
) -> tuple[int, list[str]]:
    """잴 시점이 된(또는 게시 시각을 아직 모르는) 인스타 영상을 계정 한 번 읽기로 잰다.

    (저장한 시점 값 수, 문제). 연결이 없거나 게시 계정이 없으면 부르지 않는다 (이유는 화면에)."""
    want: list[vp.Video] = []
    for v in store.videos():
        if v.platform != "instagram":
            continue
        noted = store.note_at(v.key)
        if noted is not None and now - noted[1] < RETRY_AFTER:
            continue  # 못 찾음·프로페셔널 아님은 한 시간 안에 다시 읽지 않는다 (화면 읽기마다)
        done = set(store.points(v.key))
        if v.published_at is None or vp.due_checkpoints(v.published_at, now, done):
            want.append(v)
    username = publish_username(env)
    if not want or graph is None or not getattr(graph, "configured", False) or not username:
        return 0, []
    try:
        media = await graph.business_media(username, MEDIA_LIMIT)
    except ConnectorUnavailable as error:
        if error.code == "NOT_FOUND":  # 개인 계정·없는 계정은 business_discovery가 거절한다
            for v in want:
                store.set_note(v.key, NOT_PRO, now)
            return 0, [f"@{username}: {NOT_PRO}"]
        if error.code == "NO_DATA":
            media = []
        else:
            return 0, [f"인스타 성과를 재지 못함 [{error.code}]"]
    by_code = {shortcode_of(m.get("permalink")): m for m in media}
    saved = 0
    for v in want:
        m = by_code.get(v.video_id)
        if m is None:
            store.set_note(v.key, NOT_FOUND, now)
            continue
        store.set_note(v.key, None)
        published = v.published_at or _when(m.get("timestamp"))
        if published is None:
            store.set_note(v.key, "게시물에 게시 시각이 없음", now)
            continue
        if v.published_at is None:
            store.set_published(v.key, published)
        due = vp.due_checkpoints(published, now, set(store.points(v.key)))
        median, n = baseline_of(media, v.video_id)
        for c in due:
            store.save_point(v.key, vp.Point(c, now, _int(m.get("view_count")),
                                             _int(m.get("like_count")),
                                             _int(m.get("comments_count"))))  # fmt: skip
            store.save_baseline(v.key, c, median, n)
            saved += 1
    return saved, []


# ---------------------------------------------------------------- 응답


def basis_of(username: str | None, median: float | None, n: int | None) -> str:
    who = f"@{username}" if username else "우리 게시 계정"
    if n is None:
        return f"{who} 최근 릴스 조회 중앙값 (아직 재지 않음)"
    if median is None:
        return f"표본 적음(low_sample): {who} 최근 릴스 {n}개 (3개 미만이라 배수 없음)"
    return f"{who} 최근 릴스 {n}개 조회 중앙값 (이 영상 빼고, 최대 20개)"


def line_of(c: VideoCheckpoint) -> str:
    """화면 한 줄: "1일 · 1,234회 · 좋아요 56 · 우리 릴스 대비 1.20배"."""
    if c.status != "known" or c.views is None:
        return f"{vp.NAMES[c.checkpoint]} · {c.reason or vp.MEASURING}"
    parts = [vp.NAMES[c.checkpoint], f"{c.views:,}회"]
    if c.likes is not None:
        parts.append(f"좋아요 {c.likes:,}")
    if c.peer_ratio is not None:
        parts.append(f"우리 릴스 대비 {c.peer_ratio:.2f}배")
    return " · ".join(parts)


def describe(
    out: VideoPerformanceOutput, video: vp.Video, store: vp.VideoStore,
    env: Mapping[str, str], now: datetime,
) -> None:  # fmt: skip
    """인스타 영상 하나: 시점 값·우리 릴스 대비 배수·facts·이유 (숫자는 모두 저장분)."""
    username = publish_username(env)
    out.tracking = True
    points = store.points(video.key)
    bases = store.baselines(video.key)
    note = store.note(video.key)
    out.checkpoints = []
    for c in vp.CHECKPOINTS:
        p = points.get(c)
        median, _n = bases.get(c, (None, 0))
        if p is not None and video.published_at is not None:
            out.checkpoints.append(vp.checkpoint_values(video.published_at, p, None, median))
        elif not username:
            out.checkpoints.append(VideoCheckpoint(checkpoint=c, reason=NO_USERNAME))
        elif video.published_at is None:
            out.checkpoints.append(VideoCheckpoint(checkpoint=c, reason=note or vp.MEASURING))
        else:
            out.checkpoints.append(vp.pending_checkpoint(c, video.published_at, now,
                                                         video.registered_at))  # fmt: skip
    last_base = next((bases[c] for c in reversed(vp.CHECKPOINTS) if c in bases), None)
    out.peer_median_views = last_base[0] if last_base else None
    out.peer_basis = basis_of(username, *(last_base or (None, None)))
    out.facts = facts_of(video, out.checkpoints, bases)
    known = [c for c in out.checkpoints if c.status == "known"]
    out.status = "known" if known else "unknown"
    out.reason = None if known else (NO_USERNAME if not username else
                                     note or out.checkpoints[0].reason or vp.MEASURING)  # fmt: skip
    out.limits = [
        f"인스타 성과는 공식 API business_discovery로 {'@' + username if username else '게시 계정'}"
        "을 시점마다 한 번 읽어 잰다 (최근 50개)",
        "비교 기준은 같은 계정 최근 릴스(이 영상 빼고, 최대 20개) 조회 중앙값이다. 3개 미만이면"
        " low_sample. 유튜브 숏츠 통계 기준은 쓰지 않는다",
        SALES_LIMIT,
    ]
    if video.test:
        out.limits.append("시험 영상: 화면·브리프 집계(성과 모으기)에서 뺀다")


def finish_lines(out: VideoPerformanceOutput) -> None:
    for c in out.checkpoints:
        c.line = line_of(c)
    known = [c for c in out.checkpoints if c.status == "known"]
    out.summary = (known[-1].line or "") if known else f"영상 성과 없음: {out.reason}"


def facts_of(
    video: vp.Video, cps: Sequence[VideoCheckpoint],
    bases: Mapping[str, tuple[float | None, int]],
) -> list[DataFact]:  # fmt: skip
    rows: list[DataFact] = []

    def add(label: str, value: float | None, unit: str, source: str, period: str | None) -> str:
        if value is None:
            return ""
        fid = vp.fact_id(video.key, label)
        rows.append(DataFact(fact_id=fid, label=label, value=float(value), unit=unit,
                             source=source, link=video.video_url, period=period,
                             about=video.key))  # fmt: skip
        return fid

    for c in cps:
        if c.status != "known":
            continue
        at = c.measured_at.isoformat() if c.measured_at else None
        median, n = bases.get(c.checkpoint, (None, 0))
        ids = [
            add(f"{c.checkpoint} 조회수", c.views, "회", SOURCE, at),
            add(f"{c.checkpoint} 좋아요", c.likes, "개", SOURCE, at),
            add(f"{c.checkpoint} 댓글", c.comments, "개", SOURCE, at),
            add(f"{c.checkpoint} 우리 릴스 조회 중앙값 (표본 {n})", median, "회", SOURCE, at),
            add(f"{c.checkpoint} 우리 릴스 대비 배수", c.peer_ratio, "배", "code", at),
        ]
        c.fact_ids = [i for i in ids if i]
    return rows
