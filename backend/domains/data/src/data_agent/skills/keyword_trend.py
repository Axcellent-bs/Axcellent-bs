"""keyword_trend: 검색 트렌드 · 도서 분야 4블록. 읽기 전용(grade read).

재료는 키워드 풀(`keyword_pool.db`, 수집기가 하루 1번)과 네이버 데이터랩. 숫자·판정·순위는 코드,
AI 요약 한 줄만 LLM(`explain` 숫자 검증).

1. 급상승 검색어: `rising_check`(30)개를 출처를 섞어 고르고(`keyword_candidates`: 새 키워드·
   검색량이 는 것·trend_candidates 저장분·회사 그룹·풀, 그날 처음 고른 것을 그날 그대로)
   데이터랩 일별 비율(최근 63일, 월 검색량이 비슷한 것끼리 5개씩 한 요청)로
   `trend_scan.judge_series` 판정(같은 함수·커넥터 하루 캐시·trend_age `calls_by_caller` 예산 공유,
   호출자 `keyword_trend`, 브리프와 같은 우선 몫). 캐시에 있는 질의는 한도와 상관없이 쓴다.
   판정은 keyword_pool `rising_judgments`에 남겨 그날 다시 부르지 않고(일간·주간 창 공용), 한도로
   못 본 키워드는 3일 안 직전 판정을 쓴다(`judged_on`·`stale_reason`). 줄마다 level(rising·warming·
   normal·unknown)·points·baseline·candidate_sources·why_line. 정렬은 level → 배수 → 월 검색량.
   - 일별 추정 = 월간 검색량 × (그날 비율 ÷ 최근 30일 비율 합)
   - 증가량(day) = 어제 추정 − 지난 4주 같은 요일 추정 평균
   - 증가량(week) = 최근 7일 추정 합 − 그 전 4주 주별 합 평균
   - 관련 뉴스 수 = Google 인기 검색어 RSS(trending_news)에서 키워드가 맞는 뉴스만.
     HUB 블로그·카페·뉴스 API가 켜져 있으면 확산(블로그 7일·카페 전체·뉴스 3개)도 붙인다
2. 새로 나타난 키워드: 최근 4일 날짜별로, 그날 풀에 처음 들어왔고 지난 30일 풀에 없던 것
   (월간 검색량 순)
3. 이달 시즌 키워드: 풀 상위 `season_check`(20)개의 데이터랩 지난 12개월 월별 비율(지난달까지).
   배수 = 작년 이달 비율 ÷ 12개월 평균(≥ `season_min` 1.5).
   월 검색량 = 월간 검색량 × (작년 이달 비율 ÷ 지난달 비율).
   몰리는 시기 = 작년 이달~다음 달 일별 비율이 그 기간 평균의 `peak_ratio`(1.5)배를 넘는
   가장 긴 연속 구간 (없으면 "-"). 묶음 = 공용 분류기 분류명
4. 우리 상품 = product_link 기존 기준(content_recommend `[recommend]`) 통과 상품 수.
   없으면 "없음 · 들여올 후보"
5. AI 요약 = explain 한 번(위 숫자만)

검색광고 키가 없으면 검색량·추정·증가 칸은 None + 이유이고 배수·판정만 준다.
"""

from __future__ import annotations

import json
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from ax_contracts.schemas import Evidence
from ax_contracts.schemas.data import (
    KeywordNews,
    KeywordTrendInput,
    KeywordTrendOutput,
    KeywordVolume,
    NewKeyword,
    NewKeywordDay,
    RisingKeyword,
    SeasonGroup,
    SeasonKeyword,
    TrendKeywordGroup,
    TrendScanInput,
)
from data_agent import keyword_pool
from data_agent.connectors import ConnectorUnavailable
from data_agent.keyword_pool import PoolRow, SearchConfig, compact
from data_agent.skills import keyword_candidates as kc

KST = timezone(timedelta(hours=9))
CALLER = "keyword_trend"
NEW_DAYS = 4
NEW_LOOKBACK = 30
NO_VOLUME = "검색광고 키가 없어 검색량 없음 (키 등록 후 가능)"
NO_PRODUCT = "없음 · 들여올 후보"
NOT_JUDGED = "오늘 판정 못 함"  # 한도로 못 보고 3일 안 직전 판정도 없음


@dataclass(frozen=True)
class KeywordTrendResult:
    output: KeywordTrendOutput
    evidence: Evidence


def _today(now: datetime) -> date:
    return now.astimezone(KST).date()


# ---------------------------------------------------------------- 추정 (순수 계산)


def estimates(points: Sequence[tuple[date, float]], monthly: int, end: date) -> dict[date, float]:
    """일별 추정 = 월간 검색량 × (그날 비율 ÷ 최근 30일 비율 합). 비율 합이 0이면 빈 dict."""
    recent = [v for d, v in points if end - timedelta(days=29) <= d <= end]
    total = sum(recent)
    if total <= 0:
        return {}
    return {d: monthly * v / total for d, v in points}


def increase(
    est: Mapping[date, float], end: date, window: str
) -> tuple[float | None, float | None]:
    """(이번 추정, 증가량).

    day = 어제 − 같은 요일 4주 평균, week = 최근 7일 − 그 전 4주 주별 평균.
    """
    if window == "week":

        def week_sum(last: date) -> float | None:
            days = [last - timedelta(days=i) for i in range(7)]
            return sum(est[d] for d in days) if all(d in est for d in days) else None

        now_sum = week_sum(end)
        sums = [week_sum(end - timedelta(days=7 * k)) for k in range(1, 5)]
        before = [s for s in sums if s is not None]
        if now_sum is None or not before:
            return now_sum, None
        return now_sum, now_sum - statistics.fmean(before)
    if end not in est:
        return None, None
    before = [est[d] for k in range(1, 5) if (d := end - timedelta(days=7 * k)) in est]
    return est[end], (est[end] - statistics.fmean(before)) if before else None


def peak_window(points: Sequence[tuple[date, float]], ratio: float) -> tuple[date, date] | None:
    """평균의 `ratio`배를 넘는 가장 긴 연속 구간 (빠진 날이 있으면 끊긴다)."""
    values = [v for _, v in points]
    if not values or statistics.fmean(values) <= 0:
        return None
    line = statistics.fmean(values) * ratio
    best: tuple[date, date] | None = None
    run: list[date] = []
    for d, v in sorted(points):
        if v > line and (not run or (d - run[-1]).days == 1):
            run.append(d)
        elif v > line:
            run = [d]
        else:
            run = []
        if run and (best is None or (run[-1] - run[0]) > (best[1] - best[0])):
            best = (run[0], run[-1])
    return best


def new_keywords(pools: Mapping[date, Sequence[PoolRow]], today: date,
                 limit: int) -> list[NewKeywordDay]:  # fmt: skip
    """최근 4일 날짜별: 그날 풀에 있고 지난 30일 풀에는 없던 키워드 (월간 검색량 순)."""
    out: list[NewKeywordDay] = []
    for k in range(NEW_DAYS):
        day = today - timedelta(days=k)
        rows = pools.get(day)
        if rows is None:
            # 오늘은 수집기가 아직 안 채운 것, 지난 날은 그날 수집이 없었던 것
            why = "아직 집계 전이에요" if day == today else "그날 키워드 풀 기록이 없어요"
            out.append(NewKeywordDay(day=day, status="pending", reason=why))
            continue
        before = [d for d in pools if day - timedelta(days=NEW_LOOKBACK) <= d < day]
        if not before:
            out.append(NewKeywordDay(day=day, reason="지난 30일 풀 기록이 없어 비교할 수 없음"))
            continue
        seen = {compact(r.keyword) for d in before for r in pools[d]}
        fresh = [r for r in rows if compact(r.keyword) not in seen]
        fresh.sort(key=lambda r: (-(r.total or 0), r.keyword))
        out.append(NewKeywordDay(day=day, items=[
            NewKeyword(keyword=r.keyword, monthly_volume=r.total,
                       volume_below_10=r.pc_below_10 or r.mobile_below_10)
            for r in fresh[:limit]
        ]))  # fmt: skip
    return out


def _volume(row: PoolRow) -> KeywordVolume | None:
    if row.source != "related":
        return None
    comp = row.competition if row.competition in ("low", "mid", "high") else None
    return KeywordVolume(pc=row.pc, mobile=row.mobile, pc_below_10=row.pc_below_10,
                         mobile_below_10=row.mobile_below_10, competition=comp)  # type: ignore[arg-type]  # fmt: skip


# ---------------------------------------------------------------- 데이터랩 (예산 공유)


class Datalab:
    """데이터랩 호출: 커넥터 하루 캐시 + trend_age 예산 표(`calls_by_caller`)를 같이 쓴다."""

    def __init__(self, connector: Any, env: Mapping[str, str], now: datetime,
                 age_store: Any = None) -> None:  # fmt: skip
        from data_agent.skills import trend_age

        self._ta = trend_age
        self.connector = connector
        self.env = env
        self.today = _today(now)
        self.config = trend_age.load_config(env)
        self._own = age_store is None
        self.store = age_store
        self.calls = 0
        self.capped = 0

    def _store(self) -> Any:
        if self.store is None:
            self.store = self._ta.AgeStore(self.env.get(self._ta.DB_ENV) or self._ta.DEFAULT_DB)
        return self.store

    def close(self) -> None:
        if self._own and self.store is not None:
            self.store.close()

    def _cached(self, query: TrendScanInput) -> Any:
        """커넥터 하루 캐시에 같은 질의가 있으면 그 응답 (없거나 캐시 기능이 없으면 None)."""
        lookup = getattr(self.connector, "cached", None)
        return lookup(query) if callable(lookup) else None

    async def series(
        self, keyword: str, start: date, end: date, unit: str = "date"
    ) -> tuple[list[tuple[date, float]] | None, str | None]:
        """(값, 못 받은 이유). 예산·한도면 "확인 안 함(한도)".

        그날 받아 둔 질의는 한도와 상관없이 쓴다."""
        got = await self.series_many([keyword], start, end, unit)
        return got[keyword]

    async def series_many(
        self, keywords: Sequence[str], start: date, end: date, unit: str = "date"
    ) -> dict[str, tuple[list[tuple[date, float]] | None, str | None]]:
        """키워드 최대 5개를 한 요청으로 (그룹마다 자기 추이). {키워드: (값, 못 받은 이유)}.

        네이버 값은 요청 안 최댓값 기준 상대값이지만, 판정은 키워드마다 자기 평소 값과 비교하므로
        묶어도 같다. 커넥터 하루 캐시에 있으면 한도 검사 없이 쓰고, 없을 때만 한도를 본다.
        예산 기록은 실제 호출 수로 남긴다."""
        words = list(dict.fromkeys(keywords))
        if self.connector is None:
            return {k: (None, "네이버 데이터랩 커넥터가 없음") for k in words}
        groups = [TrendKeywordGroup(group_name=k, keywords=[k]) for k in words]
        query = TrendScanInput(keyword_groups=groups, start_date=start, end_date=end,
                               time_unit=unit)  # type: ignore[arg-type]  # fmt: skip
        response = self._cached(query)
        if response is None:
            store = self._store()
            if self._ta._cap_reason(store, self.today, 1, CALLER, self.config, self.connector):  # noqa: SLF001
                self.capped += len(words)
                return {k: (None, "확인 안 함(한도)") for k in words}
            before = getattr(self.connector, "calls_today", None)
            try:
                response = await self.connector.search_trend(query)
            except ConnectorUnavailable as error:
                store.add_calls(self.today, 1, CALLER)
                self.calls += 1
                why = f"네이버 검색어 트렌드를 읽지 못함 [{error.code}]"
                return {k: (None, why) for k in words}
            after = getattr(self.connector, "calls_today", None)
            used = (after - before) if isinstance(before, int) and isinstance(after, int) else 1
            store.add_calls(self.today, used, CALLER)
            self.calls += used
        by_name = {s.group_name: s for s in response.series}
        out: dict[str, tuple[list[tuple[date, float]] | None, str | None]] = {}
        for k in words:
            s = by_name.get(k)
            if s is None or not s.points:
                out[k] = (None, "자료 없음: 네이버가 값을 주지 않음 (검색량이 적음)")
            else:
                out[k] = (list(s.points), None)
        return out


# ---------------------------------------------------------------- 뉴스 (Google RSS)


def rss_news(env: Mapping[str, str], keywords: Sequence[str]) -> dict[str, list[KeywordNews]]:
    """최신 인기 검색어 스냅샷의 관련 뉴스 중 키워드가 맞는 것.

    맞음 = 검색어 이름 또는 뉴스 제목에 키워드가 들어 있음 (공백·대소문자 무시).
    """
    from data_agent.skills.trend_candidates import trending_db
    from data_agent.trending_store import latest_trending, news_links

    path = trending_db(env)
    snap = latest_trending(path)
    if snap is None:
        return {}
    links = news_links(path, snap.geo, snap.collected_at, [i.keyword for i in snap.items])
    out: dict[str, list[KeywordNews]] = {}
    for kw in keywords:
        key = compact(kw)
        if len(key) < 2:
            continue
        found: list[KeywordNews] = []
        for name, items in links.items():
            for item in items:
                title = str(item.get("title") or "")
                if key in compact(name) or key in compact(title):
                    found.append(KeywordNews(title=title, source=item.get("source"),
                                             url=item.get("url")))  # fmt: skip
        if found:
            out[kw] = found
    return out


# ---------------------------------------------------------------- 상품 (product_link 기존 기준)


async def link_products(names: Sequence[str], llm: Any, env: Mapping[str, str],
                        catalog: Any) -> tuple[dict[str, list[str]], str | None]:  # fmt: skip
    """키워드 → 연결 상품 이름 (기준 통과만). 못 읽으면 ({}, 이유)."""
    from data_agent.skills.content_recommend import load_config as recommend_config
    from data_agent.skills.product_link import pick_links, similarities, topic_text

    if catalog is None:
        return {}, "회사 상품 목록을 주는 커넥터가 없음"
    if catalog.reason or not catalog.products:
        return {}, catalog.reason or "회사 상품 목록이 비어 있음"
    rec = recommend_config(env)
    sims = await similarities(llm, {n: topic_text([n], []) for n in names}, catalog.products,
                              catalog.categories, enabled=rec.use_similarity)  # fmt: skip
    out = {}
    for n in names:
        links = pick_links([n], catalog.products, catalog.categories, sims.by_topic.get(n),
                           min_similarity=rec.min_similarity, limit=rec.max_products,
                           top_only=rec.link_top_only, min_margin=rec.min_margin)  # fmt: skip
        out[n] = [p.name for p in links]
    return out, None


# ---------------------------------------------------------------- skill


async def keyword_trend(
    request: KeywordTrendInput,
    llm: Any,
    *,
    env: Mapping[str, str],
    connector: Any = None,
    search: Any = None,
    searchad_configured: bool | None = None,
    catalog_provider: Any = None,
    category_cache: Any = None,
    explain_cache: Any = None,
    now: datetime | None = None,
    age_store: Any = None,
    candidates: Any = None,
) -> KeywordTrendResult:
    """검색 트렌드 4블록. 상태를 바꾸지 않는다 (네이버 호출 수만 예산 표에 남김).

    `candidates`: trend_candidates 최신 저장분([결과, 출력, 근거], 핸들러가 응답 캐시에서 읽어
    넘긴다. 다시 계산하지 않는다). 급상승 후보 출처 youtube·google로 쓴다.
    """
    from data_agent.connectors.naver_searchad import has_keys
    from data_agent.explain import explain
    from data_agent.search_spread import spread
    from data_agent.skills.topic_category import TopicInput, classify_topics
    from data_agent.skills.trend_scan import REQUEST_DAYS, yesterday_kst

    now = now or datetime.now(UTC)
    today = _today(now)
    config: SearchConfig = keyword_pool.load_config(env)
    out = KeywordTrendOutput(window=request.window, generated_at=now, season_month=today.month)
    queries: list[str] = []
    configured = has_keys(env) if searchad_configured is None else searchad_configured
    path = env.get(keyword_pool.DB_ENV) or keyword_pool.DEFAULT_DB
    pool_day, pool = keyword_pool.latest(path)
    out.pool_day, out.pool_size = pool_day, len(pool)
    hint_only = bool(pool) and all(r.source != "related" for r in pool)
    if not configured:
        out.searchad_status, out.searchad_reason = "key_missing", NO_VOLUME
    elif hint_only:
        out.searchad_status = "failed"
        out.searchad_reason = "키는 있지만 오늘 풀이 힌트뿐 (검색광고를 읽지 못함)"
    else:
        out.searchad_status = "connected"
    if search is not None and getattr(search, "configured", False):
        out.spread_status = "connected"
    else:
        from data_agent.connectors.naver_search import NOT_ENABLED

        out.spread_status, out.spread_reason = "unavailable", NOT_ENABLED
    # 새로 나타난 키워드 (풀 기록만, 외부 호출 없음)
    pools = keyword_pool.by_day(path, today - timedelta(days=NEW_LOOKBACK + NEW_DAYS), today)
    out.new_keywords = new_keywords(pools, today, config.max_new)
    if not pool:
        out.reason = "키워드 풀이 아직 없음 (수집기가 하루 1번 채운다)"
        out.summary = None
        return KeywordTrendResult(out, Evidence(queries=queries, data_summary=out.reason,
                                                collected_at=now))  # fmt: skip
    lab = Datalab(connector, env, now, age_store)
    try:
        # 1. 급상승 (v3: 후보를 섞어 고르고, 5개씩 묶어 부르고, 그날 판정은 다시 쓴다)
        end = yesterday_kst(now)
        start = end - timedelta(days=REQUEST_DAYS - 1)
        rising, video = await _rising(lab, path, pool, pools, out, config, configured,
                                      request.window, start, end, today, now, candidates,
                                      env)  # fmt: skip
        queries.append(f"naver search-trend date {lab.calls}번 {start}~{end} (caller={CALLER})")
        # 뉴스 (RSS 일치) + 확산 (HUB, 켜져 있을 때)
        news = rss_news(env, [r.keyword for r in rising])
        for r in rising:
            got = news.get(r.keyword, [])
            r.news_count, r.news_source = len(got), "google_rss"
            r.news = got[:3]
            if out.spread_status == "connected":
                r.spread = await spread(search, r.keyword, today)
                if r.spread.news:
                    r.news, r.news_source = list(r.spread.news), "naver_news"
        up = [r.keyword for r in rising if r.level in ("rising", "warming")]
        for r in rising:
            me = compact(r.keyword)
            together = [k for k in up if compact(k) != me
                        and (compact(k) in me or me in compact(k))]  # fmt: skip
            r.why_line = kc.why_line(r.news, video.get(r.keyword), together)
        out.rising = rising

        # 3. 시즌
        season = await _season(lab, pool[: config.season_check], today, config, configured)
        out.season, out.season_reason = season
        # 분류 묶음 (풀에 없으면 공용 분류기, 저장 재사용)
        todo = [s.keyword for s in out.season if not s.group]
        if todo:
            try:
                got = await classify_topics([TopicInput(k) for k in todo], llm,
                                            cache=category_cache)  # fmt: skip
            except Exception:  # noqa: BLE001
                got = {}
            for s in out.season:
                if not s.group and (c := got.get(s.keyword)) is not None:
                    s.group = c.category
        groups: dict[str, int] = {}
        for s in out.season:
            groups[s.group or "기타"] = groups.get(s.group or "기타", 0) + 1
        out.season_groups = [SeasonGroup(name=k, count=v)
                             for k, v in sorted(groups.items(), key=lambda kv: -kv[1])]  # fmt: skip

        # 4. 상품
        names = list(dict.fromkeys([r.keyword for r in out.rising if r.status != "unknown"]
                                   + [s.keyword for s in out.season]))  # fmt: skip
        linked: dict[str, list[str]] = {}
        why: str | None = None
        if names:  # 상품 목록은 연결할 키워드가 있을 때만 읽는다
            catalog = await catalog_provider() if catalog_provider is not None else None
            linked, why = await link_products(names, llm, env, catalog)
        for r in out.rising:
            _set_products(r, linked, why)
        for s in out.season:
            _set_products(s, linked, why)
    finally:
        lab.close()
    out.naver_calls = lab.calls
    if lab.capped:
        out.limits.append(
            f"데이터랩 하루 예산(trend_age 호출 표 공유)에 닿아 {lab.capped}개는 확인 안 함"
        )
    if out.spread_status != "connected" and out.spread_reason:
        out.limits.append(f"블로그·카페·뉴스: {out.spread_reason}")

    # 5. AI 요약 (위 숫자만, 숫자 검증)
    said = await explain("keyword_trend", _facts(out), llm, explain_cache)
    out.summary, out.summary_fallback = said.summary, said.fallback
    out.summary_reason = said.reason
    out.status = "known"
    up = [r for r in out.rising if r.status == "rising"]
    note = (f"풀 {out.pool_size}개({out.pool_day}) 중 {len(out.rising)}개 판정, 급상승 {len(up)}개,"
            f" 시즌 {len(out.season)}개, 데이터랩 {out.naver_calls}번")  # fmt: skip
    return KeywordTrendResult(out, Evidence(queries=queries, data_summary=note, collected_at=now))


def _volume_reason(row: PoolRow | None, configured: bool) -> str:
    if not configured:
        return NO_VOLUME
    if row is not None and (row.pc_below_10 or row.mobile_below_10):
        return "검색량이 10 미만"
    return "검색량 없음" if row is not None else "키워드 풀에 없는 후보라 검색량 없음"


def _pool_volume(row: PoolRow | None) -> int | None:
    return row.total if row is not None and row.source == "related" else None


RisingResult = tuple[list[RisingKeyword], dict[str, int]]


async def _rising(lab: Datalab, path: Any, pool: Sequence[PoolRow],
                  pools: Mapping[date, Sequence[PoolRow]], out: KeywordTrendOutput,
                  config: SearchConfig, configured: bool, window: str, start: date, end: date,
                  today: date, now: datetime, stored_candidates: Any,
                  env: Mapping[str, str]) -> RisingResult:  # fmt: skip
    """급상승 줄들과 {키워드: YouTube 인기 차트 영상 수}.

    후보: 그날 처음 고른 것(저장)을 그대로. 판정: 그날 판정이 있으면 다시 부르지 않고, 없는 것만
    월 검색량이 비슷한 것끼리 5개씩 묶어 부른다. 한도로 못 본 것은 3일 안 직전 판정을 쓴다.
    """
    from data_agent.connectors.naver_datalab import TrendSeries
    from data_agent.skills.trend_scan import default_groups, judge_series

    by_key = {compact(r.keyword): r for r in pool}
    store = keyword_pool.PoolStore(path)
    try:
        saved = store.candidates(today)
        external, had = kc.external_from(stored_candidates)
        video = {k: v for k, s, v in external if s == "youtube" and v}
        if saved:
            cands = [kc.Candidate(k, s) for k, s in saved]
            for c in cands:
                c.video_count = next((v for k, v in video.items() if compact(k) == compact(
                    c.keyword)), None)  # fmt: skip
        else:
            fresh = sorted((i for d in out.new_keywords for i in d.items),
                           key=lambda i: (-(i.monthly_volume or 0), i.keyword))  # fmt: skip
            before = [d for d in pools if d < today]
            prev = pools[max(before)] if before else []
            company = [g.group_name for g in default_groups(env)][: config.cand_company]
            cands = kc.pick(new=[i.keyword for i in fresh],
                            up=kc.related_up(pool, prev, config.related_up_ratio),
                            external=external, company=company, pool=pool,
                            config=config)  # fmt: skip
            store.save_candidates(today, [(c.keyword, c.sources) for c in cands])
        if not had:
            out.limits.append(kc.NO_EXTERNAL)
        out.candidate_counts = kc.counts(cands)
        # 판정: 오늘 것은 다시 쓰고, 없는 것만 부른다 (일간·주간 창이 같은 판정을 쓴다)
        recent = store.judgments(today - timedelta(days=kc.STALE_DAYS), today)
        todays = {compact(r["keyword"]): r for r in recent if r["day"] == today.isoformat()}
        need = [c.keyword for c in cands if compact(c.keyword) not in todays]
        volumes = {c.keyword: _pool_volume(by_key.get(compact(c.keyword))) for c in cands}
        for chunk in kc.volume_chunks(need, volumes):
            got = await lab.series_many(chunk, start, end)
            for k, (points, why) in got.items():
                if why == "확인 안 함(한도)" or (points is None and why and "읽지 못함" in why):
                    continue  # 한도·오류는 저장하지 않는다 (다음에 다시)
                if points is None:
                    store.save_judgment(today, k, "unknown", None, None, [], now, reason=why)
                else:
                    v = judge_series(TrendSeries(k, (k,), tuple(points)), start, end)
                    ev = v.evidence
                    store.save_judgment(today, k, v.status, v.ratio, ev.baseline_value, points,
                                        now, ev.score, v.unknown_reason)  # fmt: skip
                todays[compact(k)] = {"day": today.isoformat(), "keyword": k}
            if any(why == "확인 안 함(한도)" for _, why in got.values()):
                for k, (_, why) in got.items():
                    if why == "확인 안 함(한도)":
                        todays.setdefault(compact(k), {"capped": why})
        recent = store.judgments(today - timedelta(days=kc.STALE_DAYS), today)
    finally:
        store.close()
    todays_rows = {compact(r["keyword"]): r for r in recent if r["day"] == today.isoformat()}
    rising: list[RisingKeyword] = []
    stale_days: list[date] = []
    for c in cands:
        row = by_key.get(compact(c.keyword))
        judged = todays_rows.get(compact(c.keyword))
        if judged is None and (old := kc.latest_before(recent, c.keyword, today)) is not None:
            judged = old
            stale_days.append(date.fromisoformat(old["day"]))
        item = _row_item(c, row, judged, todays.get(compact(c.keyword)), config, configured,
                         window, end)  # fmt: skip
        rising.append(item)
    rising.sort(key=kc.sort_key)
    for n, r in enumerate(rising, start=1):
        r.rank = n
    if stale_days:
        out.judged_on = min(stale_days)
        out.stale_reason = f"데이터랩 하루 몫을 다 써서 {out.judged_on:%m-%d} 판정을 보여 줘요"
    return rising, video


def _row_item(c: kc.Candidate, row: PoolRow | None, judged: Mapping[str, Any] | None,
              today_mark: Mapping[str, Any] | None, config: SearchConfig, configured: bool,
              window: str, end: date) -> RisingKeyword:  # fmt: skip
    """후보 하나 → 화면 줄. 판정이 없으면 unknown(풀 검색량은 채운다)."""
    item = RisingKeyword(keyword=c.keyword, rank=1, status="unknown",
                         category=row.category if row else None,
                         volume=_volume(row) if row else None, monthly_volume=_pool_volume(row),
                         candidate_sources=c.sources)  # type: ignore[arg-type]  # fmt: skip
    if item.monthly_volume is None:
        item.volume_reason = _volume_reason(row, configured)
    if judged is None or "status" not in judged:
        capped = (today_mark or {}).get("capped")
        item.reason = f"{NOT_JUDGED} ({capped})" if capped else NOT_JUDGED
        item.increase_reason = "판정 못 해 증가 추정 없음"
        return item
    points = [(date.fromisoformat(d), float(v)) for d, v in json.loads(judged["points_json"])]
    item.status = judged["status"]
    item.ratio = judged["ratio"]
    item.score = judged["score"]
    item.reason = judged["reason"]
    item.baseline = judged["baseline"]
    item.points = [v for _, v in points][-28:]
    item.level = kc.level(item.status, item.ratio, config.warming_ratio)  # type: ignore[assignment]
    if item.status == "rising" and points:
        item.peak_end, item.faded = faded_of(c.keyword, points, end)
    if item.status == "unknown":
        item.increase_reason = "판정 못 해 증가 추정 없음"
    elif item.monthly_volume is None:
        item.increase_reason = item.volume_reason
    elif points:
        est, inc = increase(estimates(points, item.monthly_volume, end), end, window)
        item.estimate = round(est) if est is not None else None
        item.increase = round(inc) if inc is not None else None
        if inc is None:
            item.increase_reason = "비교할 지난 4주 값이 없음"
    return item


def faded_of(keyword: str, points: Sequence[tuple[date, float]],
             end: date) -> tuple[date | None, bool]:  # fmt: skip
    """저장된 판정 값(points)으로 정점 끝 날짜·끝난 급상승 여부를 다시 계산한다 (같은 판정 함수·
    같은 끝 날짜, 네이버를 다시 부르지 않음. 예전에 저장한 판정에도 붙는다)."""
    from data_agent.connectors.naver_datalab import TrendSeries
    from data_agent.skills.trend_scan import judge_series

    ordered = sorted(points)
    v = judge_series(TrendSeries(keyword, (keyword,), tuple(ordered)), ordered[0][0], end)
    if v.status != "rising":
        return v.evidence.peak_end, False
    return v.evidence.peak_end, v.evidence.faded


def _set_products(item: Any, linked: Mapping[str, list[str]], why: str | None) -> None:
    if why is not None:
        item.products_reason = why
        return
    names = linked.get(item.keyword)
    if names is None:
        return
    item.products, item.product_names = len(names), names[:3]
    if not names:
        item.products_reason = NO_PRODUCT


async def _season(lab: Datalab, rows: Sequence[PoolRow], today: date, config: SearchConfig,
                  configured: bool) -> tuple[list[SeasonKeyword], str | None]:  # fmt: skip
    """작년 이달 비율 ÷ 지난 12개월 평균 (지난달까지 12개월). 기준 이상만, 배수 큰 순."""
    first = today.replace(day=1)
    last_month_end = first - timedelta(days=1)
    start = first.replace(year=first.year - 1)
    target = start  # 작년 이달 1일
    out: list[SeasonKeyword] = []
    skipped = 0
    for row in rows:
        points, why = await lab.series(row.keyword, start, last_month_end, "month")
        if points is None or len(points) < 2:
            skipped += 1
            continue
        values = dict(points)
        month_value = values.get(target)
        latest = values.get(max(values))
        mean = statistics.fmean(values.values())
        if month_value is None or mean <= 0:
            continue
        ratio = round(month_value / mean, 2)
        if ratio < config.season_min:
            continue
        item = SeasonKeyword(keyword=row.keyword, group=row.category, ratio=ratio)
        total = row.total if row.source == "related" else None
        if total is not None and latest:
            item.month_volume = round(total * month_value / latest)
        else:
            item.volume_reason = NO_VOLUME if not configured else "검색량 없음"
        # 몰리는 시기: 작년 이달 1일 ~ 다음 달 말일 일별
        nxt = (target.replace(day=28) + timedelta(days=4)).replace(day=1)
        span_end = (nxt.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        daily, why = await lab.series(row.keyword, target, span_end)
        if daily is None:
            item.peak_reason = why
        elif (peak := peak_window(daily, config.peak_ratio)) is None:
            item.peak_reason = f"평균의 {config.peak_ratio:g}배를 넘는 구간 없음"
        else:
            item.peak_from, item.peak_to = peak[0].strftime("%m-%d"), peak[1].strftime("%m-%d")
        out.append(item)
    out.sort(key=lambda s: (-s.ratio, s.keyword))
    if out:
        return out, None
    why = f"{today.month}월에 평소의 {config.season_min:g}배 넘게 몰리는 키워드 없음"
    return out, why + (f" (못 본 키워드 {skipped}개)" if skipped else "")


def _facts(out: KeywordTrendOutput) -> dict[str, int | float | str]:
    # 끝난 급상승(faded)은 요약 숫자에 쓰지 않는다 (화면에는 "지난 급상승" 칩으로 남는다)
    up = [r for r in out.rising if r.status == "rising" and not r.faded]
    facts: dict[str, int | float | str] = {
        "급상승 수": len(up),
        "판정한 키워드 수": len(out.rising),
        "시즌 키워드 수": len(out.season),
    }
    for i, r in enumerate(up[:3], start=1):
        facts[f"급상승{i}"] = r.keyword
        facts[f"급상승{i} 배수"] = r.ratio or 0.0
        if r.products is not None:
            facts[f"급상승{i} 연결 상품 수"] = r.products
    for i, s in enumerate(out.season[:2], start=1):
        facts[f"시즌{i}"] = s.keyword
        facts[f"시즌{i} 배수"] = s.ratio
    fresh = sum(len(d.items) for d in out.new_keywords)
    facts["새 키워드 수(최근 4일)"] = fresh
    return facts
