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
- 예약 몫: 새 숏츠 추적(부른 곳 `fresh_shorts`)에 `DATA_FRESH_SHORTS_DAILY_UNITS`(기본 1,200단위)를
  먼저 떼어 둔다. 다른 기능은 합쳐서 상한 − 예약(기본 6,800) 안에서만 검색하고, 새 숏츠 추적은
  자기 몫 안에서만 검색한다. 그래서 다른 기능이 몫을 다 써도 새 숏츠 추적 찾기는 막히지 않는다
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
RESERVE_CALLER = "fresh_shorts"  # 예약 몫을 가진 부른 곳 (새 숏츠 추적)
RESERVE_ENV = "DATA_FRESH_SHORTS_DAILY_UNITS"
DEFAULT_RESERVE = 1200


def cap(env: Mapping[str, str] | None = None) -> int:
    env = os.environ if env is None else env
    try:
        value = int(str(env.get(CAP_ENV, "")).strip())
    except ValueError:
        return DEFAULT_CAP
    return value if value > 0 else DEFAULT_CAP


def reserve(env: Mapping[str, str] | None = None) -> int:
    """새 숏츠 추적 예약 몫. 정해 주면 그 값(전체 상한까지), 아니면 1,200과 전체 상한의 15% 중
    작은 값 (8,000이면 1,200). 전체 상한을 낮췄을 때 기본 예약이 다른 기능 몫을 다 먹지 않게 한다.
    """
    env = os.environ if env is None else env
    limit = cap(env)
    try:
        value = int(str(env.get(RESERVE_ENV, "")).strip())
    except ValueError:
        value = 0
    return min(value, limit) if value > 0 else min(DEFAULT_RESERVE, limit * 15 // 100)


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

    def split(self, day: date | None = None) -> tuple[int, int]:
        """(새 숏츠 추적이 쓴 단위, 다른 기능이 쓴 단위)."""
        rows = self.rows(day)
        mine = sum(u for c, u, _ in rows if c == RESERVE_CALLER)
        return mine, sum(u for _, u, _ in rows) - mine

    def can_search(self, env: Mapping[str, str] | None = None,
                   caller: str | None = None) -> bool:  # fmt: skip
        """검색 하나를 더해도 그 부른 곳의 몫 안인지 (예약 몫은 새 숏츠 추적만)."""
        limit, held = cap(env), reserve(env)
        mine, others = self.split()
        if caller == RESERVE_CALLER:
            return mine + SEARCH_UNITS <= held and mine + others + SEARCH_UNITS <= limit
        return others + SEARCH_UNITS <= limit - held

    def limit_note(self, env: Mapping[str, str] | None = None,
                   caller: str | None = None) -> str:  # fmt: skip
        """검색을 막을 때 남기는 이유 (어느 몫에 닿았는지)."""
        limit, held = cap(env), reserve(env)
        mine, others = self.split()
        if caller == RESERVE_CALLER:
            return f"새 숏츠 추적 몫에 닿아 검색하지 않음 (오늘 {mine:,}/{held:,}단위)"
        return (
            f"YouTube 하루 상한에 닿아 검색하지 않음 (다른 기능 오늘 {others:,}/"
            f"{limit - held:,}단위, 전체 {limit:,} 중 새 숏츠 추적 몫 {held:,} 예약)"
        )

    def usage(self, env: Mapping[str, str] | None = None,
              day: date | None = None) -> YouTubeDailyUsage:  # fmt: skip
        day = day or today()
        rows = self.rows(day)
        units = sum(u for _, u, _ in rows)
        limit = cap(env)
        others = sum(u for c, u, _ in rows if c != RESERVE_CALLER)
        stopped = others + SEARCH_UNITS > limit - reserve(env)
        return YouTubeDailyUsage(
            day=day, units=units, unit_cap=limit, searches=sum(s for _, _, s in rows),
            search_stopped=stopped,
            by_caller=[YouTubeCallerUsage(caller=c, units=u, searches=s) for c, u, s in rows],
            note=(f"YouTube 하루 상한에 닿아 검색을 멈춤 (다른 기능 오늘 {others:,}/"
                  f"{limit - reserve(env):,}단위, 새 숏츠 추적 몫은 따로)"
                  if stopped else None),
        )  # fmt: skip

    def status_line(self, env: Mapping[str, str] | None = None,
                    day: date | None = None) -> str:  # fmt: skip
        """수집기 상태 줄: 기능별 오늘 단위 (단위 많은 순, 검색 수)."""
        rows = self.rows(day)
        limit, held = cap(env), reserve(env)
        mine = sum(u for c, u, _ in rows if c == RESERVE_CALLER)
        others = [(c, u, s) for c, u, s in rows if c != RESERVE_CALLER]
        parts = ", ".join(f"{c} {u:,}" + (f"(검색 {s})" if s else "") for c, u, s in others)
        return (
            f"[quota] 오늘 {sum(u for _, u, _ in rows):,}/{limit:,}단위 ·"
            f" 다른 기능 {sum(u for _, u, _ in others):,}/{limit - held:,} ({parts or '없음'}) ·"
            f" 새 숏츠 추적 {mine:,}/{held:,} 예약"
        )

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
