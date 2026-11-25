"""오늘 판정 게시판: 오늘(한국 날짜) 이미 낸 네이버 급상승 판정과 자동 묶음.

trend_popular처럼 네이버를 부르지 않는 skill이 "오늘 판정 결과가 있으면" 그것만 붙이려고 쓴다.
- 판정: trend_scan·trend_candidates·content_recommend가 낸 판정(`TrendVerdict`)을
  그룹 이름·키워드마다 남긴다(공백·대소문자 무시). 같은 이름을 다시 판정하면 새 값으로 바꾼다
- 자동 묶음: trend_candidates가 기준을 넘어 묶은 이름 쌍
- 한국 날짜가 바뀌면 그날 것만 본다
- 저장: `DATA_TODAY_BOARD_DB`가 있으면 그 SQLite 파일에 남겨 서버를 다시 켜도 그날 배지가 유지된다
  (run_all.sh가 `backend/.data/today_board.db`로 준다). 없으면 프로세스 안에만 둔다(테스트)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from ax_contracts.schemas.data import TrendVerdict
from data_agent import company_scope

KST = timezone(timedelta(hours=9))
DB_ENV = "DATA_TODAY_BOARD_DB"
KEEP_DAYS = 7  # 저장 파일에 남기는 날 수 (그 전 날은 지운다)


def _today() -> date:
    return datetime.now(UTC).astimezone(KST).date()


def _key(name: str) -> str:
    return "".join(name.lower().lstrip("#").split())


CREATE = (
    "CREATE TABLE IF NOT EXISTS {table} (day TEXT NOT NULL, kind TEXT NOT NULL,"
    " key TEXT NOT NULL, value TEXT NOT NULL, tenant_id TEXT NOT NULL DEFAULT 'catbooks',"
    " PRIMARY KEY (tenant_id, day, kind, key))"
)


@dataclass
class _Day:
    """회사 하나의 오늘 판정 (메모리)."""

    day: date | None = None
    verdicts: dict[str, TrendVerdict] = field(default_factory=dict)
    pairs: list[tuple[str, str]] = field(default_factory=list)


class TodayBoard:
    """오늘 판정판. 회사마다 따로 (1-5, 11/23): 메모리도 저장 파일 줄도 `tenant_id`로 나눈다.
    회사는 `company_scope.active()` (skill 요청이면 그 요청의 회사, 아니면 지금 회사)."""

    def __init__(self, today: Callable[[], date] = _today, path: str | Path | None = None) -> None:
        self.today = today
        self._days: dict[str, _Day] = {}
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._version: int | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock:
                company_scope.rebuild_with_tenant_key(self._conn, "board", CREATE)

    def _state(self) -> tuple[str, _Day]:
        cid = company_scope.active()
        return cid, self._days.setdefault(cid, _Day())

    def _changed_elsewhere(self) -> bool:
        """다른 프로세스(수집기의 미리 계산)가 저장 파일에 쓴 뒤인지 (`PRAGMA data_version`)."""
        if self._conn is None:
            return False
        with self._lock:
            version = self._conn.execute("PRAGMA data_version").fetchone()[0]
        changed = version != self._version
        self._version = version
        return changed

    def _roll(self) -> tuple[str, _Day]:
        day = self.today()
        if self._changed_elsewhere():  # 다른 프로세스가 썼으면 모든 회사 것을 다시 읽는다
            self._days = {}
        cid, st = self._state()
        if day == st.day:
            return cid, st
        st.day, st.verdicts, st.pairs = day, {}, []
        if self._conn is None:
            return cid, st
        with self._lock, self._conn:
            old = (day - timedelta(days=KEEP_DAYS)).isoformat()
            self._conn.execute("DELETE FROM board WHERE day < ?", (old,))
            rows = self._conn.execute(
                "SELECT kind, key, value FROM board WHERE tenant_id = ? AND day = ?",
                (cid, day.isoformat()),
            ).fetchall()
        for kind, key, value in rows:
            if kind == "verdict":
                st.verdicts[key] = TrendVerdict.model_validate_json(value)
            elif kind == "pair":
                a, _, b = value.partition("\t")
                if (a, b) not in st.pairs:
                    st.pairs.append((a, b))
        return cid, st

    def _save(self, cid: str, st: _Day, kind: str, key: str, value: str) -> None:
        if self._conn is None or st.day is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO board VALUES (?,?,?,?,?)",
                (st.day.isoformat(), kind, key, value, cid),
            )

    def record_verdict(self, names: Iterable[str], verdict: TrendVerdict) -> None:
        cid, st = self._roll()
        raw = verdict.model_dump_json()
        for name in names:
            if name and _key(name):
                st.verdicts[_key(name)] = verdict
                self._save(cid, st, "verdict", _key(name), raw)

    def verdict(self, names: Iterable[str]) -> TrendVerdict | None:
        """이름들 중 오늘 판정이 있는 것 (급상승이 있으면 그것을 먼저)."""
        _cid, st = self._roll()
        found = [st.verdicts[_key(n)] for n in names if n and _key(n) in st.verdicts]
        rising = [v for v in found if v.status == "rising"]
        live = [v for v in rising if not v.evidence.faded]  # 끝난 급상승보다 지금 급상승을 먼저
        return (live or rising or found or [None])[0]

    def record_pair(self, a: str, b: str) -> None:
        cid, st = self._roll()
        pair = (_key(a), _key(b))
        if pair not in st.pairs:
            st.pairs.append(pair)
            self._save(cid, st, "pair", f"{pair[0]}\t{pair[1]}", f"{pair[0]}\t{pair[1]}")

    def rising_names(self) -> list[str]:
        """오늘 급상승으로 판정된 그룹 이름 (기록 순서, 같은 판정은 한 번).

        끝난 급상승(evidence.faded)은 빼낸다: 인사이트 브리프·홍보 콘셉트·trend_age 대상이 이
        목록을 근거로 쓴다 (11/26, 정점이 며칠 전에 끝났는데 계속 급상승으로 쓰이던 것)."""
        _cid, st = self._roll()
        out: list[str] = []
        for v in st.verdicts.values():
            if v.status == "rising" and not v.evidence.faded and v.group_name not in out:
                out.append(v.group_name)
        return out

    def pairs(self) -> list[tuple[str, str]]:
        _cid, st = self._roll()
        return list(st.pairs)

    def stamp(self) -> str:
        """오늘 판정판 내용의 해시 (응답 캐시 키). 같은 판정을 다시 기록하면 바뀌지 않는다.
        회사 ID를 넣어 만든다 (같은 판정이라도 회사가 다르면 다른 키)."""
        cid, st = self._roll()
        body = json.dumps(
            [cid, str(st.day), sorted((k, v.model_dump_json()) for k, v in st.verdicts.items()),
             sorted(st.pairs)], ensure_ascii=False,
        )  # fmt: skip
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    def reload(self) -> None:
        """저장 파일을 다시 읽는다 (다른 프로세스가 쓴 오늘 판정을 보려고). 파일이 없으면 그대로."""
        if self._conn is not None:
            self._days = {}
            self._roll()

    def clear(self) -> None:
        self._days = {}


# 서버 프로세스 하나가 같이 쓴다. run_all.sh가 저장 파일 경로를 주면 다시 켜도 그날 것이 남는다
BOARD = TodayBoard(path=os.environ.get(DB_ENV) or None)
