"""판매 저장소 분석의 캐시 계산과 미리 계산 (11/18).

핸들러와 미리 계산(`warm`)이 같은 함수를 불러 캐시 키가 늘 같다. 함수마다 `sales_cache.cached`로
감싸고 계산은 각 skill 모듈 그대로다(읽기는 `SalesStore.reading`·`analysis` 읽기 연결). 새 skill도
`run(skill, store, tenant_id, inputs, catalog, now, compute)` 하나로 같은 캐시를 쓴다.

미리 계산 (`run_forever`)
- 데이터 AI 서버 안에서 저장소 버전(`SalesStore.data_version`)을 `WARM_POLL_SECONDS`마다 본다.
  수집기(`sales_collect`, 다른 프로세스)가 새 행을 저장해 버전이 오르면 화면이 처음 여는 기본 조회를
  한 번 계산해 캐시에 넣는다: 매출 분석(오늘·최근 7일·최근 30일), segment_options,
  segment_profile(첫 장르·90일), market_coverage(첫 인구 조합), event_proposal 후보(다음 달, LLM
  문장은 빼고 저장소 계산만)
- 미리 계산이 실패해도 로그만 남기고 다음 버전을 기다린다. 수집기는 다른 프로세스라 영향이 없다
- `DATA_SALES_WARM=0`이면 끈다
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from ax_contracts.schemas.data import (
    BasketLevel,
    BasketPairsOutput,
    ChannelValueOutput,
    MarketCoverageOutput,
    MetricCatalog,
    RetentionOutput,
    SalesAnalysisOutput,
    SalesPeriod,
    SegmentFavoritesOutput,
    SegmentOptionsOutput,
    SegmentPeriod,
    SegmentProfileOutput,
    SegmentTargetKind,
)
from data_agent.sales_cache import Computed, cached
from data_agent.sales_store import SalesStore
from data_agent.skills import basket_pairs as bp
from data_agent.skills import channel_value as cv
from data_agent.skills import event_proposal as ep
from data_agent.skills import retention as rt
from data_agent.skills import segment_analysis as seg
from data_agent.skills.sales_analysis import analyze

logger = logging.getLogger(__name__)
WARM_ENV = "DATA_SALES_WARM"
WARM_POLL_SECONDS = 30
SALES_PERIODS: tuple[SalesPeriod, ...] = ("today", "7d", "30d")


def catalog_key(cat: MetricCatalog) -> str:
    """카탈로그 판 + 설정 내용 해시 (같은 판 번호로 설정만 바꿔 끼워도 섞이지 않게)."""
    raw = cat.sales.model_dump_json() if cat.sales else ""
    version = cat.sales.version if cat.sales else "없음"
    return f"{version}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:12]}"


def run[T](  # noqa: PLR0913 - 캐시 키 재료와 계산
    skill: str,
    store: SalesStore,
    tenant_id: str,
    inputs: Mapping[str, Any],
    cat: MetricCatalog,
    now: datetime,
    compute: Callable[[], T],
) -> Computed[T]:
    """캐시 계산. 화면 요청은 예전 결과가 있으면 먼저 받고(뒤에서 갱신), 미리 계산(`warm`)은
    기다려 새로 계산한다."""
    return cached(
        skill, store, tenant_id, inputs, cat.id, catalog_key(cat), now, compute,
        stale_ok=not getattr(_WARMING, "on", False),
    )  # fmt: skip


_WARMING = threading.local()  # 이 스레드가 미리 계산 중인지


def sales_analysis(
    store: SalesStore, tenant_id: str, period: SalesPeriod, cat: MetricCatalog, now: datetime
) -> Computed[SalesAnalysisOutput]:
    return run("sales_analysis", store, tenant_id, {"period": period}, cat, now,
               lambda: analyze(store, tenant_id, period, cat.sales, cat.id, now))  # fmt: skip


def segment_options(
    store: SalesStore, tenant_id: str, cat: MetricCatalog, now: datetime
) -> Computed[SegmentOptionsOutput]:
    return run("segment_options", store, tenant_id, {}, cat, now,
               lambda: seg.read_options(store, tenant_id, cat.sales, cat.id))  # fmt: skip


def segment_profile(  # noqa: PLR0913 - 입력 칸 그대로
    store: SalesStore, tenant_id: str, target_kind: SegmentTargetKind, target_code: str,
    attribute_keys: list[str], period: SegmentPeriod, cat: MetricCatalog, now: datetime,
) -> Computed[SegmentProfileOutput]:  # fmt: skip
    inputs = {"target_kind": target_kind, "target_code": target_code,
              "attribute_keys": list(attribute_keys), "period": period}  # fmt: skip
    return run("segment_profile", store, tenant_id, inputs, cat, now, lambda: seg.profile(
        store, tenant_id, target_kind, target_code, attribute_keys, period, cat.sales, cat.id,
        now,
    ))  # fmt: skip


def segment_favorites(  # noqa: PLR0913 - 입력 칸 그대로
    store: SalesStore, tenant_id: str, filters: dict[str, str], period: SegmentPeriod, top: int,
    cat: MetricCatalog, now: datetime,
) -> Computed[SegmentFavoritesOutput]:  # fmt: skip
    """우리 쇼핑몰 안 순위만 (바깥 출처는 커넥터 캐시로 따로 붙인다)."""
    inputs = {"filters": dict(filters), "period": period, "top": top}
    return run("segment_favorites", store, tenant_id, inputs, cat, now, lambda: seg.favorites(
        store, tenant_id, filters, period, top, cat.sales, cat.id, now,
    ))  # fmt: skip


def coverage_buyers(
    store: SalesStore, tenant_id: str, attribute_keys: list[str], cat: MetricCatalog, now: datetime
) -> Computed[MarketCoverageOutput]:
    """구매자 수 줄만 (인구는 KOSIS 커넥터 캐시로 따로 붙인다)."""
    return run("market_coverage", store, tenant_id, {"attribute_keys": list(attribute_keys)},
               cat, now, lambda: seg.coverage_buyers(store, tenant_id, attribute_keys, cat.sales,
                                                     cat.id, now))  # fmt: skip


def event_base(
    store: SalesStore, tenant_id: str, cat: MetricCatalog, now: datetime
) -> Computed[ep.Base]:
    """이벤트 제안 후보 (저장소 계산만. 시즌·LLM 문장·보내기 기록은 그 뒤에 따로)."""
    return run("event_proposal", store, tenant_id, {}, cat, now,
               lambda: ep.read_base(store, tenant_id, cat.sales, cat.id, now))  # fmt: skip


def retention(  # noqa: PLR0913 - 입력 칸 그대로
    store: SalesStore, tenant_id: str, cohorts: int, filters: dict[str, str], cat: MetricCatalog,
    now: datetime,
) -> Computed[RetentionOutput]:  # fmt: skip
    return run("retention", store, tenant_id, {"cohorts": cohorts, "filters": dict(filters)},
               cat, now, lambda: rt.retention(store, tenant_id, cohorts, filters, cat.sales,
                                              cat.id, now))  # fmt: skip


def basket_pairs(  # noqa: PLR0913 - 입력 칸 그대로
    store: SalesStore, tenant_id: str, level: BasketLevel, period: SegmentPeriod,
    filters: dict[str, str], max_pairs: int, cat: MetricCatalog, now: datetime,
) -> Computed[BasketPairsOutput]:  # fmt: skip
    inputs = {"level": level, "period": period, "filters": dict(filters), "max_pairs": max_pairs}
    return run("basket_pairs", store, tenant_id, inputs, cat, now, lambda: bp.basket(
        store, tenant_id, level, period, filters, max_pairs, cat.sales, cat.id, now,
    ))  # fmt: skip


def channel_value(
    store: SalesStore, tenant_id: str, period: SegmentPeriod, cat: MetricCatalog, now: datetime
) -> Computed[tuple[ChannelValueOutput, int]]:
    return run("channel_value", store, tenant_id, {"period": period}, cat, now,
               lambda: cv.channel_value(store, tenant_id, period, cat.sales, cat.id,
                                        now))  # fmt: skip


# ---------------------------------------------------------------- 미리 계산


def warm(store: SalesStore, tenant_id: str, cat: MetricCatalog, now: datetime) -> list[str]:
    """화면이 처음 여는 기본 조회를 캐시에 넣는다. 계산한 것 이름 목록 (실패한 것은 빼고 로그)."""
    done: list[str] = []
    jobs: list[tuple[str, Callable[[], object]]] = [
        (f"sales_analysis {p}", lambda p=p: sales_analysis(store, tenant_id, p, cat, now))
        for p in SALES_PERIODS
    ]
    jobs.append(("segment_options", lambda: segment_options(store, tenant_id, cat, now)))
    cfg = cat.sales
    if cfg is not None and cfg.genres:
        first = next(iter(cfg.genres))
        jobs.append(("segment_profile", lambda: segment_profile(
            store, tenant_id, "genre", first, [], "90d", cat, now)))  # fmt: skip
    if cfg is not None and cfg.population:
        keys = list(cfg.population[0].attribute_keys)
        jobs.append(("market_coverage", lambda: coverage_buyers(store, tenant_id, keys, cat, now)))
    jobs.append(("event_proposal", lambda: event_base(store, tenant_id, cat, now)))
    _WARMING.on = True
    try:
        for name, job in jobs:
            try:
                job()
                done.append(name)
            except Exception:  # noqa: BLE001 - 하나가 실패해도 나머지는 계산한다
                logger.warning("판매 분석 미리 계산 실패: %s", name, exc_info=True)
    finally:
        _WARMING.on = False
    return done


def warm_enabled(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return (env.get(WARM_ENV) or "1").strip() not in {"0", "false", "off"}


async def ensure_summary(store: SalesStore, tenant_id: str) -> bool:
    """구매자 요약 표가 비어 있으면(준비되지 않았으면) 채운다 (11/18). 채웠으면 True.

    데이터 AI가 시작할 때와 미리 계산 루프마다 부른다. 실패해도 분석은 예전 SQL로 돈다."""
    try:
        result = await asyncio.to_thread(store.ensure_summary, tenant_id)
    except Exception:  # noqa: BLE001 - 채우기가 실패해도 서버는 계속 돈다 (예전 방식으로 센다)
        logger.warning("구매자 요약 표를 채우지 못함", exc_info=True)
        return False
    if result is not None:
        logger.info("구매자 요약 표 채움: 구매자 %s명, 항목 %s줄", result.buyers, result.items)
    return result is not None


async def run_forever(
    get_store: Callable[[], SalesStore],
    get_catalog: Callable[[], MetricCatalog],
    tenant_id: str,
    poll_seconds: float = WARM_POLL_SECONDS,
) -> None:
    """저장소 버전이 바뀔 때마다(수집기가 새 행을 저장하면) 기본 조회를 미리 계산한다."""
    last: int | None = None
    while True:
        try:
            store = get_store()
            # 요약 표가 준비되지 않았으면(예전 파일·리셋 뒤 바꿔 끼운 파일) 먼저 채운다
            await ensure_summary(store, tenant_id)
            version = await asyncio.to_thread(store.data_version, tenant_id)
            if version and version != last:
                done = await asyncio.to_thread(
                    warm, store, tenant_id, get_catalog(), datetime.now(UTC)
                )
                logger.info("판매 분석 미리 계산 (저장소 버전 %s): %s", version, ", ".join(done))
                last = version
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - 미리 계산이 실패해도 서버·수집은 계속 돈다
            logger.warning("판매 분석 미리 계산을 건너뜀", exc_info=True)
        await asyncio.sleep(poll_seconds)
