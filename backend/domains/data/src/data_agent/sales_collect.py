"""판매 기록 증분 수집 (한 번 또는 `--every 초`마다). run_all.sh가 data와 함께 띄운다.

    cd backend
    uv run python -m data_agent.sales_collect              # 한 번
    uv run python -m data_agent.sales_collect --every 300  # 5분마다 (Ctrl+C로 멈춤)

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
NO_CUSTOMERS_EXPORT = "판매 서버에 고객 내보내기 없음"
NOT_ALL = "한 번에 다 받지 못함 (다음 수집에 이어서)"
DEFAULT_EVERY_SECONDS = 300
MIN_EVERY_SECONDS = 60  # 판매 서버에 부담 주지 않게
MAX_PAGES = 200  # 한 번에 받는 최대 페이지 (500건 × 200 = 10만 건). 넘으면 다음 수집이 이어 받는다


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


async def run(every: int | None, env: Mapping[str, str]) -> int:
    store = SalesStore(db_path(env))
    tenant = (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()
    name = source_name(env)
    streak = ErrorStreak()
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
            if every is None:
                return 1 if result.errors else 0
            await asyncio.sleep(every)
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
    args = parser.parse_args(argv)
    env = dict(os.environ)
    every = None if args.every is None else every_seconds(env, args.every or None)
    path = lock_path(env)
    lock = collector_lock.acquire(path, "sales", lambda line: print(line, flush=True))
    if lock is None:
        print(f"[sales] 이미 다른 수집기가 돌고 있음 (잠금 {path}): 끝냄", flush=True)
        return 0
    try:
        return asyncio.run(run(every, env))
    except KeyboardInterrupt:
        return 0
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
