"""검색 트렌드 키워드 풀 (`keyword_pool.db`). 수집기가 하루(한국 날짜) 1번 채운다.

- 힌트: 회사 씨앗(`trends.toml` 사람 덮어쓰기 그룹의 키워드 + `[recommend] company_keywords`) +
  카탈로그 카테고리 이름 + 상품 이름 핵심어(많이 나온 순). 상한 `[search] pool_hints`(기본 40)
- 연관어: 검색광고 키워드 도구에 힌트 5개씩. 키워드·PC·모바일 월간 검색량
  ("<10"은 숫자로 바꾸지 않음)·경쟁 정도를 날짜별로 저장
- 도서 분야 거르기: 카탈로그 핵심어(카테고리 이름·상품 핵심어)를 품은 키워드는 남기고, 나머지는
  공용 분류기(`classify_topics`)가 "교육·책"으로 본 것만 남긴다.
  상한 `[search] pool_max`(기본 300, 검색량 순)
- 검색광고 키가 없으면 힌트만 풀로 저장한다(검색량 None, `source = hint`).
  배수·판정은 이 풀로 계속 본다
- 로그: `[keyword_pool] 날짜 N개 (힌트 H · 연관어 R · 분야 밖 X)` (값·키 없이)
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import tomllib
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_agent_template.tenant import setting as tenant_setting
from data_agent.connectors import ConnectorUnavailable

DB_ENV = "DATA_KEYWORD_POOL_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "keyword_pool.db"  # backend/.data
TREND_CONFIG_ENV = "DATA_TREND_CONFIG"
KST = timezone(timedelta(hours=9))
BOOK = "교육·책"
_WORD = re.compile(r"[^\W_]+")


@dataclass(frozen=True)
class SearchConfig:
    """회사 `trends.toml` `[search]`. 기본값은 코드와 같다 (임시 기준)."""

    pool_max: int = 300
    pool_hints: int = 40
    rising_check: int = 30
    season_check: int = 20
    season_min: float = 1.5
    peak_ratio: float = 1.5
    max_new: int = 5  # 날마다 보여 줄 새 키워드 수
    # v3(11/20) 급상승 후보 출처별 상한 (rising_check 자리를 섞어 고른다, 남은 자리는 풀 검색량 순)
    cand_new: int = 12  # 최근 4일 새 키워드
    cand_related_up: int = 10  # 직전 풀 날짜보다 검색량이 크게 는 것
    cand_external: int = 8  # trend_candidates 저장분 (YouTube·인기 검색어)
    cand_company: int = 6  # 회사 trend_scan 그룹 이름
    related_up_ratio: float = 1.3  # 직전 풀 대비 이 배 이상 늘면 related_up
    warming_ratio: float = 1.5  # normal이고 배수가 이 이상이면 level warming


def load_config(env: Mapping[str, str]) -> SearchConfig:
    path = env.get(TREND_CONFIG_ENV) or tenant_setting(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return SearchConfig()
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("search") or {}
        base = SearchConfig()
        return SearchConfig(
            pool_max=max(1, int(raw.get("pool_max", base.pool_max))),
            pool_hints=max(1, int(raw.get("pool_hints", base.pool_hints))),
            rising_check=max(0, int(raw.get("rising_check", base.rising_check))),
            season_check=max(0, int(raw.get("season_check", base.season_check))),
            season_min=max(1.0, float(raw.get("season_min", base.season_min))),
            peak_ratio=max(1.0, float(raw.get("peak_ratio", base.peak_ratio))),
            max_new=max(1, int(raw.get("max_new", base.max_new))),
            cand_new=max(0, int(raw.get("cand_new", base.cand_new))),
            cand_related_up=max(0, int(raw.get("cand_related_up", base.cand_related_up))),
            cand_external=max(0, int(raw.get("cand_external", base.cand_external))),
            cand_company=max(0, int(raw.get("cand_company", base.cand_company))),
            related_up_ratio=max(1.0, float(raw.get("related_up_ratio", base.related_up_ratio))),
            warming_ratio=max(1.0, float(raw.get("warming_ratio", base.warming_ratio))),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return SearchConfig()


def compact(text: str) -> str:
    return "".join(text.lower().split())


@dataclass(frozen=True)
class PoolRow:
    keyword: str
    pc: int | None
    mobile: int | None
    pc_below_10: bool
    mobile_below_10: bool
    competition: str | None
    source: str  # related(검색광고 연관어) · hint(힌트만)
    category: str | None

    @property
    def total(self) -> int | None:
        return None if self.pc is None or self.mobile is None else self.pc + self.mobile


class PoolStore:
    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS pool (day TEXT NOT NULL, keyword TEXT NOT NULL,"
                " pc INTEGER, mobile INTEGER, pc_below_10 INTEGER NOT NULL,"
                " mobile_below_10 INTEGER NOT NULL, competition TEXT, source TEXT NOT NULL,"
                " category TEXT, PRIMARY KEY (day, keyword));"
                "CREATE TABLE IF NOT EXISTS runs (day TEXT PRIMARY KEY, status TEXT NOT NULL,"
                " reason TEXT, hints INTEGER NOT NULL, calls INTEGER NOT NULL, at TEXT NOT NULL);"
                # v3(11/20): 급상승 후보(그날 처음 고른 것을 그날 그대로)·
                # 판정(하루 1번, 일간·주간 공용)
                "CREATE TABLE IF NOT EXISTS rising_candidates (day TEXT NOT NULL,"
                " position INTEGER NOT NULL, keyword TEXT NOT NULL, sources TEXT NOT NULL,"
                " PRIMARY KEY (day, position));"
                "CREATE TABLE IF NOT EXISTS rising_judgments (day TEXT NOT NULL,"
                " keyword TEXT NOT NULL, status TEXT NOT NULL, ratio REAL, baseline REAL,"
                " points_json TEXT NOT NULL, judged_at TEXT NOT NULL, score REAL, reason TEXT,"
                " PRIMARY KEY (day, keyword));"
            )

    def close(self) -> None:
        self._conn.close()

    def done(self, day: date) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM runs WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        return row is not None and row[0] in ("ok", "hint_only")

    def save(self, day: date, rows: Sequence[PoolRow], status: str, reason: str | None,
             hints: int, calls: int, at: datetime) -> None:  # fmt: skip
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM pool WHERE day = ?", (day.isoformat(),))
            self._conn.executemany(
                "INSERT OR REPLACE INTO pool VALUES (?,?,?,?,?,?,?,?,?)",
                [(day.isoformat(), r.keyword, r.pc, r.mobile, int(r.pc_below_10),
                  int(r.mobile_below_10), r.competition, r.source, r.category) for r in rows],
            )  # fmt: skip
            self._conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?)",
                (day.isoformat(), status, reason, hints, calls, at.isoformat()),
            )


    # ---- v3 급상승 후보·판정 ------------------------------------------------------------

    def candidates(self, day: date) -> list[tuple[str, list[str]]]:
        """그날 처음 고른 후보 [(키워드, 출처들)] (순서대로). 없으면 빈 목록."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT keyword, sources FROM rising_candidates WHERE day = ? ORDER BY position",
                (day.isoformat(),),
            ).fetchall()
        return [(k, list(json.loads(s))) for k, s in rows]

    def save_candidates(self, day: date, items: Sequence[tuple[str, Sequence[str]]]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM rising_candidates WHERE day = ?", (day.isoformat(),))
            self._conn.executemany(
                "INSERT INTO rising_candidates VALUES (?,?,?,?)",
                [(day.isoformat(), n, k, json.dumps(list(s))) for n, (k, s) in enumerate(items)],
            )

    _JUDGE_COLS = ("day", "keyword", "status", "ratio", "baseline", "points_json", "judged_at",
                   "score", "reason")  # fmt: skip

    def judgments(self, since: date, until: date) -> list[dict[str, Any]]:
        """since~until 판정 (최근 날짜 먼저)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {', '.join(self._JUDGE_COLS)} FROM rising_judgments"  # noqa: S608
                " WHERE day >= ? AND day <= ? ORDER BY day DESC",
                (since.isoformat(), until.isoformat()),
            ).fetchall()
        return [dict(zip(self._JUDGE_COLS, r, strict=True)) for r in rows]

    def save_judgment(self, day: date, keyword: str, status: str, ratio: float | None,
                      baseline: float | None, points: Sequence[tuple[date, float]], at: datetime,
                      score: float | None = None, reason: str | None = None) -> None:  # fmt: skip
        body = json.dumps([[d.isoformat(), v] for d, v in points])
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO rising_judgments VALUES (?,?,?,?,?,?,?,?,?)",
                (day.isoformat(), keyword, status, ratio, baseline, body, at.isoformat(), score,
                 reason),
            )  # fmt: skip


def _rows(conn: sqlite3.Connection, sql: str, args: Sequence[Any]) -> list[PoolRow]:
    return [
        PoolRow(k, pc, mo, bool(pl), bool(ml), comp, src, cat)
        for k, pc, mo, pl, ml, comp, src, cat in conn.execute(sql, args).fetchall()
    ]


def read_ro(path: str | Path) -> sqlite3.Connection | None:
    if str(path) != ":memory:" and not Path(path).is_file():
        return None
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)


_COLS = "keyword, pc, mobile, pc_below_10, mobile_below_10, competition, source, category"


def latest(path: str | Path) -> tuple[date | None, list[PoolRow]]:
    """가장 최근 날짜의 풀 (검색량 많은 순). 읽기 전용, 파일이 없으면 (None, [])."""
    conn = read_ro(path)
    if conn is None:
        return None, []
    try:
        row = conn.execute("SELECT MAX(day) FROM pool").fetchone()
        if not row or not row[0]:
            return None, []
        day = date.fromisoformat(row[0])
        sql = (f"SELECT {_COLS} FROM pool WHERE day = ?"  # noqa: S608
               " ORDER BY COALESCE(pc, 0) + COALESCE(mobile, 0) DESC, keyword")  # fmt: skip
        rows = _rows(conn, sql, (row[0],))
        return day, rows
    except sqlite3.DatabaseError:
        return None, []
    finally:
        conn.close()


def by_day(path: str | Path, since: date, until: date) -> dict[date, list[PoolRow]]:
    """날짜별 풀 (since~until, 한국 날짜). 읽기 전용."""
    conn = read_ro(path)
    if conn is None:
        return {}
    try:
        out: dict[date, list[PoolRow]] = {}
        for (day,) in conn.execute(
            "SELECT DISTINCT day FROM pool WHERE day >= ? AND day <= ?",
            (since.isoformat(), until.isoformat()),
        ).fetchall():
            out[date.fromisoformat(day)] = _rows(
                conn,
                f"SELECT {_COLS} FROM pool WHERE day = ?",
                (day,),  # noqa: S608
            )
        return out
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------- 힌트·거르기


def seeds(env: Mapping[str, str]) -> list[str]:
    """회사 씨앗: 사람 덮어쓰기 그룹의 키워드 + `[recommend] company_keywords` (trends.toml)."""
    from data_agent.skills.content_recommend import load_config as recommend_config
    from data_agent.skills.trend_scan import default_groups

    words = [k for g in default_groups(env) for k in (g.group_name, *g.keywords)]
    words += list(recommend_config(env).company_keywords)
    return list(dict.fromkeys(w.strip() for w in words if w and w.strip()))


def catalog_terms(catalog: Any) -> tuple[list[str], list[str]]:
    """(카테고리 이름, 상품 이름 핵심어 — 많이 나온 순). 카탈로그가 없으면 빈 목록."""
    if catalog is None or not getattr(catalog, "products", None):
        return [], []
    cats = [c for c in dict.fromkeys(catalog.categories.values()) if c and len(c.strip()) >= 2]
    counts: Counter[str] = Counter()
    for p in catalog.products:
        for w in _WORD.findall(p.name.lower()):
            if len(w) >= 2 and not w.isdigit():
                counts[w] += 1
    words = [w for w, n in counts.most_common() if n >= 2] or [w for w, _ in counts.most_common()]
    return cats, words


def hints(env: Mapping[str, str], catalog: Any, limit: int) -> list[str]:
    cats, words = catalog_terms(catalog)
    return list(dict.fromkeys([*seeds(env), *cats, *words]))[:limit]


def in_domain_by_terms(keyword: str, terms: Sequence[str]) -> bool:
    key = compact(keyword)
    return any(t and (compact(t) in key) for t in terms)


# ---------------------------------------------------------------- 수집


@dataclass(frozen=True)
class CollectResult:
    day: date
    size: int
    hints: int
    related: int
    out_of_domain: int
    calls: int
    status: str  # ok · hint_only · skipped · failed
    reason: str | None = None

    def line(self) -> str:
        head = (f"[keyword_pool] {self.day} {self.size}개 (힌트 {self.hints}"
                f" · 연관어 {self.related} · 분야 밖 {self.out_of_domain})")  # fmt: skip
        return head + (f" ({self.reason})" if self.reason else "")


async def collect(
    env: Mapping[str, str],
    searchad: Any,
    *,
    catalog: Any = None,
    llm: Any = None,
    category_cache: Any = None,
    now: datetime | None = None,
    store: PoolStore | None = None,
    force: bool = False,
) -> CollectResult:
    """오늘 풀을 한 번 채운다 (이미 채웠으면 건너뜀)."""
    from data_agent.skills.topic_category import TopicInput, classify_topics

    now = now or datetime.now(UTC)
    day = now.astimezone(KST).date()
    config = load_config(env)
    own = store is None
    store = store or PoolStore(env.get(DB_ENV) or DEFAULT_DB)
    try:
        if not force and store.done(day):
            return CollectResult(day, 0, 0, 0, 0, 0, "skipped", "오늘 이미 채움")
        hint_list = hints(env, catalog, config.pool_hints)
        if not hint_list:
            store.save(day, [], "failed", "힌트 없음 (씨앗·카탈로그 없음)", 0, 0, now)
            return CollectResult(day, 0, 0, 0, 0, 0, "failed", "힌트 없음 (씨앗·카탈로그 없음)")
        cats, words = catalog_terms(catalog)
        terms = [*cats, *words, *seeds(env)]
        if searchad is None or not getattr(searchad, "configured", False):
            rows = [PoolRow(h, None, None, False, False, None, "hint", None) for h in hint_list]
            why = "검색광고 키가 없어 힌트만 (키 등록 후 가능)"
            store.save(day, rows, "hint_only", why, len(hint_list), 0, now)
            return CollectResult(day, len(rows), len(hint_list), 0, 0, 0, "hint_only", why)
        found: dict[str, Any] = {}
        calls = 0
        why: str | None = None
        for i in range(0, len(hint_list), 5):
            try:
                stats = await searchad.keywords(hint_list[i : i + 5])
            except ConnectorUnavailable as error:
                why = f"검색광고를 다 읽지 못함 [{error.code}]"
                break
            calls += 1
            for s in stats:
                old = found.get(s.keyword)
                if old is None or (s.total or 0) > (old.total or 0):
                    found[s.keyword] = s
        ranked = sorted(found.values(), key=lambda s: (-(s.total or 0), s.keyword))
        ranked = ranked[: config.pool_max * 2]
        cats_of: dict[str, str] = {}
        if ranked and llm is not None:
            try:
                got = await classify_topics([TopicInput(s.keyword) for s in ranked], llm,
                                            cache=category_cache)  # fmt: skip
                cats_of = {k: v.category for k, v in got.items()}
            except Exception:  # noqa: BLE001 - 분류 실패면 카탈로그 일치만 남긴다
                why = (why + " · " if why else "") + "분류 실패로 카탈로그 일치만"
        # 남기는 것: 분류기가 교육·책으로 본 것, 또는 카탈로그 핵심어가 맞고 분류기가 다른 분야로
        # 보지 않은 것 (핵심어 "고양이"만 맞는 "고양이 사료"는 생활·취미라 뺀다)
        keep: list[Any] = [
            s for s in ranked
            if cats_of.get(s.keyword) == BOOK
            or (in_domain_by_terms(s.keyword, terms)
                and cats_of.get(s.keyword) in (None, "기타", "unknown"))
        ]  # fmt: skip
        keep = keep[: config.pool_max]
        rows = [
            PoolRow(s.keyword, s.pc, s.mobile, s.pc_below_10, s.mobile_below_10,
                    s.competition, "related", cats_of.get(s.keyword))
            for s in keep
        ]  # fmt: skip
        status = "ok" if rows else "failed"
        store.save(day, rows, status, why, len(hint_list), calls, now)
        return CollectResult(day, len(rows), len(hint_list), len(found), len(found) - len(rows),
                             calls, status, why)  # fmt: skip
    finally:
        if own:
            store.close()


async def run(env: Mapping[str, str] | None = None) -> CollectResult | None:
    """수집기용: 상품 목록·LLM·분류 저장분을 서버와 같은 방식으로 얻어 채우고 한 줄을 찍는다."""
    from data_agent import handlers
    from data_agent.connectors.naver_searchad import NaverSearchAdConnector
    from data_agent.skills.product_link import read_catalog

    env = os.environ if env is None else env
    try:
        catalog = await read_catalog(handlers.get_catalog())
        result = await collect(
            env, NaverSearchAdConnector(env), catalog=catalog, llm=handlers.get_llm(),
            category_cache=handlers.get_category_cache(),
        )  # fmt: skip
    except Exception as error:  # noqa: BLE001 - 풀 채우기 실패가 수집을 막지 않게
        print(f"[keyword_pool] 실패 ({type(error).__name__})", flush=True)
        return None
    if result.status != "skipped":
        print(result.line(), flush=True)
    return result
