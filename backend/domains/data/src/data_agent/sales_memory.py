"""원본 판매 기록을 디스크에 두지 않는 방식 (3단계 3-3 A안, 11/25).

`DATA_SALES_STORE=memory`면 판매 수집기(`sales_collect`)를 따로 띄우지 않는다. 데이터 AI 프로세스가
회사 사이트에서 판매 기록·고객을 받아 메모리 저장소(`SalesStore(":memory:")`)에만 두고, 분석 skill은
같은 저장소를 읽는다(분석 코드는 그대로). 일별 집계(`aggregates.db`)는 지금처럼 디스크에 남는다.

- 시작: 빈 메모리 저장소로 바로 뜬다(health를 막지 않는다). 뒤에서 처음부터 끝까지 받은 뒤
  바꿔 끼운다.
  다 받기 전에는 분석 화면이 "수집 전"으로 보인다(지어내지 않는다)
- 그 뒤: 같은 저장소에 `collect_once`로 이어 받고(커서는 메모리에), 하루 집계 훅을 부른다(입력 이름
  `company_server`)
- 재시작하면 메모리가 비므로 다시 처음부터 받는다. 보관한 `sales.db`는 읽지도 쓰지도 않는다
- 되돌리기: 값을 지우고 재시작 → 예전처럼 수집기가 `sales.db`에 이어 쓴다(커서는 `sales.db`의 것)
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent.sales_store import SalesStore

STORE_ENV = "DATA_SALES_STORE"  # "memory"면 원본을 디스크에 두지 않는다. 비움·"file" = 예전 그대로
MEMORY = "memory"
LOAD_ROUNDS = 50  # 처음 받기에서 collect_once를 부르는 최대 횟수 (한 번에 최대 10만 건)
NOTICE = (
    f"[sales] {STORE_ENV}=memory: 원본을 디스크에 저장하지 않음 — "
    "데이터 AI 안에서 받아 메모리에만 둠. sales.db는 그대로 보관"
)


def memory_mode(env: Mapping[str, str]) -> bool:
    return (env.get(STORE_ENV) or "").strip().lower() == MEMORY


class LoadFailed(RuntimeError):
    """처음 받기를 끝내지 못함 (오류 또는 횟수 초과). 메시지는 수집 줄과 같은 말."""


async def load_full(
    source: object,
    tenant: str,
    name: str,
    now: datetime | None = None,
    into: SalesStore | None = None,
) -> SalesStore:
    """새 메모리 저장소에 판매 기록·고객을 처음부터 끝까지 받는다. 끝내지 못하면 `LoadFailed`.

    `into`를 주면 새로 만들지 않고 그 저장소에 받는다(다른 회사가 이미 들어 있는 저장소, 11/26).
    끝까지 받기 전에는 마지막 수집 시각이 비어 있어 분석은 "수집 전"으로 보인다."""
    from data_agent.sales_collect import _customers_pending, collect_once

    store = into if into is not None else SalesStore(":memory:")
    try:
        for _ in range(LOAD_ROUNDS):
            result = await collect_once(source, store, tenant_id=tenant, source_name=name, now=now)  # type: ignore[arg-type]
            print(result.line(), flush=True)
            if result.errors:
                raise LoadFailed("; ".join(result.errors))
            if result.finished and not _customers_pending(store, tenant, name):
                store.ensure_summary(tenant)
                return store
        raise LoadFailed(f"{LOAD_ROUNDS}번 받아도 끝나지 않음")
    except BaseException:
        if into is None:
            store.close()
        raise


async def run_forever(
    env: Mapping[str, str],
    swap: Callable[[SalesStore], None],
    every: int | None = None,
    *,
    source_factory: Callable[[Mapping[str, str]], object] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    rounds: int | None = None,
) -> None:
    """처음 받기 → `swap` → 이어 받기·하루 집계를 `every`초마다.

    `rounds`는 시험용(그 횟수만 돈다)."""
    from data_agent.sales_collect import (
        TENANT_ENV,
        ErrorStreak,
        RunResult,
        aggregate_daily,
        collect_once,
        collect_other_companies,
        every_seconds,
        get_source,
        source_name,
        watched_companies,
    )

    tenant = (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()
    name = source_name(env)
    wait = every_seconds(env, every)
    make = source_factory or get_source
    print(NOTICE, flush=True)
    store: SalesStore | None = None
    loaded = False  # 캣북스 처음 받기를 끝냈는지
    streak = ErrorStreak()
    streaks: dict[str, ErrorStreak] = {}
    done = 0
    while rounds is None or done < rounds:
        done += 1
        try:
            source = make(env)
        except Exception as error:  # noqa: BLE001 - 출처를 못 만들어도 다음 주기에 다시
            print(f"[sales] 판매 기록 출처를 만들 수 없음 {type(error).__name__}", flush=True)
        else:
            if not loaded:
                try:
                    got = await load_full(source, tenant, name, clock(), into=store)
                    if store is None:
                        store = got
                        swap(store)
                    loaded = True
                    print("[sales] 메모리 저장소 처음 받기 끝: 분석이 이 저장소를 읽음", flush=True)
                except LoadFailed as error:
                    print(f"[sales] 메모리 저장소 처음 받기 실패: {error} (다음 주기에 다시)",
                          flush=True)  # fmt: skip
            else:
                result: RunResult = await collect_once(
                    source,
                    store,
                    tenant_id=tenant,
                    source_name=name,  # type: ignore[arg-type]
                )
                for line in streak.lines(result):
                    print(line, flush=True)
        if loaded and store is not None:
            # 메모리 저장소는 재시작 뒤 모든 주문의 synced_at이 새것이라 첫 하루 계산은 전체 날짜를
            # 다시 만든다(정확하고 조금 느림)
            aggregate_daily(store, tenant, env, clock())
        if store is None and watched_companies(env):
            # 캣북스 처음 받기가 끝나지 않아도 다른 회사는 기다리지 않는다(11/26 회사 분리):
            # 빈 메모리 저장소를 먼저 끼우고, 캣북스는 다음 주기부터 이 저장소에 이어 받는다
            store = SalesStore(":memory:")
            swap(store)
        if store is not None:
            # 연결된 다른 회사도 같은 메모리 저장소에 회사 id(tenant)로 받는다(첫 회차는 처음부터)
            await collect_other_companies(store, env, clock(), streaks, source_factory)
        if rounds is not None and done >= rounds:
            break
        await asyncio.sleep(wait)
