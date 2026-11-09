"""YouTube 할당량 장부 하나 (하루 = 미국 태평양 시간 0시부터, 구글 할당량과 같은 경계).

- 모든 YouTube 호출은 커넥터(`connectors.youtube.YouTubeConnector._get_live`)를 지나므로 거기서
  적는다: 수집기·숏츠 검색·하위 주제 검색·지금 인기 보충 검색·영상 탐색이 같은 장부를 쓴다.
  단가는 공식 문서 값(search 100, videos·channels·videoCategories 1).
  실패한 호출도 할당량을 쓰므로 센다
- 부른 곳(`caller`): 단계 기록 중인 skill 이름(`stage_timing`), 없으면 커넥터에 준 이름
  (수집기는 `youtube_collect`), 그것도 없으면 `other`
- 하루 전체 상한 `DATA_YOUTUBE_DAILY_UNITS`(기본 8,000단위, 공식 하루 10,000 중 여유를 남김).
  어떤 호출이든 더하면 넘을 때는 부르지 않고 `QUOTA_EXCEEDED`(공식 할당량 초과와 같은 길).
  확인과 기록은 한 번에 한다(서버·수집기가 같은 파일을 써도 합계가 상한을 넘지 않게)
- 예약 몫: 새 숏츠 추적(부른 곳 `fresh_shorts`)에 `DATA_FRESH_SHORTS_DAILY_UNITS`(기본 1,200단위)를
  먼저 떼어 둔다. 다른 기능의 검색은 합쳐서 상한 − 예약(기본 6,800) 안에서만, 새 숏츠 추적의
  검색은 자기 몫 안에서만. 전체 상한은 늘 먼저 지킨다(예약 전에 많이 쓴 날은 새 숏츠 추적도
  남은 만큼만). 1단위 호출은 몫을 나누지 않고 전체 상한만 본다
- 하루: 미국 태평양 시간(America/Los_Angeles, 서머타임 자동) 0시에 바뀐다 = 한국 17:00
  (서머타임 중 16:00). 화면·로그는 "다음 초기화: 오늘 17:00 KST"처럼 한국 시각으로 적는다.
  LLM 장부(`llm_budget`)는 한국 0시 그대로
- 기록은 SQLite(`DATA_YOUTUBE_QUOTA_DB`, run_all.sh가 `backend/.data/youtube_quota.db`로 준다,
  서버·수집기가 같은 파일). 환경변수가 없으면 프로세스 메모리(테스트). 표 `usage_pt`(태평양 날짜).
  예전 표 `usage`(한국 날짜)는 지우지 않고, `usage_pt`가 비어 있을 때 한 번만 지금 한국 날짜 줄을
  지금 태평양 날짜로 옮긴다(태평양 0시 전이면 그 한국 날짜는 지금 태평양 날짜 안에 들어 있다)
"""

from __future__ import annotations

import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ax_contracts.schemas.data import YouTubeCallerUsage, YouTubeDailyUsage

DB_ENV = "DATA_YOUTUBE_QUOTA_DB"
CAP_ENV = "DATA_YOUTUBE_DAILY_UNITS"
DEFAULT_CAP = 8000
SEARCH_UNITS = 100
KST = timezone(timedelta(hours=9))
PT = ZoneInfo("America/Los_Angeles")  # 구글 할당량이 다시 채워지는 시간대
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


def day_of(at: datetime) -> date:
    """그 시각이 들어가는 장부 날짜 (태평양 시간 날짜)."""
    return at.astimezone(PT).date()


def today() -> date:
    return day_of(datetime.now(UTC))


def next_reset(now: datetime | None = None) -> datetime:
    """다음 초기화 시각 (다음 태평양 0시, UTC)."""
    now = now or datetime.now(UTC)
    nxt = datetime.combine(day_of(now) + timedelta(days=1), time(0), tzinfo=PT)
    return nxt.astimezone(UTC)


def reset_label(now: datetime | None = None) -> str:
    """'다음 초기화: 오늘 17:00 KST' (오늘·내일은 한국 날짜 기준)."""
    now = now or datetime.now(UTC)
    at = next_reset(now).astimezone(KST)
    word = "오늘" if at.date() == now.astimezone(KST).date() else "내일"
    return f"다음 초기화: {word} {at:%H:%M} KST"


class Ledger:
    def __init__(self, path: str | Path | None = None, *, now: datetime | None = None) -> None:
        self._lock = threading.Lock()
        self._mem: dict[tuple[str, str], tuple[int, int]] = {}
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS usage_pt (day TEXT NOT NULL, caller TEXT NOT NULL,"
                    " units INTEGER NOT NULL, searches INTEGER NOT NULL,"
                    " PRIMARY KEY (day, caller))"
                )
                self._migrate(now or datetime.now(UTC))

    def _migrate(self, now: datetime) -> None:
        """`usage_pt`가 비면 예전 표의 지금 한국 날짜 줄을 지금 태평양 날짜로 한 번 옮긴다.

        한국 0시가 지금 태평양 날짜 안이면(한국 17시 전, 서머타임 중 16시 전) 그 한국 날짜 사용량은
        모두 지금 장부 날짜 것이라 옮긴다. 그 뒤(새 태평양 날짜가 이미 시작)면 한국 날짜 줄에 지난
        장부 날짜 사용량이 섞여 있으므로 옮기지 않고 0부터 센다.
        """
        assert self._conn is not None
        if self._conn.execute("SELECT 1 FROM usage_pt LIMIT 1").fetchone():
            return
        old = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'usage'"
        ).fetchone()
        kst_day = now.astimezone(KST).date()
        kst_start = datetime.combine(kst_day, time(0), tzinfo=KST)
        pt_start = datetime.combine(day_of(now), time(0), tzinfo=PT)
        if not old or kst_start < pt_start:
            return
        self._conn.execute(
            "INSERT INTO usage_pt SELECT ?, caller, units, searches FROM usage WHERE day = ?",
            (day_of(now).isoformat(), kst_day.isoformat()),
        )

    # ------------------------------------------------------------ 기록

    def _rows_locked(self, key: str) -> list[tuple[str, int, int]]:
        if self._conn is not None:
            rows = self._conn.execute(
                "SELECT caller, units, searches FROM usage_pt WHERE day = ?", (key,)
            ).fetchall()
        else:
            rows = [(c, u, s) for (d, c), (u, s) in self._mem.items() if d == key]
        return sorted(((str(c), int(u), int(s)) for c, u, s in rows),
                      key=lambda r: (-r[1], r[0]))  # fmt: skip

    def _add_locked(self, key: str, caller: str, units: int, n: int) -> None:
        if self._conn is not None:
            self._conn.execute(
                "INSERT INTO usage_pt VALUES (?,?,?,?) ON CONFLICT(day, caller) DO UPDATE"
                " SET units = units + excluded.units, searches = searches + excluded.searches",
                (key, caller, units, n),
            )
            old = (date.fromisoformat(key) - timedelta(days=30)).isoformat()
            self._conn.execute("DELETE FROM usage_pt WHERE day < ?", (old,))
            return
        u, s = self._mem.get((key, caller), (0, 0))
        self._mem[(key, caller)] = (u + units, s + n)

    def add(self, caller: str, units: int, *, search: bool = False,
            day: date | None = None) -> None:  # fmt: skip
        key = (day or today()).isoformat()
        with self._lock:
            if self._conn is not None:
                with self._conn:
                    self._add_locked(key, caller, units, 1 if search else 0)
                return
            self._add_locked(key, caller, units, 1 if search else 0)

    def take(self, caller: str, units: int, *, search: bool = False,
             env: Mapping[str, str] | None = None) -> str | None:  # fmt: skip
        """몫 안이면 적고 None, 아니면 적지 않고 막은 이유. 확인과 기록을 한 번에 한다."""
        key = today().isoformat()
        with self._lock:
            if self._conn is None:
                why = _refuse(self._rows_locked(key), caller, units, search, env)
                if why is None:
                    self._add_locked(key, caller, units, 1 if search else 0)
                return why
            self._conn.execute("BEGIN IMMEDIATE")  # 다른 프로세스와 같은 순간에 넘지 않게
            try:
                why = _refuse(self._rows_locked(key), caller, units, search, env)
                if why is None:
                    self._add_locked(key, caller, units, 1 if search else 0)
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
            return why

    # ------------------------------------------------------------ 읽기

    def rows(self, day: date | None = None) -> list[tuple[str, int, int]]:
        """(부른 곳, 단위, 검색 수), 단위가 많은 순."""
        with self._lock:
            return self._rows_locked((day or today()).isoformat())

    def units(self, day: date | None = None) -> int:
        return sum(u for _, u, _ in self.rows(day))

    def split(self, day: date | None = None) -> tuple[int, int]:
        """(새 숏츠 추적이 쓴 단위, 다른 기능이 쓴 단위)."""
        rows = self.rows(day)
        mine = sum(u for c, u, _ in rows if c == RESERVE_CALLER)
        return mine, sum(u for _, u, _ in rows) - mine

    def can_search(self, env: Mapping[str, str] | None = None,
                   caller: str | None = None) -> bool:  # fmt: skip
        """검색 하나를 더해도 전체 상한과 그 부른 곳의 몫 안인지 (적지는 않는다)."""
        return _refuse(self.rows(), caller or "other", SEARCH_UNITS, True, env) is None

    def usage(self, env: Mapping[str, str] | None = None,
              day: date | None = None) -> YouTubeDailyUsage:  # fmt: skip
        day = day or today()
        rows = self.rows(day)
        units = sum(u for _, u, _ in rows)
        limit = cap(env)
        others = sum(u for c, u, _ in rows if c != RESERVE_CALLER)
        stopped = others + SEARCH_UNITS > limit - reserve(env) or units + SEARCH_UNITS > limit
        return YouTubeDailyUsage(
            day=day, units=units, unit_cap=limit, searches=sum(s for _, _, s in rows),
            search_stopped=stopped,
            by_caller=[YouTubeCallerUsage(caller=c, units=u, searches=s) for c, u, s in rows],
            note=(f"YouTube 하루 상한에 닿아 검색을 멈춤 (다른 기능 오늘 {others:,}/"
                  f"{limit - reserve(env):,}단위, 새 숏츠 추적 몫은 따로, {reset_label()})"
                  if stopped else None),
        )  # fmt: skip

    def status_line(self, env: Mapping[str, str] | None = None,
                    day: date | None = None) -> str:  # fmt: skip
        """수집기 상태 줄: 기능별 오늘 단위 (단위 많은 순, 검색 수)와 다음 초기화 (한국 시각)."""
        rows = self.rows(day)
        limit, held = cap(env), reserve(env)
        mine = sum(u for c, u, _ in rows if c == RESERVE_CALLER)
        others = [(c, u, s) for c, u, s in rows if c != RESERVE_CALLER]
        parts = ", ".join(f"{c} {u:,}" + (f"(검색 {s})" if s else "") for c, u, s in others)
        return (
            f"[quota] 오늘 {sum(u for _, u, _ in rows):,}/{limit:,}단위 ·"
            f" 다른 기능 {sum(u for _, u, _ in others):,}/{limit - held:,} ({parts or '없음'}) ·"
            f" 새 숏츠 추적 {mine:,}/{held:,} 예약 · {reset_label()}"
        )

    def clear(self) -> None:
        """테스트용: 메모리 기록을 비운다."""
        with self._lock:
            self._mem = {}


def _refuse(rows: list[tuple[str, int, int]], caller: str, units: int, search: bool,
            env: Mapping[str, str] | None) -> str | None:  # fmt: skip
    """막는 이유. 검색이면 부른 곳의 몫, 그리고 어떤 호출이든 전체 상한. 막지 않으면 None."""
    limit, held = cap(env), reserve(env)
    mine = sum(u for c, u, _ in rows if c == RESERVE_CALLER)
    others = sum(u for _, u, _ in rows) - mine
    if search and caller == RESERVE_CALLER and mine + units > held:
        return f"새 숏츠 추적 몫에 닿아 검색하지 않음 (오늘 {mine:,}/{held:,}단위)"
    if search and caller != RESERVE_CALLER and others + units > limit - held:
        return (
            f"YouTube 하루 상한에 닿아 검색하지 않음 (다른 기능 오늘 {others:,}/"
            f"{limit - held:,}단위, 전체 {limit:,} 중 새 숏츠 추적 몫 {held:,} 예약)"
        )
    if mine + others + units > limit:
        return (f"YouTube 하루 상한에 닿아 부르지 않음 (오늘 {mine + others:,}/{limit:,}단위,"
                f" {reset_label()})")  # fmt: skip
    return None


LEDGER = Ledger(os.environ.get(DB_ENV) or None)


def current_caller(default: str | None = None) -> str:
    """부른 곳: 단계 기록 중인 skill 이름 → 커넥터 이름 → other."""
    from data_agent.stage_timing import current_name

    name = current_name()
    if name:
        return name.split("@", 1)[0]
    return default or "other"
