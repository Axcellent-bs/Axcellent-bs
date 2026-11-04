"""trend_age: 연령대별 상승. 읽기 전용(grade read). 숫자는 모두 코드, LLM을 쓰지 않는다.

- 대상(코드가 고름, 손으로 나열하지 않음): 오늘 급상승 판정(today_board) → 지금 인기 상위
  (trend_popular, 순위 순) → 시즌 키워드(오늘 만든 기획안의 시즌 확인에서 기준을 넘은 것, 없으면
  카탈로그 카테고리 이름·상품 핵심어). 몇 개씩 고를지는 `[age]` 설정(기본 3·5·2 = 10개).
  요청에 keywords가 있으면 그것만
- 연령대(`[age] bands`, 기본 10대·20대·30대·40대·50대 이상 = 네이버 연령 코드 묶음)마다
  네이버 검색어 트렌드를 따로 불러, trend_scan과 같은 판정 함수(`judge_series`)로
  "그 연령대에서 평소 대비 몇 배"를 낸다
- 네이버 값은 요청마다 최댓값이 100인 상대값이라 연령대끼리 절대량은 비교하지 않는다(응답 `note`)
- 하루 호출 상한 `[age] daily_calls`(기본 80). 주제 하나에 연령대 수만큼 부르는데, 남은 호출이
  모자라면 그 주제만 unknown + 이유. 결과는 하루 캐시(같은 주제·연령대 설정이면 그날은 다시
  부르지 않는다)
README "trend_age".
"""

from __future__ import annotations

import json
import sqlite3
import threading
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    AgeBandTrend,
    AgeTopic,
    TrendAgeOutput,
    TrendAgeRequest,
    TrendKeywordGroup,
    TrendPopularRequest,
    TrendScanInput,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.skills.trend_scan import REQUEST_DAYS, TREND_CONFIG_ENV, judge_series, yesterday_kst
from data_agent.today_board import BOARD, TodayBoard

KST = timezone(timedelta(hours=9))
DB_ENV = "DATA_TREND_AGE_DB"
DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "trend_age.db"  # backend/.data
# 네이버 연령 코드: 1=0~12, 2=13~18, 3=19~24, 4=25~29, 5=30~34, 6=35~39, 7=40~44, 8=45~49,
# 9=50~54, 10=55~59, 11=60~
DEFAULT_BANDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("10대", ("2",)),
    ("20대", ("3", "4")),
    ("30대", ("5", "6")),
    ("40대", ("7", "8")),
    ("50대 이상", ("9", "10", "11")),
)
NAVER_AGES = {str(n) for n in range(1, 12)}


@dataclass(frozen=True)
class AgeConfig:
    bands: tuple[tuple[str, tuple[str, ...]], ...] = DEFAULT_BANDS
    rising_topics: int = 3
    popular_topics: int = 5
    season_topics: int = 2
    daily_calls: int = 80


def load_config(env: Mapping[str, str]) -> AgeConfig:
    path = env.get(TREND_CONFIG_ENV)
    if not path or not Path(path).is_file():
        return AgeConfig()
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("age") or {}
        base = AgeConfig()
        bands = tuple(
            (str(name).strip(), codes)
            for name, v in (raw.get("bands") or {}).items()
            if str(name).strip()
            and (codes := tuple(str(c).strip() for c in (v if isinstance(v, list) else [v])
                                if str(c).strip() in NAVER_AGES))
        )  # fmt: skip
        return AgeConfig(
            bands=bands or base.bands,
            rising_topics=max(0, int(raw.get("rising_topics", base.rising_topics))),
            popular_topics=max(0, int(raw.get("popular_topics", base.popular_topics))),
            season_topics=max(0, int(raw.get("season_topics", base.season_topics))),
            daily_calls=max(0, int(raw.get("daily_calls", base.daily_calls))),
        )
    except (OSError, tomllib.TOMLDecodeError, TypeError, ValueError):
        return AgeConfig()


class AgeStore:
    """그날 쓴 네이버 호출 수와 주제별 결과 (하루 캐시)."""

    def __init__(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS calls (day TEXT PRIMARY KEY, calls INTEGER NOT NULL)"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS topics (day TEXT NOT NULL, key TEXT NOT NULL,"
                " topic TEXT NOT NULL, PRIMARY KEY (day, key))"
            )

    def close(self) -> None:
        self._conn.close()

    def calls(self, day: date) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT calls FROM calls WHERE day = ?", (day.isoformat(),)
            ).fetchone()
        return int(row[0]) if row else 0

    def add_calls(self, day: date, n: int) -> None:
        if n <= 0:
            return
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO calls VALUES (?, ?) ON CONFLICT(day) DO UPDATE"
                " SET calls = calls + excluded.calls",
                (day.isoformat(), n),
            )

    def topic(self, day: date, key: str) -> AgeTopic | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT topic FROM topics WHERE day = ? AND key = ?", (day.isoformat(), key)
            ).fetchone()
        return AgeTopic.model_validate_json(row[0]) if row else None

    def save_topic(self, day: date, key: str, topic: AgeTopic) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO topics VALUES (?,?,?)",
                (day.isoformat(), key, topic.model_dump_json()),
            )


@dataclass
class Target:
    keyword: str
    keywords: list[str]
    sources: list[str]


def _norm(name: str) -> str:
    return "".join(name.split()).lower()


def pick_targets(
    config: AgeConfig,
    *,
    rising: Sequence[str],
    popular: Sequence[tuple[str, Sequence[str]]],
    season: Sequence[str],
    limit: int,
) -> list[Target]:
    """급상승 → 지금 인기 → 시즌 순으로 설정 수만큼. 같은 이름(공백·대소문자 무시)은 출처만 합침."""
    out: list[Target] = []
    seen: dict[str, Target] = {}

    def add(name: str, names: Sequence[str], source: str, quota: int, used: list[int]) -> None:
        keys = [k for k in (_norm(n) for n in [name, *names]) if k]
        if not keys:
            return
        same = next((seen[k] for k in keys if k in seen), None)
        if same is not None:  # 이름이나 별칭이 겹치면 같은 주제로 보고 출처만 합친다
            if source not in same.sources:
                same.sources.append(source)
            return
        if used[0] >= quota or len(out) >= limit:
            return
        used[0] += 1
        t = Target(name, list(dict.fromkeys([name, *names]))[:20], [source])
        for k in keys:
            seen[k] = t
        out.append(t)

    for quota, source, rows in (
        (config.rising_topics, "rising", [(n, ()) for n in rising]),
        (config.popular_topics, "popular", list(popular)),
        (config.season_topics, "season", [(n, ()) for n in season]),
    ):
        used = [0]
        for name, names in rows:
            add(name, names, source, quota, used)
    return out


def _band_trend(name: str, codes: Sequence[str], verdict: Any) -> AgeBandTrend:
    ev = verdict.evidence
    return AgeBandTrend(
        band=name,
        ages=list(codes),
        status=verdict.status,
        ratio=verdict.ratio,
        latest_value=ev.latest_value,
        baseline_value=ev.baseline_value,
        reason=verdict.unknown_reason,
    )


def _top(bands: Sequence[AgeBandTrend]) -> tuple[str | None, float | None]:
    known = [b for b in bands if b.ratio is not None]
    pool = [b for b in known if b.status == "rising"] or known
    if not pool:
        return None, None
    best = max(pool, key=lambda b: b.ratio or 0.0)
    return best.band, best.ratio


async def measure_topic(
    target: Target, connector: Any, config: AgeConfig, start: date, end: date
) -> tuple[AgeTopic, int]:
    """주제 하나를 연령대마다 따로 판정한다. (결과, 실제로 부른 네이버 수)."""
    group = TrendKeywordGroup(group_name=target.keyword, keywords=target.keywords)
    query = TrendScanInput(keyword_groups=[group], start_date=start, end_date=end)
    bands: list[AgeBandTrend] = []
    used = 0
    for name, codes in config.bands:
        before = getattr(connector, "calls_today", None)
        try:
            response = await connector.search_trend(query, ages=codes)
        except ConnectorUnavailable as error:
            why = f"네이버 검색어 트렌드를 읽지 못함 [{error.code}]"
            bands.append(AgeBandTrend(band=name, ages=list(codes), status="unknown", reason=why))
            used += 1
            continue
        after = getattr(connector, "calls_today", None)
        used += (after - before) if isinstance(before, int) and isinstance(after, int) else 1
        if not response.series or not response.series[0].points:
            why = "이 연령대 검색량이 적어 네이버가 값을 주지 않음"
            bands.append(AgeBandTrend(band=name, ages=list(codes), status="unknown", reason=why))
            continue
        bands.append(_band_trend(name, codes, judge_series(response.series[0], start, end)))
    top_band, top_ratio = _top(bands)
    known = any(b.status != "unknown" for b in bands)
    topic = AgeTopic(
        keyword=target.keyword,
        keywords=target.keywords,
        sources=target.sources,  # type: ignore[arg-type]
        status="known" if known else "unknown",
        reason=None if known else "모든 연령대에서 값을 받지 못함",
        bands=bands,
        top_band=top_band,
        top_ratio=top_ratio,
    )
    return topic, used


@dataclass(frozen=True)
class AgeResult:
    output: TrendAgeOutput
    evidence: Evidence


async def trend_age(
    request: TrendAgeRequest,
    connector: Any,
    *,
    env: Mapping[str, str],
    popular_items: Sequence[tuple[str, Sequence[str]]] = (),
    season_keywords: Sequence[str] = (),
    board: TodayBoard | None = None,
    store: AgeStore | None = None,
    now: datetime | None = None,
) -> AgeResult:
    """연령대별 상승. 상태를 바꾸지 않는다(그날 결과·호출 수만 data 파일에 남김)."""
    now = now or datetime.now(UTC)
    today = now.astimezone(KST).date()
    config = load_config(env)
    board = board if board is not None else BOARD
    end = yesterday_kst(now)
    start = end - timedelta(days=REQUEST_DAYS - 1)
    limit = request.max_topics or (config.rising_topics + config.popular_topics
                                   + config.season_topics)  # fmt: skip
    if request.keywords:
        targets = [Target(k.strip(), [k.strip()], ["request"]) for k in request.keywords
                   if k.strip()][:limit]  # fmt: skip
    else:
        targets = pick_targets(config, rising=board.rising_names(), popular=popular_items,
                               season=season_keywords, limit=limit)  # fmt: skip
    out = TrendAgeOutput(
        start_date=start, end_date=end, bands=[b for b, _ in config.bands],
        call_cap=config.daily_calls, generated_at=now,
    )  # fmt: skip
    queries: list[str] = []
    if not targets:
        out.reason = "볼 주제가 없음 (오늘 급상승 판정·지금 인기·시즌 키워드가 모두 없음)"
        out.summary = f"연령대별 상승 없음: {out.reason}"
        return AgeResult(out, Evidence(queries=queries, data_summary=out.summary, collected_at=now))
    own = store is None
    store = store or AgeStore(env.get(DB_ENV) or DEFAULT_DB)
    bands_key = json.dumps(config.bands, ensure_ascii=False)
    cached_all = True
    try:
        for t in targets:
            key = f"{_norm(t.keyword)}|{json.dumps(t.keywords, ensure_ascii=False)}|{bands_key}"
            hit = store.topic(today, key)
            if hit is not None:
                out.items.append(hit.model_copy(update={"sources": t.sources}))
                continue
            cached_all = False
            need = len(config.bands)
            if store.calls(today) + need > config.daily_calls:
                why = f"하루 네이버 호출 상한({config.daily_calls}번)에 닿아 이 주제는 보지 않음"
                out.items.append(
                    AgeTopic(keyword=t.keyword, keywords=t.keywords, sources=t.sources, reason=why)  # type: ignore[arg-type]
                )
                continue
            topic, used = await measure_topic(t, connector, config, start, end)
            store.add_calls(today, used)
            queries.append(
                f"naver search-trend ages group={t.keyword} bands={len(config.bands)}"
                f" {start}~{end} date calls={used}"
            )
            if topic.status == "known":
                store.save_topic(today, key, topic)
            out.items.append(topic)
        out.calls_used = store.calls(today)
    finally:
        if own:
            store.close()
    out.cached = cached_all
    known = [i for i in out.items if i.status == "known"]
    out.status = "known" if known else "unknown"
    if not known:
        out.reason = "모든 주제에서 연령대별 값을 받지 못함"
    rising = [f"{i.keyword}({i.top_band} {i.top_ratio:g}배)" for i in known
              if i.top_band and any(b.status == "rising" and b.band == i.top_band for b in i.bands)
              ]  # fmt: skip
    out.summary = (
        f"주제 {len(out.items)}개 중 {len(known)}개를 연령대 {len(config.bands)}개로 봄"
        f" (오늘 네이버 {out.calls_used}/{config.daily_calls}번)."
        + (f" 연령대 급상승: {', '.join(rising[:5])}." if rising else " 연령대 급상승 없음.")
    )
    return AgeResult(out, Evidence(queries=queries, data_summary=f"{out.summary}\n{out.note}",
                                   collected_at=now))  # fmt: skip


def popular_rows(output: Any, limit: int) -> list[tuple[str, list[str]]]:
    """trend_popular 응답 → (대표 이름, 별칭) 순위 순."""
    items = sorted(output.items, key=lambda i: i.rank)
    return [(i.keyword, list(i.aliases)) for i in items[: max(0, limit)]]


async def popular_targets(llm: Any, env: Mapping[str, str], now: datetime, limit: int,
                          category_cache: Any = None) -> list[tuple[str, list[str]]]:  # fmt: skip
    """지금 인기 상위 (기획안 재료와 같은 경로: YouTube 커넥터 없이, 보충 영상 없이)."""
    from data_agent.skills.trend_popular import popular

    result = await popular(TrendPopularRequest(), llm, env=env, now=now,
                           category_cache=category_cache, fill=False)  # fmt: skip
    return popular_rows(result.output, limit)
