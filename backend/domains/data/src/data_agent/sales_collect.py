"""판매 기록 증분 수집 (한 번 또는 `--every 초`마다). run_all.sh가 data와 함께 띄운다.

    cd backend
    uv run python -m data_agent.sales_collect              # 한 번
    uv run python -m data_agent.sales_collect --every 300  # 5분마다 (Ctrl+C로 멈춤)
    uv run python -m data_agent.sales_collect --reset      # 저장소를 비우고 처음부터 다시 받기
    uv run python -m data_agent.sales_collect --rebuild-summary  # 구매자 요약 표를 다시 채우기

`--reset` (11/17): 판매 서버 자료를 갈아 끼웠는데 주문 updated_at이 과거라 이어 받기 커서 뒤라서
안 들어올 때 쓴다.
- `--every`와 같이 쓰면 종료 코드 2 (한 번만 실행)
- 수집기 잠금을 먼저 잡는다. 못 잡으면(수집기가 돌고 있으면) 아무것도 지우지 않고 종료 코드 1
- DB 파일은 지우지 않고 옆으로 옮긴다: `sales.db` → `sales.db.bak-<UTC YYYYmmddTHHMMSS>`
  (`-wal`·`-shm`도 같은 접미사). 파일이 없으면 그냥 진행
- 새 저장소는 새 저장소 생성 id를 받는다(`SalesStore.store_id`, 결과 캐시 키에 들어간다)
- 주문·고객을 처음부터 끝까지 받는다(`collect_once`를 다 받을 때까지 최대 `RESET_ROUNDS`번).
  오류가 나면 바로 멈추고 종료 코드 1, 백업은 그대로 두고 되돌리는 방법을 한 줄 적는다
- 끝나면 저장소에서 바로 센 요약을 적는다: 출처·백업·저장소 생성 id, 전체 주문 수와 접수 날짜 범위,
  고객 수, 어제까지 7일 결제 완료 건수·매출(매출 분석과 같은 정의), 유입 출처별 주문 수
- 데이터 AI 서버는 판매 저장소 연결을 연 채라 다시 띄워야 새 파일을 읽는다

- 출처: 회사 카탈로그의 `connector`(캣북스 `shop-api`)의 판매 기록 내보내기(계약 `shop_sales`).
  기존 shop_api 커넥터와 토큰(`DATA_TOKEN` 또는 토큰 명부의 data)을 그대로 쓴다
- 목 모드(`DATA_MOCK=1`·`AGENT_TEMPLATE_MOCK=1`, 테스트 전용): shop-api를 부르지 않고 고정 표본
  주문을 같은 커서 방식으로 준다
- 저장소의 커서부터 이어 받고 페이지마다 저장 + 커서를 남긴다. 같은 주문은 덮어쓴다(upsert)
- 끝까지 받았을 때만 마지막 수집 시각을 바꾼다. 실패하면 이유만 남기고 숫자를 지어내지 않는다
- 어떤 오류(연결 실패·저장소 오류·형식 오류 등)에도 프로세스는 끝나지 않고 다음 주기에 다시 한다.
  오류 종류와 이름만 로그 한 줄과 `sync_state.last_error`에 남긴다(예외 문장은 남기지 않는다).
  같은 오류가 연달아 나면 로그는 한 줄만 쓰고 바뀌거나 회복할 때 몇 번 연속이었는지 적는다.
  `--every` 없이 한 번 돌렸을 때 오류가 있으면 종료 코드 1
- 간격: `--every` 또는 `DATA_SALES_SYNC_SECONDS`(기본 300초, 최소 60초)
- 같은 DB에 수집기가 둘 돌지 않게 DB 옆 `sales_collect.lock`을 잡는다(`collector_lock`)
- 고객 내보내기(계약 `shop_customers`, 11/16): 같은 주기에 판매 기록 다음으로 받는다. 커서와
  sync_state 출처는 따로(`<출처>:customers`). 판매 서버에 그 주소가 없으면(404 `NOT_FOUND`) 실패가
  아니다: 그 출처의 `last_error`에 "판매 서버에 고객 내보내기 없음"만 남기고(분석이 이유로
  보여 준다) 판매 기록 수집은 성공으로 두며 한 번 실행의 종료 코드도 1로 만들지 않는다
- 가짜 출처(`DATA_SALES_SOURCE=fake`, 데모 전용): `sales_fake`가 같은 계약 모양으로 만든다.
  sync_state 출처 이름은 `fake`라 실제 판매 서버 커서와 섞이지 않는다. 기본은 꺼짐(공용 서버 251은
  켜지 않는다). 테스트 전용 목 모드와는 따로다
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError

from ax_contracts.data_access.shop_customers import ShopCustomersExportResult
from ax_contracts.data_access.shop_sales import (
    SalesOrderLine,
    SalesOrderRefund,
    SalesOrderRow,
    ShopSalesExportResult,
)
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent import catalog, collector_lock
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.shop_api import (
    CUSTOMERS_EXPORT_PAGE,
    NOT_FOUND,
    SALES_EXPORT_PAGE,
    ShopApiConnector,
)
from data_agent.sales_store import (
    DB_ENV,
    DEFAULT_DB,
    FAKE_SOURCE,
    SalesStore,
    customers_source,
)

EVERY_ENV = "DATA_SALES_SYNC_SECONDS"
TENANT_ENV = "DATA_SALES_TENANT_ID"
SOURCE_ENV = "DATA_SALES_SOURCE"  # 비움 = 실제 판매 서버, "fake" = 가짜 출처(데모 전용)
NO_CUSTOMERS_EXPORT = "사이트 서버에 고객 내보내기 없음"
NOT_ALL = "한 번에 다 받지 못함 (다음 수집에 이어서)"
DEFAULT_EVERY_SECONDS = 300
MIN_EVERY_SECONDS = 60  # 판매 서버에 부담 주지 않게
MAX_PAGES = 200  # 한 번에 받는 최대 페이지 (500건 × 200 = 10만 건). 넘으면 다음 수집이 이어 받는다
RESET_ROUNDS = 10  # --reset이 다 받을 때까지 collect_once를 부르는 최대 횟수
RESET_DAYS = 7  # --reset 요약의 "어제까지 7일"
NO_SOURCE = "없음"  # 유입 출처가 없는 주문


class SalesSource(Protocol):
    async def sales_export(
        self, cursor: str | None = None, limit: int = SALES_EXPORT_PAGE
    ) -> ShopSalesExportResult: ...


class CustomersSource(Protocol):
    """고객 내보내기가 있는 출처. 없는 출처는 이 메서드가 없다(`NO_CUSTOMERS_EXPORT`)."""

    async def customers_export(
        self, cursor: str | None = None, limit: int = CUSTOMERS_EXPORT_PAGE
    ) -> ShopCustomersExportResult: ...


@dataclass
class RunResult:
    attempted_at: datetime
    pages: int = 0
    saved: int = 0
    dropped: int = 0
    finished: bool = False  # 끝까지 받았는지
    errors: list[str] = field(default_factory=list)
    customers_pages: int = 0
    customers_saved: int = 0
    # 실패는 아니지만 남길 것 (예: 판매 서버에 고객 내보내기 없음). 종료 코드에 넣지 않는다
    notes: list[str] = field(default_factory=list)

    def line(self) -> str:
        tail = f" / 실패: {'; '.join(self.errors)}" if self.errors else ""
        more = "" if self.finished or self.errors else " (다음 수집에 이어서)"
        customers = f", 고객 {self.customers_saved}명" if self.customers_pages else ""
        notes = f" / 참고: {'; '.join(self.notes)}" if self.notes else ""
        return (
            f"[sales] {self.attempted_at.isoformat(timespec='seconds')} 페이지 {self.pages}, "
            f"저장 {self.saved}건, 버림 {self.dropped}건{customers}{more}{notes}{tail}"
        )


async def collect_once(
    source: SalesSource,
    store: SalesStore,
    *,
    tenant_id: str = TENANT_ID_DEFAULT,
    source_name: str = "shop-api",
    limit: int = SALES_EXPORT_PAGE,
    max_pages: int = MAX_PAGES,
    now: datetime | None = None,
    customers_limit: int = CUSTOMERS_EXPORT_PAGE,
) -> RunResult:
    """커서부터 끝까지(또는 `max_pages`까지) 받아 저장한다. 이어서 고객 내보내기도 받는다."""
    attempted = (now or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    result = RunResult(attempted)
    try:
        cursor = store.sync_state(tenant_id, source_name).cursor
        while result.pages < max_pages:
            page = await source.sales_export(cursor, limit)
            result.pages += 1
            saved = store.upsert(tenant_id, page.orders, attempted)
            result.saved += saved.saved
            result.dropped += saved.dropped_orders + saved.dropped_parts
            if page.next_cursor and page.next_cursor != cursor:
                cursor = page.next_cursor
                store.save_cursor(tenant_id, source_name, cursor)
            if not page.has_more:
                result.finished = True
                break
    except ConnectorUnavailable as error:
        result.errors.append(f"판매 기록 [{error.code}]")
    except Exception as error:  # noqa: BLE001 - 수집기는 어떤 오류에도 다음 주기를 돈다
        result.errors.append(describe(error))
    error_text = "; ".join(result.errors) if result.errors else None
    if not result.finished and not result.errors:
        error_text = NOT_ALL
    try:
        store.finish_sync(tenant_id, source_name, attempted, error_text)
    except Exception as error:  # noqa: BLE001 - 상태를 못 남겨도 수집기는 계속 돈다
        result.errors.append(f"수집 상태 기록 실패: {describe(error)}")
    await _collect_customers(
        source, store, result, tenant_id=tenant_id, source_name=customers_source(source_name),
        limit=customers_limit, max_pages=max_pages,
    )  # fmt: skip
    return result


async def _collect_customers(
    source: SalesSource,
    store: SalesStore,
    result: RunResult,
    *,
    tenant_id: str,
    source_name: str,
    limit: int,
    max_pages: int,
) -> None:
    """고객 내보내기를 자기 커서부터 이어 받는다. 판매 기록과 같은 오류 처리(끝나지 않음).

    출처에 고객 내보내기가 없거나 판매 서버가 404(`NOT_FOUND`)면 실패가 아니라 `notes`와 그
    출처의 `last_error`에 `NO_CUSTOMERS_EXPORT`만 남긴다.
    """
    attempted = result.attempted_at
    fetch = getattr(source, "customers_export", None)
    errors: list[str] = []
    finished = False
    if fetch is None:
        result.notes.append(NO_CUSTOMERS_EXPORT)
    else:
        try:
            cursor = store.sync_state(tenant_id, source_name).cursor
            pages = 0
            while pages < max_pages:
                page: ShopCustomersExportResult = await fetch(cursor, limit)
                pages += 1
                result.customers_pages += 1
                result.customers_saved += store.upsert_customers(
                    tenant_id, page.customers, attempted
                )
                if page.next_cursor and page.next_cursor != cursor:
                    cursor = page.next_cursor
                    store.save_cursor(tenant_id, source_name, cursor)
                if not page.has_more:
                    finished = True
                    break
        except ConnectorUnavailable as error:
            if error.code == NOT_FOUND:
                result.notes.append(NO_CUSTOMERS_EXPORT)
            else:
                errors.append(f"고객 기록 [{error.code}]")
        except Exception as error:  # noqa: BLE001 - 수집기는 어떤 오류에도 다음 주기를 돈다
            errors.append(f"고객 기록: {describe(error)}")
    result.errors.extend(errors)
    if NO_CUSTOMERS_EXPORT in result.notes:
        error_text: str | None = NO_CUSTOMERS_EXPORT
    elif errors:
        error_text = "; ".join(errors)
    else:
        error_text = None if finished else NOT_ALL
    try:
        store.finish_sync(tenant_id, source_name, attempted, error_text)
    except Exception as error:  # noqa: BLE001 - 상태를 못 남겨도 수집기는 계속 돈다
        text = f"수집 상태 기록 실패: {describe(error)}"
        if text not in result.errors:  # 판매 기록 쪽과 같은 저장소 오류는 한 번만 적는다
            result.errors.append(text)


def describe(error: BaseException) -> str:
    """예외를 로그·`last_error`에 남길 짧은 이유로. 예외 문장은 넣지 않는다(토큰·쿼리값이 섞일 수
    있어서). 종류와 이름만 남긴다."""
    if isinstance(error, sqlite3.Error):
        what = "판매 저장소 오류"
    elif isinstance(error, ValidationError):
        what = f"판매 기록 형식 오류 ({error.error_count()}개 칸)"
    else:
        what = "예상 못 한 오류"
    return f"{what} [{type(error).__name__}]"


class ErrorStreak:
    """연속 같은 오류는 로그 한 줄만 쓰고 횟수를 센다. 바뀌거나 회복하면 몇 번이었는지 한 줄."""

    def __init__(self) -> None:
        self.key: tuple[str, ...] = ()
        self.count = 0

    def lines(self, result: RunResult) -> list[str]:
        key = tuple(result.errors)
        if key and key == self.key:
            self.count += 1
            return []
        out = []
        if self.count > 1:
            out.append(f"[sales] 앞 오류가 {self.count}번 연속 났음: {'; '.join(self.key)}")
        self.key, self.count = key, (1 if key else 0)
        out.append(result.line())
        return out


# ---------------------------------------------------------------- 목 모드 (테스트 전용)
_MOCK_T0 = datetime(2026, 11, 1, 1, 0, tzinfo=UTC)


def mock_rows(count: int = 30) -> list[SalesOrderRow]:
    """목 모드 고정 표본: 구매자 10명, 도서·문구, 쿠폰·포인트·환불·취소·유입 출처가 섞인 주문."""
    rows: list[SalesOrderRow] = []
    for n in range(count):
        at = _MOCK_T0 + timedelta(hours=7 * n)
        qty = 1 + n % 3
        price = 18000 if n % 2 == 0 else 7500
        coupon = 2000 if n % 5 == 0 else 0
        points = 500 if n % 4 == 0 else 0
        rows.append(
            SalesOrderRow(
                order_id=f"ord_m{n:04d}",
                buyer_id=f"buy_m{n % 10:04d}",
                status="cancelled" if n % 13 == 12 else "paid",
                created_at=at,
                updated_at=at + timedelta(minutes=1),
                items_amount=qty * price,
                points_used=points,
                coupon_discount=coupon,
                src_source="naver" if n % 3 == 0 else None,
                src_medium="cpc" if n % 3 == 0 else None,
                payment_status="succeeded",
                lines=[
                    SalesOrderLine(
                        product_id="prd_0001" if n % 2 == 0 else "prd_0004",
                        category_code="book" if n % 2 == 0 else "stationery",
                        title="파이썬 입문서" if n % 2 == 0 else "무지 노트 3권 세트",
                        qty=qty,
                        unit_price=price,
                        coupon_share=coupon,
                        points_share=points,
                    )
                ],
                refunds=(
                    [SalesOrderRefund(amount=3000, refunded_at=at + timedelta(days=1))]
                    if n % 7 == 6
                    else []
                ),
            )
        )
    return sorted(rows, key=lambda r: (r.updated_at, r.order_id))


class MockSalesSource:
    """고정 표본을 `(updated_at, order_id)` 순서로 나눠 준다. 커서는 `mock:<다음 위치>`."""

    def __init__(self, rows: Sequence[SalesOrderRow] | None = None) -> None:
        self.rows = sorted(rows if rows is not None else mock_rows(), key=_order_key)
        self.calls: list[str | None] = []

    async def sales_export(
        self, cursor: str | None = None, limit: int = SALES_EXPORT_PAGE
    ) -> ShopSalesExportResult:
        self.calls.append(cursor)
        start = int(cursor.removeprefix("mock:")) if cursor else 0
        page = self.rows[start : start + limit]
        end = start + len(page)
        return ShopSalesExportResult(
            orders=list(page),
            next_cursor=f"mock:{end}" if page else cursor,
            has_more=end < len(self.rows),
            collected_at=datetime.now(UTC),
        )


def _order_key(row: SalesOrderRow) -> tuple[datetime, str]:
    return (row.updated_at, row.order_id)


def _mock(env: Mapping[str, str]) -> bool:
    return env.get("DATA_MOCK") == "1" or env.get("AGENT_TEMPLATE_MOCK") == "1"


def uses_fake(env: Mapping[str, str]) -> bool:
    """가짜 출처(데모 전용)를 쓰는지: `DATA_SALES_SOURCE=fake`이고 목 모드(테스트)가 아닐 때."""
    return (env.get(SOURCE_ENV) or "").strip().lower() == FAKE_SOURCE and not _mock(env)


def source_name(env: Mapping[str, str]) -> str:
    """sync_state 출처 이름. 가짜면 `fake`(실제 판매 서버 커서와 섞이지 않게), 아니면 커넥터."""
    return FAKE_SOURCE if uses_fake(env) else catalog.current().connector


def get_source(env: Mapping[str, str] = os.environ) -> SalesSource:
    """목 모드(테스트 전용)면 고정 표본, `DATA_SALES_SOURCE=fake`면 가짜 출처(데모 전용),
    아니면 회사 카탈로그의 커넥터(캣북스는 shop-api)."""
    if _mock(env):
        return MockSalesSource()
    if uses_fake(env):
        from data_agent.sales_fake import FakeSalesSource

        return FakeSalesSource.for_catalog(catalog.current().id)
    name = catalog.current().connector
    if name == "shop-api":
        from data_agent.clients.shop_client import shop_api

        return ShopApiConnector(shop_api(env))
    raise NotImplementedError(f"커넥터 {name}에 판매 기록 수집이 없다 (sales_collect.get_source)")


def every_seconds(env: Mapping[str, str], given: int | None) -> int:
    raw = given if given is not None else env.get(EVERY_ENV)
    try:
        value = int(raw) if raw not in (None, "") else DEFAULT_EVERY_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_EVERY_SECONDS
    return max(MIN_EVERY_SECONDS, value)


def db_path(env: Mapping[str, str]) -> Path:
    return Path(env.get(DB_ENV) or DEFAULT_DB)


def lock_path(env: Mapping[str, str]) -> Path:
    """중복 실행 잠금 파일 (판매 저장소 옆). trending·youtube 수집기와 같은 방식."""
    return db_path(env).with_name("sales_collect.lock")


AGGREGATE_ENV = "DATA_AGGREGATE_DAILY"  # "1"이면 하루 집계 계산 (3-1b). 기본 끔 (run_all.sh가 켠다)
AGGREGATE_AFTER = (0, 30)  # 한국 00:30 뒤 첫 회차에 (설계 5.1)


def aggregate_daily(store: SalesStore, tenant: str, env: Mapping[str, str], now: datetime) -> None:
    """일별 집계 하루 계산 (회사마다 하루 한 번). 실패해도 판매 수집은 그대로 이어 간다."""
    if env.get(AGGREGATE_ENV, "").strip() != "1":
        return
    from data_agent import catalog
    from data_agent.aggregate.daily import run_daily
    from data_agent.aggregate.store import AggregateStore
    from data_agent.aggregate.store import db_path as agg_db_path
    from data_agent.sales_quality import KST

    local = now.astimezone(KST)
    if (local.hour, local.minute) < AGGREGATE_AFTER:
        return
    try:
        agg = AggregateStore(agg_db_path(dict(env)))
        try:
            from data_agent.sales_memory import memory_mode

            run_daily(store, agg, tenant, catalog.current(env).sales, now,
                      input_name="company_server" if memory_mode(env) else "sales_db")  # fmt: skip
        finally:
            agg.close()
    except Exception as error:  # noqa: BLE001 - 집계 때문에 수집이 멈추지 않게
        print(f"[aggregate] 하루 계산을 시작하지 못함 {describe(error)}", flush=True)


def watched_companies(env: Mapping[str, str]) -> list[str]:
    """캣북스 말고 이번 회차에 판매 기록을 받을 회사: 회사 목록 중 회사 연결이 켜진 회사.

    캣북스는 예전처럼 `DATA_SALES_TENANT_ID`(기본 shop-demo)로 따로 받는다.
    목·가짜 출처(시험·데모)는 캣북스 하나만 받는다(가짜 출처는 회사 연결이 없다).
    회사 연결은 감지기 회사별(#1749)과 같은 기준.
    """
    if _mock(env) or uses_fake(env):
        return []
    from ax_agent_template import company_connection, company_scope
    from ax_agent_template.companies import company_ids

    out: list[str] = []
    try:
        ids = company_ids()
    except Exception as error:  # noqa: BLE001 - 회사 목록을 못 읽어도 캣북스 수집은 그대로
        print(f"[sales] 회사 목록을 읽지 못함 {describe(error)}", flush=True)
        return []
    for raw in ids:
        cid = company_scope.scope_id(raw)
        if cid == company_scope.CATBOOKS or cid in out:
            continue
        try:
            found = company_connection.read_connection(cid)
        except Exception:  # noqa: BLE001 - 연결 파일이 깨진 회사는 이번 회차에서 뺀다
            continue
        if found is not None and found.enabled:
            out.append(cid)
    return out


async def collect_company(  # noqa: PLR0913 - 저장소·회사·환경·시각·오류 줄·출처
    store: SalesStore,
    cid: str,
    env: Mapping[str, str],
    now: datetime,
    streaks: dict[str, ErrorStreak],
    source_factory: object | None = None,
) -> RunResult:
    """캣북스가 아닌 회사 하나의 판매 기록을 그 회사 연결로 받아 `tenant_id=회사 id`로 쌓고
    하루 집계한다.

    그 회사 범위(`company_scope.using`) 안에서 출처를 만들어 그 회사 연결·변환기 경로를 읽는다.
    예외를 내지 않는다(한 회사 오류가 다른 회사 수집을 막지 않게)."""
    from ax_agent_template import company_scope

    make = source_factory or get_source
    with company_scope.using(cid):
        try:
            source = make(env)  # type: ignore[operator]
            name = source_name(env)
        except Exception as error:  # noqa: BLE001 - 출처를 못 만들어도 다음 주기에 다시
            result = RunResult(now.astimezone(UTC).replace(microsecond=0))
            result.errors.append(f"판매 기록 출처를 만들 수 없음 {describe(error)}")
        else:
            result = await collect_once(source, store, tenant_id=cid, source_name=name)
        streak = streaks.setdefault(cid, ErrorStreak())
        for line in streak.lines(result):
            print(line.replace("[sales]", f"[sales {cid}]", 1), flush=True)
        aggregate_daily(store, cid, env, now)
    return result


async def collect_other_companies(
    store: SalesStore,
    env: Mapping[str, str],
    now: datetime,
    streaks: dict[str, ErrorStreak],
    source_factory: object | None = None,
) -> dict[str, RunResult]:
    """연결된 다른 회사를 모두 한 번씩 (캣북스 뒤에). 한 회사가 실패해도 다음 회사는 돈다."""
    out: dict[str, RunResult] = {}
    for cid in watched_companies(env):
        out[cid] = await collect_company(store, cid, env, now, streaks, source_factory)
    return out


def catbooks_tenant(env: Mapping[str, str] = os.environ) -> str:
    """캣북스 판매 저장소 tenant (`DATA_SALES_TENANT_ID`, 기본 shop-demo). 수집기·집계와 같은 값."""
    return (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()


def request_tenant(env: Mapping[str, str] = os.environ) -> str:
    """지금 요청 회사의 판매 저장소 tenant: 캣북스면 `DATA_SALES_TENANT_ID`(수집기와 같은 값),
    다른 회사면 그 회사 id(`collect_company`가 쓰는 값)."""
    from ax_agent_template import company_scope

    cid = company_scope.scope_id(company_scope.active())
    if cid == company_scope.CATBOOKS:
        return (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()
    return cid


async def run(every: int | None, env: Mapping[str, str]) -> int:
    store = SalesStore(db_path(env))
    tenant = (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()
    name = source_name(env)
    streak = ErrorStreak()
    streaks: dict[str, ErrorStreak] = {}
    try:
        while True:
            try:
                source = get_source(env)
            except Exception as error:  # noqa: BLE001 - 출처를 못 만들어도 다음 주기에 다시
                result = RunResult(datetime.now(UTC).replace(microsecond=0))
                result.errors.append(f"판매 기록 출처를 만들 수 없음 {describe(error)}")
            else:
                result = await collect_once(source, store, tenant_id=tenant, source_name=name)
            for line in streak.lines(result):
                print(line, flush=True)
            aggregate_daily(store, tenant, env, datetime.now(UTC))
            # 연결된 다른 회사(누비투어·스터디온 등)도 회사마다 받는다
            # (11/26: 수집이 캣북스 하나뿐이라 다른 회사 매출 분석이 늘 비었다)
            others = await collect_other_companies(store, env, datetime.now(UTC), streaks)
            if every is None:
                failed = result.errors or any(r.errors for r in others.values())
                return 1 if failed else 0
            await asyncio.sleep(every)
    finally:
        store.close()


# ---------------------------------------------------------------- --reset


def backup_files(db: Path, at: datetime) -> list[tuple[Path, Path]]:
    """DB와 -wal·-shm을 `<이름>.bak-<UTC 시각>`으로 옮긴다. 옮긴 (원래, 백업) 목록
    (없는 파일은 뺀다)."""
    suffix = f".bak-{at.astimezone(UTC):%Y%m%dT%H%M%S}"
    moved: list[tuple[Path, Path]] = []
    for path in (db, db.with_name(f"{db.name}-wal"), db.with_name(f"{db.name}-shm")):
        if path.exists():
            target = path.with_name(f"{path.name}{suffix}")
            path.rename(target)
            moved.append((path, target))
    return moved


def _customers_pending(store: SalesStore, tenant: str, name: str) -> bool:
    """고객 내보내기가 한 번에 다 오지 않아 이어 받을 것이 남았는지."""
    return store.sync_state(tenant, customers_source(name)).last_error == NOT_ALL


def reset_summary(  # noqa: PLR0913 - 요약에 적을 재료
    store: SalesStore, tenant: str, name: str, backup: Path | None, now: datetime
) -> list[str]:  # fmt: skip
    """리셋 뒤 요약 (숫자는 저장소에서 바로 센다. 매출은 매출 분석과 같은 집계
    `sales_sql.tally`)."""
    from data_agent.sales_quality import KST
    from data_agent.sales_sql import tally

    total, first, last = store.order_span(tenant)
    span = (
        f"{first.astimezone(KST):%Y-%m-%d} ~ {last.astimezone(KST):%Y-%m-%d} (접수 날짜, 한국)"
        if first and last
        else "주문 없음"
    )
    customers_state = store.sync_state(tenant, customers_source(name))
    customers = (
        customers_state.last_error
        if customers_state.last_error
        else f"{store.customer_count(tenant):,}명"
    )
    today = now.astimezone(KST).replace(hour=0, minute=0, second=0, microsecond=0)
    start = today - timedelta(days=RESET_DAYS)
    week, _ = tally(store, tenant, start.astimezone(UTC), today.astimezone(UTC))
    last_day = today - timedelta(days=1)
    sources = ", ".join(
        f"{src or NO_SOURCE} {n:,}건" for src, n in store.orders_by_source(tenant).items()
    )
    return [
        f"[sales] 리셋 끝: 출처 {name}, 백업 {backup or '없음 (옮길 파일 없었음)'}",
        f"[sales] 저장소 생성 id {store.store_id}",
        f"[sales] 전체 주문 {total:,}건, {span}",
        f"[sales] 고객 {customers}",
        f"[sales] 어제까지 7일({start:%m/%d}~{last_day:%m/%d}) 결제 완료 {week.orders:,}건, "
        f"매출 {week.revenue:,}원",
        f"[sales] 유입 출처별 주문 (전체): {sources or '주문 없음'}",
        summary_line(store, tenant),
    ]


def summary_line(store: SalesStore, tenant: str, filled: bool | None = None) -> str:
    """구매자 요약 표 한 줄 (11/18): 채운 구매자·항목 줄 수와 준비됐는지."""
    counts = store.summary_counts(tenant)
    how = "처음부터 다시 채움" if filled else "수집하며 채움"
    state = how if store.summary_ready(tenant) else "준비 안 됨 (분석은 예전 방식)"
    return f"[sales] 요약 표: 구매자 {counts.buyers:,}명, 항목 {counts.items:,}줄 ({state})"


async def reset(env: Mapping[str, str], now: datetime | None = None) -> int:
    """저장소를 옆으로 옮기고 새 저장소에 주문·고객을 처음부터 받는다 (잠금은 부르는 쪽이
    잡는다)."""
    at = (now or datetime.now(UTC)).astimezone(UTC)
    db = db_path(env)
    moved = backup_files(db, at)
    backup = next((b for orig, b in moved if orig == db), None)
    undo = (
        "[sales] 되돌리려면 데이터 AI를 멈춘 채 백업을 sales.db로 다시 옮기세요: "
        + "; ".join(f"mv {b} {orig}" for orig, b in moved)
        if moved
        else "[sales] 옮긴 백업 없음 (처음부터 빈 저장소였음)"
    )
    tenant = (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()
    store: SalesStore | None = None
    try:
        store = SalesStore(db)
        name = source_name(env)
        source = get_source(env)
        done = False
        for _ in range(RESET_ROUNDS):
            result = await collect_once(source, store, tenant_id=tenant, source_name=name)
            print(result.line(), flush=True)
            if result.errors:
                print("[sales] 리셋 실패: 오류가 나서 멈춤", flush=True)
                print(undo, flush=True)
                return 1
            if result.finished and not _customers_pending(store, tenant, name):
                done = True
                break
        if not done:
            print(f"[sales] 리셋 실패: {RESET_ROUNDS}번 받아도 끝나지 않음", flush=True)
            print(undo, flush=True)
            return 1
        store.ensure_summary(tenant)  # 새 저장소는 수집하며 채운다. 준비되지 않았으면 여기서 채운다
        for line in reset_summary(store, tenant, name, backup, datetime.now(UTC)):
            print(line, flush=True)
        return 0
    except Exception as error:  # noqa: BLE001 - 어떤 오류든 백업은 두고 되돌리는 법을 적는다
        print(f"[sales] 리셋 실패: {describe(error)}", flush=True)
        print(undo, flush=True)
        return 1
    finally:
        if store is not None:
            store.close()


def rebuild_summary(env: Mapping[str, str]) -> int:
    """구매자 요약 표를 기존 주문으로 다시 채운다 (잠금은 부르는 쪽이 잡는다)."""
    tenant = (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()
    store = SalesStore(db_path(env))
    try:
        store.rebuild_summary(tenant)
        print(summary_line(store, tenant, filled=True), flush=True)
        return 0
    finally:
        store.close()


def recollect(env: Mapping[str, str], company: str) -> int:
    """캣북스가 아닌 회사 하나의 판매 기록·고객 커서만 비운다 (잠금은 부르는 쪽이 잡는다, 11/26).

    지우는 기록은 없다. 다음 수집 회차가 그 회사 주문을 처음부터 다시 받아 같은 주문 번호 줄을
    덮어쓰고(예: 변환기가 새로 채운 상품 이름·분류), 다음 하루 집계가 다시 받은 날을 다시 센다.
    """
    from ax_agent_template import company_scope

    cid = company_scope.scope_id(company.strip())
    if not cid or cid == company_scope.CATBOOKS:
        print("[sales] --recollect는 캣북스가 아닌 회사 id만 (캣북스는 --reset)", flush=True)
        return 2
    store = SalesStore(db_path(env))
    try:
        with company_scope.using(cid):
            name = source_name(env)
        store.save_cursor(cid, name, None)
        # 고객 내보내기도 처음부터 (변환기가 새로 꺼낸 고객 속성 연령대·성별을 받게, 11/26)
        store.save_cursor(cid, customers_source(name), None)
        print(f"[sales {cid}] 판매 기록·고객 커서를 비움 ({name}): 다음 회차에 처음부터 다시 받음",
              flush=True)  # fmt: skip
        return 0
    finally:
        store.close()


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔에서도 한글이 깨지지 않게
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="판매 기록 증분 수집 (매출 분석 표준 저장소)")
    parser.add_argument(
        "--every",
        type=int,
        default=None,
        help=f"초마다 반복 (최소 {MIN_EVERY_SECONDS}, 없으면 한 번. 0이면 {EVERY_ENV} 값)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="저장소를 옆으로 옮겨 두고(백업) 주문·고객을 처음부터 다시 받는다 (한 번만)",
    )
    parser.add_argument(
        "--rebuild-summary",
        action="store_true",
        help="구매자 요약 표를 기존 주문으로 처음부터 다시 채운다 (한 번만, 주문은 받지 않음)",
    )
    parser.add_argument(
        "--recollect",
        metavar="COMPANY_ID",
        default=None,
        help="캣북스가 아닌 회사 하나의 판매 기록을 다음 회차에 처음부터 다시 받게 (커서만 비움)",
    )
    args = parser.parse_args(argv)
    if args.recollect and (args.reset or args.rebuild_summary or args.every is not None):
        print("[sales] --recollect는 혼자 한 번만 실행", flush=True)
        return 2
    if args.rebuild_summary and (args.reset or args.every is not None):
        print("[sales] --rebuild-summary는 혼자 한 번만 실행", flush=True)
        return 2
    if args.reset and args.every is not None:
        print("[sales] --reset은 한 번만 실행 (--every와 같이 쓸 수 없음)", flush=True)
        return 2
    env = dict(os.environ)
    from data_agent.sales_memory import NOTICE, memory_mode

    if memory_mode(env):  # 3-3 A안: 데이터 AI 안에서 받는다. 잠금·sales.db를 건드리지 않는다
        print(NOTICE, flush=True)
        return 0
    every = None if args.every is None else every_seconds(env, args.every or None)
    path = lock_path(env)
    lock = collector_lock.acquire(path, "sales", lambda line: print(line, flush=True))
    if lock is None:
        if args.reset:
            print(
                f"[sales] 잠금 {path}을 잡지 못해 아무것도 지우지 않음: 돌고 있는 sales_collect를 "
                "먼저 멈추세요 (pkill -f data_agent.sales_collect)",
                flush=True,
            )
            return 1
        print(f"[sales] 이미 다른 수집기가 돌고 있음 (잠금 {path}): 끝냄", flush=True)
        return 0
    try:
        if args.recollect:
            return recollect(env, args.recollect)
        if args.rebuild_summary:
            return rebuild_summary(env)
        if args.reset:
            return asyncio.run(reset(env))
        return asyncio.run(run(every, env))
    except KeyboardInterrupt:
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
