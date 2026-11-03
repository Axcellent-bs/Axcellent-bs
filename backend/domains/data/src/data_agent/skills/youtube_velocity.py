"""youtube_velocity: YouTube 영상의 지금 속도(시간당 조회 증가). 읽기 전용(grade read).

- YouTube는 누적값만 줘서, 스냅샷 DB(`youtube_store`, #277)의 최근 두 수집 시각 사이 조회수 차이 ÷
  시간으로 "지금 속도"를 구한다. 셋째 스냅샷이 있으면 그 전 구간 속도와 비교해
  빨라졌는지(가속)도 준다.
- **판정하지 않는다.** 평소 속도 기준이 아직 없어서 "급상승" 같은 판정 단어를 쓰지 않고, 지금 속도가
  큰 순으로 상위 N개(회사 설정 `trends.toml` `[velocity] top`)만 보여 준다.
- 조회수가 빠진 영상(응답에 없음)은 속도를 비우고 순위에 넣지 않는다(0으로 채우지 않음).
  좋아요·댓글이 숨겨진 영상은 그 칸만 비운다. 최근 스냅샷에 없는 영상(지워짐·비공개·추적 끝)은 뺀다.
- 스냅샷이 2개 미만이면 unknown + 이유.
- 상위 영상에 저장된 제목·길이가 없으면(회사 키워드 검색·추적으로 들어온 영상) videos.list로
  한 번 받아 저장하고 붙인다(영상 50개마다 1단위). 받지 못하면 비워 두고 요약에 수를 적는다.
README "youtube_velocity".
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    YouTubeVelocityOutput,
    YouTubeVelocityRequest,
    YouTubeVideoVelocity,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.skills.trend_scan import TREND_CONFIG_ENV
from data_agent.youtube_store import (
    DB_ENV,
    DEFAULT_DB,
    SnapshotStore,
    StoredSnapshot,
    recent_snapshots,
    video_details,
    video_titles,
)

DEFAULT_TOP = 10


def top_setting(env: Mapping[str, str]) -> int:
    """회사 `trends.toml` `[velocity] top`. 없거나 틀리면 10."""
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return DEFAULT_TOP
    try:
        with Path(path).open("rb") as f:
            value = int((tomllib.load(f).get("velocity") or {}).get("top", DEFAULT_TOP))
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return DEFAULT_TOP
    return value if 1 <= value <= 50 else DEFAULT_TOP


def _per_hour(new: int | None, old: int | None, hours: float) -> float | None:
    if new is None or old is None or hours <= 0:
        return None
    return round((new - old) / hours, 1)


def velocities(snaps: list[StoredSnapshot]) -> tuple[list[YouTubeVideoVelocity], int, int]:
    """최근 스냅샷 영상별 속도와 (두 스냅샷 모두에 있는 영상 수, 조회수가 빠진 영상 수).

    `snaps`는 최근 것부터다.
    """
    latest, previous = snaps[0], snaps[1]
    earlier = snaps[2] if len(snaps) > 2 else None
    hours = (latest.collected_at - previous.collected_at).total_seconds() / 3600
    hours_before = (
        (previous.collected_at - earlier.collected_at).total_seconds() / 3600 if earlier else 0.0
    )
    out: list[YouTubeVideoVelocity] = []
    compared = missing = 0
    for vid, now in latest.videos.items():
        before = previous.videos.get(vid)
        if before is None:
            continue  # 새로 들어온 영상: 비교할 앞 값이 없다
        compared += 1
        speed = _per_hour(now.view_count, before.view_count, hours)
        if speed is None:
            missing += 1
        prev_speed = None
        if earlier is not None and (first := earlier.videos.get(vid)) is not None:
            prev_speed = _per_hour(before.view_count, first.view_count, hours_before)
        change = (
            round(speed - prev_speed, 1) if speed is not None and prev_speed is not None else None
        )
        out.append(
            YouTubeVideoVelocity(
                video_id=vid,
                channel_id=now.channel_id,
                published_at=now.published_at,
                popular_rank=now.popular_rank,
                tags=list(now.tags),
                views=now.view_count,
                likes=now.like_count,
                comments=now.comment_count,
                views_per_hour=speed,
                prev_views_per_hour=prev_speed,
                speed_change=change,
                faster=None if change is None else change > 0,
            )
        )
    return out, compared, missing


def with_names(db: str, rows: list[YouTubeVideoVelocity]) -> list[YouTubeVideoVelocity]:
    """화면 표시용 제목·채널 이름·길이를 붙인다 (저장된 값이 없으면 None, 지어내지 않음)."""
    ids = [r.video_id for r in rows]
    titles = video_titles(db, ids)
    details = video_details(db, ids)
    out = []
    for r in rows:
        d = details.get(r.video_id)
        out.append(
            r.model_copy(
                update={
                    "title": titles.get(r.video_id) or None,
                    "channel_title": d.channel_title if d else None,
                    "duration_seconds": d.duration_seconds if d else None,
                }
            )
        )
    return out


async def fill_names(
    db: str, rows: list[YouTubeVideoVelocity], youtube: Any, now: datetime | None = None
) -> tuple[list[YouTubeVideoVelocity], str | None]:
    """제목이나 길이가 비어 있는 영상을 videos.list로 받아 저장하고 다시 붙인다.

    (붙인 결과, 실패 이유). 커넥터가 없거나 받지 못하면 그대로 둔다(지어내지 않음).
    """
    ids = [r.video_id for r in rows if not r.title or r.duration_seconds is None]
    if not ids:
        return rows, None
    if youtube is None or not hasattr(youtube, "videos"):
        return rows, "YouTube 커넥터가 없어 제목을 받지 않음"
    try:
        stats = await youtube.videos(ids)
    except ConnectorUnavailable as error:
        return rows, f"제목 받기 실패 [{error.code}]"
    at = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    store = SnapshotStore(db)
    try:
        store.save_titles(at, [(s.video_id, s.title) for s in stats])
        store.save_details(at, stats)
    finally:
        store.close()
    return with_names(db, rows), None


async def measure_velocity_named(
    request: YouTubeVelocityRequest,
    *,
    env: Mapping[str, str],
    youtube: Any,
    now: datetime | None = None,
) -> VelocityResult:
    """measure_velocity + 비어 있는 제목·길이 채우기 (서버 핸들러가 쓴다)."""
    result = measure_velocity(request, env=env, now=now)
    out = result.output
    if out.status != "known" or not out.videos:
        return result
    db = env.get(DB_ENV) or str(DEFAULT_DB)
    videos, why = await fill_names(db, list(out.videos), youtube, now)
    missing = sum(1 for v in videos if not v.title)
    summary = out.summary
    if missing:
        summary += f" 제목을 받지 못한 영상 {missing}개" + (f" ({why})." if why else ".")
    output = out.model_copy(update={"videos": videos, "summary": summary})
    evidence = result.evidence.model_copy(update={"data_summary": summary})
    return VelocityResult(output, evidence)


@dataclass(frozen=True)
class VelocityResult:
    output: YouTubeVelocityOutput
    evidence: Evidence


def measure_velocity(
    request: YouTubeVelocityRequest, *, env: Mapping[str, str], now: datetime | None = None
) -> VelocityResult:
    """최근 스냅샷 3개로 영상별 지금 속도를 잰다. 판정하지 않는다. 상태를 바꾸지 않는다."""
    now = now or datetime.now(UTC)
    db = env.get(DB_ENV) or str(DEFAULT_DB)
    snaps = recent_snapshots(db, 3)
    query = f"youtube snapshots latest 3 db={Path(db).name}"
    if len(snaps) < 2:
        why = (
            "YouTube 스냅샷이 2개 미만이라 지금 속도를 잴 수 없음"
            f" (지금 {len(snaps)}개, 두 시각의 조회수 차이로 잰다)"
        )
        output = YouTubeVelocityOutput(
            status="unknown",
            unknown_reason=why,
            latest_at=snaps[0].collected_at if snaps else None,
            summary=f"확인할 수 없음: {why}",
        )
        return VelocityResult(
            output, Evidence(queries=[query], data_summary=output.summary, collected_at=now)
        )
    rows, compared, missing = velocities(snaps)
    ranked = sorted(
        (r for r in rows if r.views_per_hour is not None),
        key=lambda r: (-(r.views_per_hour or 0.0), r.video_id),
    )
    top = request.top or top_setting(env)
    picked = with_names(db, ranked[:top])
    hours = (snaps[0].collected_at - snaps[1].collected_at).total_seconds() / 3600
    summary = (
        f"최근 두 스냅샷({hours:.1f}시간 간격) 모두에 있는 영상 {compared}개 중"
        f" 지금 속도 상위 {len(picked)}개."
    )
    if missing:
        summary += f" 조회수가 빠진 {missing}개는 빼고 셌다."
    output = YouTubeVelocityOutput(
        status="known",
        latest_at=snaps[0].collected_at,
        previous_at=snaps[1].collected_at,
        earlier_at=snaps[2].collected_at if len(snaps) > 2 else None,
        compared_videos=compared,
        missing_views=missing,
        videos=picked,
        summary=summary,
    )
    evidence = Evidence(queries=[query], data_summary=summary, collected_at=now)
    return VelocityResult(output, evidence)
