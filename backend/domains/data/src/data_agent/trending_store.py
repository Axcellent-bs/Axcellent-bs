"""인기 검색어 스냅샷 저장소 (data 자체 SQLite 파일, 커밋하지 않음).

YouTube 스냅샷(`youtube_store`)과 같은 방식이다. 공개 피드는 지금 목록만 주므로 정해진 간격으로 받아
쌓아 둔다(`trending_collect`).

- 위치: 환경변수 `DATA_TRENDING_DB`(없으면 `backend/.data/trending_snapshots.db`)
- `trending`: 수집 시각·지역·순위마다 한 줄 (검색어, 피드의 트래픽 표시값, 피드의 올라온 시각).
  **(수집 시각, 지역, 순위)가 기본 키**라 같은 시각 같은 순위는 한 줄이다
- `runs`: 수집 한 번마다 한 줄 (항목 수, 실패 이유)
- `trending_news`: 수집 시각·지역·검색어마다 피드의 관련 뉴스 제목(JSON 배열, 최대 3개).
  `trending` 표 모양은 그대로 두고 따로 둔다(예전 수집기가 쓰는 INSERT를 깨지 않게)
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from data_agent.connectors.google_trends import TrendingItem

DB_ENV = "DATA_TRENDING_DB"
# backend/.data
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "trending_snapshots.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trending (
    collected_at  TEXT NOT NULL,      -- 수집 시각 (UTC, 초 단위)
    geo           TEXT NOT NULL,      -- 지역 (KR)
    rank          INTEGER NOT NULL,   -- 피드 안 순서 (1부터)
    keyword       TEXT NOT NULL,
    traffic       TEXT,               -- 피드의 트래픽 표시값 (예: 500+). NULL = 피드에 없음
    published_at  TEXT,               -- 피드의 pubDate (UTC). NULL = 피드에 없음
    PRIMARY KEY (collected_at, geo, rank)
);
CREATE TABLE IF NOT EXISTS trending_news (
    collected_at  TEXT NOT NULL,
    geo           TEXT NOT NULL,
    keyword       TEXT NOT NULL,      -- 피드에 나온 검색어 그대로
    titles        TEXT NOT NULL,      -- 관련 뉴스 제목 JSON 배열
    PRIMARY KEY (collected_at, geo, keyword)
);
CREATE TABLE IF NOT EXISTS trending_news_links (
    collected_at  TEXT NOT NULL,
    geo           TEXT NOT NULL,
    keyword       TEXT NOT NULL,
    items         TEXT NOT NULL,      -- [{"title", "source", "url"}] JSON 배열 (피드 값 그대로)
    PRIMARY KEY (collected_at, geo, keyword)
);
CREATE TABLE IF NOT EXISTS runs (
    collected_at  TEXT NOT NULL,
    geo           TEXT NOT NULL,
    item_count    INTEGER NOT NULL,
    errors        TEXT NOT NULL,      -- 실패 이유 JSON 배열 (없으면 [])
    PRIMARY KEY (collected_at, geo)
);
"""


def iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat()


class TrendingStore:
    """SQLite 파일(또는 `:memory:`) 저장소."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save(self, collected_at: datetime, geo: str, items: Iterable[TrendingItem]) -> int:
        """한 시각의 항목들을 넣는다. 이미 있는 (시각, 지역, 순위)는 건너뛴다. 넣은 줄 수."""
        at = iso(collected_at)
        items_list = list(items)
        values = [
            (at, geo, i.rank, i.keyword, i.traffic, iso(i.published_at) if i.published_at else None)
            for i in items_list
        ]
        news = [
            (at, geo, i.keyword, json.dumps(list(i.news_titles), ensure_ascii=False))
            for i in items_list
            if i.news_titles
        ]
        links = [
            (at, geo, i.keyword, json.dumps(
                [{"title": t, "source": src, "url": url} for t, src, url in i.news],
                ensure_ascii=False,
            ))
            for i in items_list
            if i.news
        ]  # fmt: skip
        with self._lock, self._conn:
            before = self._conn.total_changes
            self._conn.executemany("INSERT OR IGNORE INTO trending VALUES (?,?,?,?,?,?)", values)
            saved = self._conn.total_changes - before
            self._conn.executemany("INSERT OR IGNORE INTO trending_news VALUES (?,?,?,?)", news)
            self._conn.executemany(
                "INSERT OR IGNORE INTO trending_news_links VALUES (?,?,?,?)", links
            )
            return saved

    def log_run(self, collected_at: datetime, geo: str, items: int, errors: list[str]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
                (iso(collected_at), geo, items, json.dumps(errors, ensure_ascii=False)),
            )


@dataclass(frozen=True)
class TrendingSnapshot:
    collected_at: datetime
    geo: str
    items: tuple[TrendingItem, ...]  # 순위 순


def latest_trending(
    path: str | Path, geo: str = "KR", at_or_before: datetime | None = None
) -> TrendingSnapshot | None:
    """가장 최근 수집 시각의 인기 검색어. 파일·기록이 없으면 None. 읽기 전용(파일을 만들지 않음).

    `at_or_before`를 주면 그 시각 이전(같은 시각 포함) 스냅샷 중 가장 최근 것이다(지난 기간 판정용).
    """
    if str(path) != ":memory:" and not Path(path).is_file():
        return None
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        sql, args = "SELECT MAX(collected_at) FROM trending WHERE geo = ?", [geo]
        if at_or_before is not None:
            sql += " AND collected_at <= ?"
            args.append(iso(at_or_before))
        row = conn.execute(sql, args).fetchone()
        if not row or not row[0]:
            return None
        rows = conn.execute(
            "SELECT rank, keyword, traffic, published_at FROM trending"
            " WHERE collected_at = ? AND geo = ? ORDER BY rank",
            (row[0], geo),
        ).fetchall()
    except sqlite3.DatabaseError:
        return None  # 표가 없는 파일 등
    finally:
        conn.close()
    items = tuple(
        TrendingItem(
            keyword=str(keyword),
            rank=int(rank),
            traffic=traffic,
            published_at=datetime.fromisoformat(pub).astimezone(UTC) if pub else None,
        )
        for rank, keyword, traffic, pub in rows
    )
    return TrendingSnapshot(datetime.fromisoformat(row[0]).astimezone(UTC), geo, items)


def news_titles(
    path: str | Path, geo: str, collected_at: datetime, keywords: Iterable[str]
) -> dict[str, list[str]]:
    """그 스냅샷(수집 시각·지역)의 검색어별 관련 뉴스 제목. 없으면 빠진다. 읽기 전용.

    예전 파일(표 없음)이나 뉴스를 안 받은 스냅샷이면 빈 dict.
    """
    wanted = [k for k in keywords if k]
    if not wanted or (str(path) != ":memory:" and not Path(path).is_file()):
        return {}
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        marks = ",".join("?" * len(wanted))
        rows = conn.execute(
            "SELECT keyword, titles FROM trending_news"
            f" WHERE collected_at = ? AND geo = ? AND keyword IN ({marks})",
            (iso(collected_at), geo, *wanted),
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}  # 표가 없는 예전 파일
    finally:
        conn.close()
    out: dict[str, list[str]] = {}
    for keyword, raw in rows:
        try:
            titles = [str(t) for t in json.loads(raw) if str(t).strip()]
        except (TypeError, ValueError):
            continue
        if titles:
            out[str(keyword)] = titles
    return out


def news_links(
    path: str | Path, geo: str, collected_at: datetime, keywords: Iterable[str]
) -> dict[str, list[dict[str, str | None]]]:
    """그 스냅샷의 검색어별 관련 뉴스 (제목·출처·주소). 없으면 빠진다. 읽기 전용.

    출처·주소를 저장하기 전 스냅샷(표 없음·줄 없음)이면 빈 dict.
    """
    wanted = [k for k in keywords if k]
    if not wanted or (str(path) != ":memory:" and not Path(path).is_file()):
        return {}
    conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        marks = ",".join("?" * len(wanted))
        rows = conn.execute(
            "SELECT keyword, items FROM trending_news_links"
            f" WHERE collected_at = ? AND geo = ? AND keyword IN ({marks})",
            (iso(collected_at), geo, *wanted),
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    out: dict[str, list[dict[str, str | None]]] = {}
    for keyword, raw in rows:
        try:
            items = [
                {"title": str(x["title"]), "source": x.get("source"), "url": x.get("url")}
                for x in json.loads(raw)
                if isinstance(x, dict) and str(x.get("title") or "").strip()
            ]
        except (TypeError, ValueError):
            continue
        if items:
            out[str(keyword)] = items
    return out
