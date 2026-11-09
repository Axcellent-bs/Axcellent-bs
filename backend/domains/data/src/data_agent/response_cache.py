"""트렌드 감지 탭 계산 결과 저장 (미리 계산·응답 캐시). 데이터가 바뀌면 키가 바뀌어 자동으로 무효.

- 누가 채우나: 수집기(`youtube_collect`)가 새 스냅샷을 저장한 직후 `trend_precompute`로
  계산해 넣는다. 화면 요청은 저장된 결과를 읽기만 한다. 저장된 것이 없을 때만
  (첫 실행·수집기 꺼짐) 지금 계산하고 넣는다
- 어디에: `DATA_TREND_RESPONSES_DB` SQLite 파일
  (run_all.sh가 `backend/.data/trend_responses.db`로 준다).
  서버를 다시 켜도 남는다. 없으면 프로세스 메모리에만 둔다(테스트)
- 키 = skill + 요청 입력 + 데이터 시점 도장(`data_stamp`) + 규칙 버전(`rules_version`)
  - 도장: 마지막 YouTube 스냅샷 시각, 마지막 인기 검색어 스냅샷 시각,
    두 스냅샷이 "오래됨" 기준을 넘었는지, 숏츠 형식·주제 분류 저장분,
    숏츠 다시 조회·검색 저장분, 오늘 판정판 내용, 한국 날짜, 설정 파일 내용,
    LLM 설정·YouTube 키가 있는지(값은 안 봄)
  - 규칙 버전: `RULES_VERSION` + data_agent 코드 해시 (코드가 바뀌면 예전 결과를 쓰지 않는다)

숫자는 그대로다: 같은 키면 처음 계산한 응답을 돌려줄 뿐 새로 만들지 않는다.
한계: 같은 키 동안 "올린 뒤 지난 시간"처럼 지금 시각으로 세는 칸은 처음 계산한 값이다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from functools import cache
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
DB_ENV = "DATA_TREND_RESPONSES_DB"
MAX_ENTRIES = 64  # 메모리 모드 상한
KEEP_PER_SKILL = 50  # SQLite 모드: skill마다 최근 이만큼만 남긴다
RULES_VERSION = "2"  # 저장 형식·도장 규칙을 바꾸면 올린다


def _max_at(path: str | Path, sql: str) -> str | None:
    if not Path(path).is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            row = conn.execute(sql).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def _file_digest(paths: list[str | None]) -> str:
    h = hashlib.sha256()
    for p in paths:
        if p and Path(p).is_file():
            h.update(Path(p).read_bytes())
    return h.hexdigest()[:12]


def _stale(at: str | None, hours: float, now: datetime) -> bool | None:
    if not at:
        return None
    return now - datetime.fromisoformat(at) > timedelta(hours=hours)


@cache
def rules_version() -> str:
    """`RULES_VERSION` + data_agent 소스 해시. 프로세스마다 한 번 센다."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256(RULES_VERSION.encode())
    for p in sorted(root.rglob("*.py")):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return f"{RULES_VERSION}:{h.hexdigest()[:12]}"


def data_stamp(env: Mapping[str, str], board_stamp: str, now: datetime | None = None) -> str:
    """데이터 시점 도장. 스냅샷 시각은 인덱스(collected_at)로 바로 찾는다."""
    from ax_agent_template.llm import LLMSettings
    from data_agent.skills import shorts_stats, topic_category
    from data_agent.skills import trend_candidates as cand_mod
    from data_agent.youtube_store import DB_ENV as YT_ENV
    from data_agent.youtube_store import DEFAULT_DB

    now = now or datetime.now(UTC)
    yt = env.get(YT_ENV) or str(DEFAULT_DB)
    tr = cand_mod.trending_db(env)
    shorts = env.get(shorts_stats.SHORTS_DB_ENV) or str(shorts_stats.SHORTS_DEFAULT_DB)
    cats = env.get(topic_category.DB_ENV) or str(topic_category.DEFAULT_DB)
    cand = cand_mod.load_config(env)
    yt_at = _max_at(yt, "SELECT MAX(collected_at) FROM snapshots")
    tr_at = _max_at(tr, "SELECT MAX(collected_at) FROM trending")
    parts = [
        yt_at,
        tr_at,
        # 스냅샷이 "오래됨" 기준을 넘으면 응답이 달라진다 (넘는 순간 한 번 다시 계산)
        _stale(yt_at, cand.max_age_hours, now),
        _stale(tr_at, cand.trending_max_age_hours, now),
        # 숏츠 형식·주제 분류 저장분 (다른 요청이 새로 분류하면 응답 칸이 바뀐다)
        _max_at(shorts, "SELECT MAX(classified_at) || COUNT(*) FROM formats"),
        _max_at(cats, "SELECT MAX(classified_at) || COUNT(*) FROM topic_categories"),
        # 숏츠 다시 조회·오늘 검색 저장분 (shorts_stats 응답 칸)
        _max_at(shorts, "SELECT MAX(at) || COUNT(*) FROM views"),
        _max_at(shorts, "SELECT MAX(day) || COUNT(*) FROM searched_videos"),
        board_stamp,
        now.astimezone(KST).date().isoformat(),
        _file_digest(
            [
                env.get("DATA_TREND_CONFIG"),
                env.get("DATA_TENANT_CONFIG"),
                env.get("DATA_YOUTUBE_CONFIG"),
            ]
        ),  # fmt: skip
        # 이 프로세스가 LLM·YouTube를 쓸 수 있는지 (값은 안 본다).
        # 수집기와 서버의 설정이 다르면 다른 키
        bool((llm := LLMSettings.from_env("data", env)).model and llm.base_url),
        bool(env.get("YOUTUBE_API_KEY")),
    ]
    return json.dumps(parts, ensure_ascii=False)


class ResponseCache:
    """계산 결과 저장. 값은 (outcome, output, evidence JSON) 그대로.

    `path`가 있으면 SQLite(다시 켜도 남음), 없으면 프로세스 메모리 LRU.
    """

    def __init__(self, path: str | Path | None = None, max_entries: int = MAX_ENTRIES) -> None:
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute("PRAGMA journal_mode=WAL")
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS responses (key TEXT PRIMARY KEY,"
                    " skill TEXT NOT NULL, stored_at TEXT NOT NULL, value TEXT NOT NULL)"
                )
                self._conn.execute(
                    "CREATE INDEX IF NOT EXISTS responses_skill ON responses (skill, stored_at)"
                )

    @staticmethod
    def key(skill: str, payload: Any, stamp: str, rules: str) -> str:
        body = json.dumps([skill, payload, stamp, rules], ensure_ascii=False, sort_keys=True,
                          default=str)  # fmt: skip
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if self._conn is not None:
                row = self._conn.execute(
                    "SELECT value FROM responses WHERE key = ?", (key,)
                ).fetchone()
                value = json.loads(row[0]) if row else None
            else:
                value = self._data.get(key)
                if value is not None:
                    self._data.move_to_end(key)
            if value is None:
                self.misses += 1
                return None
            self.hits += 1
            return value

    def latest(self, skill: str) -> Any | None:
        """그 skill로 가장 최근에 저장한 값 (키와 상관없이). 없으면 None. hit·miss는 안 센다."""
        with self._lock:
            if self._conn is None:
                return None  # 메모리 저장은 skill을 남기지 않는다
            row = self._conn.execute(
                "SELECT value FROM responses WHERE skill = ? ORDER BY stored_at DESC LIMIT 1",
                (skill,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key: str, value: Any, skill: str = "") -> None:
        with self._lock:
            if self._conn is not None:
                at = datetime.now(UTC).isoformat()
                body = json.dumps(value, ensure_ascii=False, default=str)
                with self._conn:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO responses VALUES (?,?,?,?)", (key, skill, at, body)
                    )
                    self._conn.execute(
                        "DELETE FROM responses WHERE skill = ? AND key NOT IN (SELECT key FROM"
                        " responses WHERE skill = ? ORDER BY stored_at DESC LIMIT ?)",
                        (skill, skill, KEEP_PER_SKILL),
                    )
                return
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            if self._conn is not None:
                with self._conn:
                    self._conn.execute("DELETE FROM responses")
