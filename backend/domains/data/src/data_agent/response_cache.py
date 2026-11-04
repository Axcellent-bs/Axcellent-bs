"""읽기 응답 캐시 (트렌드 감지 탭 속도). 데이터가 바뀌면 키가 바뀌어 자동으로 무효.

키 = skill + 요청 입력 + 데이터 시점 도장(`data_stamp`: 마지막 YouTube 스냅샷 시각, 마지막
인기 검색어 스냅샷 시각, 숏츠 형식·주제 분류 저장분, 오늘 판정판 내용, 한국 날짜, 설정 파일 내용).
새 스냅샷이 들어오거나 판정·설정·코드가 바뀌면 다른 키가 되어 다시 계산한다.

숫자는 그대로다: 같은 키면 처음 계산한 응답을 돌려줄 뿐 새로 만들지 않는다.
한계: 같은 스냅샷 동안 "올린 뒤 지난 시간"처럼 지금 시각으로 세는 칸은 처음 계산한 값이다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections import OrderedDict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

KST = timezone(timedelta(hours=9))
MAX_ENTRIES = 64


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


def data_stamp(env: Mapping[str, str], board_stamp: str) -> str:
    """데이터 시점 도장. 스냅샷 시각은 인덱스(collected_at)로 바로 찾는다."""
    from data_agent.skills import shorts_stats, topic_category
    from data_agent.skills.trend_candidates import trending_db
    from data_agent.youtube_store import DB_ENV, DEFAULT_DB

    yt = env.get(DB_ENV) or str(DEFAULT_DB)
    tr = trending_db(env)
    shorts = env.get(shorts_stats.SHORTS_DB_ENV) or str(shorts_stats.SHORTS_DEFAULT_DB)
    cats = env.get(topic_category.DB_ENV) or str(topic_category.DEFAULT_DB)
    parts = [
        _max_at(yt, "SELECT MAX(collected_at) FROM snapshots"),
        _max_at(tr, "SELECT MAX(collected_at) FROM trending"),
        # 숏츠 형식·주제 분류 저장분 (다른 요청이 새로 분류하면 응답 칸이 바뀐다)
        _max_at(shorts, "SELECT MAX(classified_at) || COUNT(*) FROM formats"),
        _max_at(cats, "SELECT MAX(classified_at) || COUNT(*) FROM topic_categories"),
        board_stamp,
        datetime.now(UTC).astimezone(KST).date().isoformat(),
        _file_digest(
            [
                env.get("DATA_TREND_CONFIG"),
                env.get("DATA_TENANT_CONFIG"),
                env.get("DATA_YOUTUBE_CONFIG"),
            ]
        ),  # fmt: skip
    ]
    return json.dumps(parts, ensure_ascii=False)


class ResponseCache:
    """프로세스 메모리 LRU. 값은 (outcome, output, evidence) 그대로."""

    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._data: OrderedDict[str, Any] = OrderedDict()
        self._lock = threading.Lock()
        self.max_entries = max_entries
        self.hits = 0
        self.misses = 0

    @staticmethod
    def key(skill: str, payload: Any, stamp: str, rules: str) -> str:
        body = json.dumps([skill, payload, stamp, rules], ensure_ascii=False, sort_keys=True,
                          default=str)  # fmt: skip
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return None

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
