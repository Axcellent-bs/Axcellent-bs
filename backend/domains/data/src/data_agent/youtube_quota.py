"""YouTube 할당량 장부 하나 (하루 단위, 한국 날짜). 하루 전체 상한을 넘으면 검색을 멈춘다.

- 모든 YouTube 호출은 커넥터(`connectors.youtube.YouTubeConnector._get_live`)를 지나므로 거기서
  적는다: 수집기·숏츠 검색·하위 주제 검색·지금 인기 보충 검색·영상 탐색이 같은 장부를 쓴다.
  단가는 공식 문서 값(search 100, videos·channels·videoCategories 1).
  실패한 호출도 할당량을 쓰므로 센다
- 부른 곳(`caller`): 단계 기록 중인 skill 이름(`stage_timing`), 없으면 커넥터에 준 이름
  (수집기는 `youtube_collect`), 그것도 없으면 `other`
- 하루 전체 상한 `DATA_YOUTUBE_DAILY_UNITS`(기본 8,000단위, 공식 하루 10,000 중 여유를 남김).
  검색(100단위) 하나를 더하면 넘을 때 검색을 부르지 않고 `QUOTA_EXCEEDED`(공식 할당량 초과와
  같은 길: 그 실행의 남은 검색을 멈춘다). 1단위 호출(videos·channels)은 계속한다
- 기록은 SQLite(`DATA_YOUTUBE_QUOTA_DB`, run_all.sh가 `backend/.data/youtube_quota.db`로 준다,
  서버·수집기가 같은 파일). 환경변수가 없으면 프로세스 메모리(테스트)
- 한국 날짜로 센다. YouTube 공식 할당량은 태평양 시간 자정에 다시 채워지므로 날이 바뀌는 시각이
  다르다(상한을 공식 한도보다 낮게 둔 이유 중 하나)
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from ax_contracts.schemas.data import YouTubeCallerUsage, YouTubeDailyUsage

DB_ENV = "DATA_YOUTUBE_QUOTA_DB"
CAP_ENV = "DATA_YOUTUBE_DAILY_UNITS"
DEFAULT_CAP = 8000
SEARCH_UNITS = 100
KST = timezone(timedelta(hours=9))


def cap(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        value = int(str(env.get(CAP_ENV, "")).strip())
    except ValueError:
        return DEFAULT_CAP
    return value if value > 0 else DEFAULT_CAP


def today() -> date:
    return datetime.now(UTC).astimezone(KST).date()


class Ledger:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._mem: dict[tuple[str, str], tuple[int, int]] = {}
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS usage (day TEXT NOT NULL, caller TEXT NOT NULL,"
                    " units INTEGER NOT NULL, searches INTEGER NOT NULL,"
                    " PRIMARY KEY (day, caller))"
                )

    def add(self, caller: str, units: int, *, search: bool = False,
            day: date | None = None) -> None:  # fmt: skip
        key = (day or today()).isoformat()
        n = 1 if search else 0
        with self._lock:
            if self._conn is not None:
                with self._conn:
                    self._conn.execute(
                        "INSERT INTO usage VALUES (?,?,?,?) ON CONFLICT(day, caller) DO UPDATE"
                        " SET units = units + excluded.units,"
                        " searches = searches + excluded.searches",
                        (key, caller, units, n),
                    )
                    old = (date.fromisoformat(key) - timedelta(days=30)).isoformat()
                    self._conn.execute("DELETE FROM usage WHERE day < ?", (old,))
                return
            u, s = self._mem.get((key, caller), (0, 0))
            self._mem[(key, caller)] = (u + units, s + n)

    def rows(self, day: date | None = None) -> list[tuple[str, int, int]]:
        """(부른 곳, 단위, 검색 수), 단위가 많은 순."""
        key = (day or today()).isoformat()
        with self._lock:
            if self._conn is not None:
                rows = self._conn.execute(
                    "SELECT caller, units, searches FROM usage WHERE day = ?", (key,)
                ).fetchall()
            else:
                rows = [(c, u, s) for (d, c), (u, s) in self._mem.items() if d == key]
        return sorted(((str(c), int(u), int(s)) for c, u, s in rows),
                      key=lambda r: (-r[1], r[0]))  # fmt: skip

    def units(self, day: date | None = None) -> int:
        return sum(u for _, u, _ in self.rows(day))

    def can_search(self, env: Mapping[str, str] | None = None) -> bool:
        return self.units() + SEARCH_UNITS <= cap(env)

    def usage(self, env: Mapping[str, str] | None = None,
              day: date | None = None) -> YouTubeDailyUsage:  # fmt: skip
        day = day or today()
        rows = self.rows(day)
        units = sum(u for _, u, _ in rows)
        limit = cap(env)
        stopped = units + SEARCH_UNITS > limit
        return YouTubeDailyUsage(
            day=day, units=units, unit_cap=limit, searches=sum(s for _, _, s in rows),
            search_stopped=stopped,
            by_caller=[YouTubeCallerUsage(caller=c, units=u, searches=s) for c, u, s in rows],
            note=(f"YouTube 하루 상한에 닿아 검색을 멈춤 (오늘 {units:,}/{limit:,}단위)"
                  if stopped else None),
        )  # fmt: skip

    def clear(self) -> None:
        """테스트용: 메모리 기록을 비운다."""
        with self._lock:
            self._mem = {}


LEDGER = Ledger(os.environ.get(DB_ENV) or None)


def current_caller(default: str | None = None) -> str:
    """부른 곳: 단계 기록 중인 skill 이름 → 커넥터 이름 → other."""
    from data_agent.stage_timing import current_name

    name = current_name()
    if name:
        return name.split("@", 1)[0]
    return default or "other"
