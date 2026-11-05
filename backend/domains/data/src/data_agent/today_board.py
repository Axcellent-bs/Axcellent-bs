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
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from ax_contracts.schemas.data import TrendVerdict

KST = timezone(timedelta(hours=9))
DB_ENV = "DATA_TODAY_BOARD_DB"
KEEP_DAYS = 7  # 저장 파일에 남기는 날 수 (그 전 날은 지운다)


def _today() -> date:
    return datetime.now(UTC).astimezone(KST).date()


def _key(name: str) -> str:
    return "".join(name.lower().lstrip("#").split())


class TodayBoard:
    def __init__(self, today: Callable[[], date] = _today, path: str | Path | None = None) -> None:
        self.today = today
        self._day: date | None = None
        self._verdicts: dict[str, TrendVerdict] = {}
        self._pairs: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS board (day TEXT NOT NULL, kind TEXT NOT NULL,"
                    " key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (day, kind, key))"
                )

    def _roll(self) -> None:
        day = self.today()
        if day == self._day:
            return
        self._day, self._verdicts, self._pairs = day, {}, []
        if self._conn is None:
            return
        with self._lock, self._conn:
            old = (day - timedelta(days=KEEP_DAYS)).isoformat()
            self._conn.execute("DELETE FROM board WHERE day < ?", (old,))
            rows = self._conn.execute(
                "SELECT kind, key, value FROM board WHERE day = ?", (day.isoformat(),)
            ).fetchall()
        for kind, key, value in rows:
            if kind == "verdict":
                self._verdicts[key] = TrendVerdict.model_validate_json(value)
            elif kind == "pair":
                a, _, b = value.partition("\t")
                if (a, b) not in self._pairs:
                    self._pairs.append((a, b))

    def _save(self, kind: str, key: str, value: str) -> None:
        if self._conn is None or self._day is None:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO board VALUES (?,?,?,?)",
                (self._day.isoformat(), kind, key, value),
            )

    def record_verdict(self, names: Iterable[str], verdict: TrendVerdict) -> None:
        self._roll()
        raw = verdict.model_dump_json()
        for name in names:
            if name and _key(name):
                self._verdicts[_key(name)] = verdict
                self._save("verdict", _key(name), raw)

    def verdict(self, names: Iterable[str]) -> TrendVerdict | None:
        """이름들 중 오늘 판정이 있는 것 (급상승이 있으면 그것을 먼저)."""
        self._roll()
        found = [self._verdicts[_key(n)] for n in names if n and _key(n) in self._verdicts]
        rising = [v for v in found if v.status == "rising"]
        return (rising or found or [None])[0]

    def record_pair(self, a: str, b: str) -> None:
        self._roll()
        pair = (_key(a), _key(b))
        if pair not in self._pairs:
            self._pairs.append(pair)
            self._save("pair", f"{pair[0]}\t{pair[1]}", f"{pair[0]}\t{pair[1]}")

    def rising_names(self) -> list[str]:
        """오늘 급상승으로 판정된 그룹 이름 (기록 순서, 같은 판정은 한 번)."""
        self._roll()
        out: list[str] = []
        for v in self._verdicts.values():
            if v.status == "rising" and v.group_name not in out:
                out.append(v.group_name)
        return out

    def pairs(self) -> list[tuple[str, str]]:
        self._roll()
        return list(self._pairs)

    def stamp(self) -> str:
        """오늘 판정판 내용의 해시 (응답 캐시 키). 같은 판정을 다시 기록하면 바뀌지 않는다."""
        self._roll()
        body = json.dumps(
            [str(self._day), sorted((k, v.model_dump_json()) for k, v in self._verdicts.items()),
             sorted(self._pairs)], ensure_ascii=False,
        )  # fmt: skip
        return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]

    def reload(self) -> None:
        """저장 파일을 다시 읽는다 (다른 프로세스가 쓴 오늘 판정을 보려고). 파일이 없으면 그대로."""
        if self._conn is not None:
            self._day = None
            self._roll()

    def clear(self) -> None:
        self._day, self._verdicts, self._pairs = None, {}, []


# 서버 프로세스 하나가 같이 쓴다. run_all.sh가 저장 파일 경로를 주면 다시 켜도 그날 것이 남는다
BOARD = TodayBoard(path=os.environ.get(DB_ENV) or None)
