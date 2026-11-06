"""같은 형식 숏츠의 시간 맞춤 기준 (게시 후 같은 시간대). 새 API 호출 없이 저장분만 읽는다.

- 형식: `shorts.db`의 `formats` (shorts_stats가 분류해 저장한 영상 형식)
- 값: `youtube_snapshots.db`의 `snapshots` (수집기가 매시간 저장: 게시 시각·조회·좋아요·댓글).
  영상마다 시점 창 [H, H + 창) 안 첫 스냅샷을 그 시점 값으로 본다 (우리 영상 측정과 같은 규칙)
- 기준값 (형식·시점마다 중앙값): 조회수, 좋아요율, 댓글율, 1일 이후 성장(3d ÷ 1d − 1),
  3일→7일 배수(7d ÷ 3d)
- 표본이 `MIN_BASELINE`(5)개 미만이면 기준 없음 (배수를 내지 않는다)
- 한계: 수집기가 보는 숏츠(인기·추적·검색)만이라 이미 뜬 숏츠 쪽으로 치우칠 수 있다
"""

from __future__ import annotations

import sqlite3
import statistics
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

MIN_BASELINE = 5


@dataclass(frozen=True)
class Sample:
    views: int
    likes: int | None
    comments: int | None


@dataclass
class FormatBaseline:
    """한 형식의 시점별 표본 (시점 → 영상 id → 그 시점 값)."""

    name: str
    samples: dict[str, dict[str, Sample]] = field(default_factory=dict)

    def _median(self, values: list[float]) -> tuple[float | None, int]:
        n = len(values)
        return (statistics.median(values) if n >= MIN_BASELINE else None), n

    def _per(self, checkpoint: str, value: Callable[[Sample], float | None]) -> list[float]:
        out = []
        for s in self.samples.get(checkpoint, {}).values():
            v = value(s)
            if v is not None:
                out.append(v)
        return out

    def views(self, checkpoint: str) -> tuple[float | None, int]:
        return self._median(self._per(checkpoint, lambda s: float(s.views)))

    def like_rate(self, checkpoint: str) -> tuple[float | None, int]:
        return self._median(self._per(checkpoint, lambda s: s.likes / s.views
                                      if s.likes is not None and s.views else None))  # fmt: skip

    def comment_rate(self, checkpoint: str) -> tuple[float | None, int]:
        return self._median(self._per(checkpoint, lambda s: s.comments / s.views
                                      if s.comments is not None and s.views else None))  # fmt: skip

    def _pair(self, a: str, b: str) -> list[tuple[int, int]]:
        first, second = self.samples.get(a, {}), self.samples.get(b, {})
        return [(first[v].views, second[v].views) for v in first if v in second and first[v].views]

    def growth(self) -> tuple[float | None, int]:
        """1일 이후 성장 = 3d ÷ 1d − 1 (1일·3일을 다 잰 영상만)."""
        return self._median([b / a - 1 for a, b in self._pair("1d", "3d")])

    def ratio_7d_3d(self) -> tuple[float | None, int]:
        """3일 → 7일 배수 = 7d ÷ 3d (3일·7일을 다 잰 영상만). 7일 예상치에 쓴다."""
        return self._median([b / a for a, b in self._pair("3d", "7d")])


def first_in_windows(
    rows: Iterable[tuple[float, Sample]], windows: Mapping[str, tuple[int, int]]
) -> dict[str, Sample]:
    """(게시 후 시간, 값) 목록에서 시점마다 창 [H, H + 창) 안 첫 값."""
    out: dict[str, Sample] = {}
    for age, sample in sorted(rows, key=lambda r: r[0]):
        for name, (hours, window) in windows.items():
            if name not in out and hours <= age < hours + window:
                out[name] = sample
    return out


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)


def load(
    env: Mapping[str, str], windows: Mapping[str, tuple[int, int]],
    formats: Iterable[str] | None = None, exclude: Iterable[str] = (),
) -> dict[str, FormatBaseline]:  # fmt: skip
    """형식 → 시점별 표본. 저장 파일이 없으면 빈 결과 (만들지 않는다)."""
    from data_agent.skills.shorts_stats import SHORTS_DB_ENV, SHORTS_DEFAULT_DB
    from data_agent.youtube_store import DB_ENV as YT_DB_ENV
    from data_agent.youtube_store import DEFAULT_DB as YT_DEFAULT_DB

    shorts_path = Path(env.get(SHORTS_DB_ENV) or SHORTS_DEFAULT_DB)
    yt_path = Path(env.get(YT_DB_ENV) or YT_DEFAULT_DB)
    if not shorts_path.is_file() or not yt_path.is_file():
        return {}
    wanted = set(formats) if formats is not None else None
    skip = set(exclude)
    conn = _ro(shorts_path)
    try:
        rows = conn.execute("SELECT video_id, format FROM formats").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        conn.close()
    format_of = {vid: f for vid, f in rows
                 if vid not in skip and (wanted is None or f in wanted)}  # fmt: skip
    out: dict[str, FormatBaseline] = {f: FormatBaseline(f) for f in set(format_of.values())}
    if not format_of:
        return out
    by_video: dict[str, list[tuple[float, Sample]]] = {}
    conn = _ro(yt_path)
    try:
        ids = list(format_of)
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for vid, at, pub, views, likes, comments in conn.execute(
                "SELECT video_id, collected_at, published_at, view_count, like_count,"
                f" comment_count FROM snapshots WHERE video_id IN ({marks})", chunk,
            ):  # fmt: skip
                if views is None:
                    continue
                age = (datetime.fromisoformat(at) - datetime.fromisoformat(pub)).total_seconds()
                by_video.setdefault(vid, []).append((age / 3600, Sample(views, likes, comments)))
    except sqlite3.Error:
        by_video = {}
    finally:
        conn.close()
    for vid, history in by_video.items():
        base = out[format_of[vid]]
        for name, sample in first_in_windows(history, windows).items():
            base.samples.setdefault(name, {})[vid] = sample
    return out
