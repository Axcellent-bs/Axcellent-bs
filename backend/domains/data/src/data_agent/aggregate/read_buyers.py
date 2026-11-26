"""구매자 숫자 집계 읽기 (3-2 ③~⑦, 설계 2.2·2.4·10절 결정 1 = A안).

구매자 수는 날짜끼리 더할 수 없어서, 고객 분석은 정해진 기간 스냅샷을 읽는다.
- 기준일 = 어제(한국). 기간 끝은 어제 24시다(11/24 결정 1 A안: "어제 기준"으로 바꾸고 표시한다).
  지금 원본 경로(끝 = 지금)와 오늘 몫만큼 다르다.
- 스위치 `DATA_SALES_READ=aggregate`가 켜지고 그 기준일 스냅샷이 있을 때만 쓴다(기본 끔).
  없으면(계산 전·실패·파일을 못 읽음) `None`: 부르는 쪽이 원본 경로 그대로 간다.
- 근거(basis)의 기간은 스냅샷 기간으로 바꾸고, 요약 끝에 "(11월 23일 기준)"을 붙인다.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from ax_contracts.data_access.sales_aggregates import AggPeriod
from ax_contracts.schemas.data import SalesBasis
from data_agent.aggregate.read import enabled
from data_agent.aggregate.store import AggregateStore, db_path
from data_agent.sales_quality import KST

if TYPE_CHECKING:  # build_snapshots는 skill을 import한다(skill → 이 파일 → build_snapshots 순환)
    from data_agent.aggregate.build_snapshots import Window

logger = logging.getLogger(__name__)


def as_of_for(now: datetime) -> date:
    """스냅샷 기준일: 어제(한국)."""
    return now.astimezone(KST).date() - timedelta(days=1)


def window_of(period: AggPeriod, as_of: date) -> Window:
    from data_agent.aggregate.build_snapshots import windows  # noqa: PLC0415 - 순환 import

    return next(w for w in windows(as_of) if w.period == period)


def note(as_of: date) -> str:
    return f"({as_of.month}월 {as_of.day}일 기준)"


def with_note(text: str, as_of: date) -> str:
    return f"{text} {note(as_of)}"


def basis_at(basis: SalesBasis, w: Window) -> SalesBasis:
    """근거 기간을 스냅샷 기간(끝 = 기준일 24시)으로. 직전 기간은 없음(시작과 같게)."""
    return basis.model_copy(update={"period_from": w.start, "period_to": w.end,
                                    "previous_from": w.start, "previous_to": w.start})  # fmt: skip


def from_snapshot[T](
    tenant_id: str, now: datetime, read: Callable[[AggregateStore, date], T]
) -> T | None:
    """스위치가 켜지고 어제 기준 스냅샷이 있으면 `read(집계, 기준일)`, 아니면 None(원본 경로)."""
    if not enabled():
        return None
    as_of = as_of_for(now)
    try:
        agg = AggregateStore(db_path())
        try:
            if as_of.isoformat() not in agg.snapshot_days(tenant_id):
                return None
            return read(agg, as_of)
        finally:
            agg.close()
    except Exception:  # noqa: BLE001 - 집계를 못 읽으면 원본 경로로 (화면은 그대로)
        logger.warning("[aggregate] 스냅샷 읽기 실패: 원본으로 계산", exc_info=True)
        return None


def run_source(tenant_id: str) -> tuple[bool, str | None]:
    """마지막으로 끝난 집계 계산의 원본 판매 출처 이름 (3-2 ②). (찾음, 이름).

    스위치가 꺼졌거나, 끝난 계산이 없거나, 출처 칸이 없는 옛 기록이면 (False, None): 원본 경로."""
    if not enabled():
        return False, None
    try:
        agg = AggregateStore(db_path())
        try:
            done = [r for r in agg.runs(tenant_id) if r["status"] == "done"]
        finally:
            agg.close()
    except Exception:  # noqa: BLE001 - 못 읽으면 원본 경로로
        logger.warning("[aggregate] 계산 기록 읽기 실패: 출처는 원본에서", exc_info=True)
        return False, None
    if not done or done[-1]["source_name"] is None:
        return False, None
    return True, done[-1]["source_name"]


def category_codes(tenant_id: str) -> list[str]:
    """집계에 나온 그 회사 분류 code (분류 표 없는 회사의 고객·시장 분석 선택지, 11/26)."""
    agg = AggregateStore(db_path())
    try:
        return agg.category_codes(tenant_id)
    finally:
        agg.close()


def run_key(tenant_id: str) -> str:
    """판매 캐시 키 조각 (3-2 ⑧): 스위치가 켜졌으면 마지막으로 끝난 집계 계산 번호, 꺼졌으면 "".
    하루 계산이 새로 끝나면 키가 달라져 어제 스냅샷으로 낸 결과가 섞이지 않는다."""
    if not enabled():
        return ""
    try:
        agg = AggregateStore(db_path())
        try:
            done = [r for r in agg.runs(tenant_id) if r["status"] == "done"]
        finally:
            agg.close()
    except Exception:  # noqa: BLE001 - 못 읽으면 원본 경로와 같은 키 조각
        logger.warning("[aggregate] 계산 기록 읽기 실패: 캐시 키는 원본 기준", exc_info=True)
        return ""
    return f"aggregate:{done[-1]['run_id']}" if done else "aggregate:"
