"""고객지원 AI의 판매자 정산 지급 승인 자동 요청 (settlement 서비스, 2026-11-12 팀장 결정).

예전에는 사람(고객지원 담당)이 "정산 묶음 지급 승인 요청"을 눌러야 했고, 안전 관문이 요청한
사람의 승인을 막아 고객지원 밖의 사람이 승인해야 했다. 이제 고객지원 AI가 요청하고 고객지원 담당이
승인한다.

- 장부를 다 모은 뒤 `SUPPORT_SETTLEMENT_AUTO_REQUEST_SECONDS`(기본 10분)마다 한 번 돈다
- 지급 기한 점검 범위 안에서 기간이 끝났고 지급 예정일이 된 주(`settlement.due_weeks`)를 오래된
  주부터 본다. 그 주에 보류 없이 줄 판매자가 있고 묶음이 요청 중·승인됨이 아니면 사람 버튼과 같은 길
  (`settlement_handlers.request_week`)로 요청한다. 요청자는 `support-ai`라 어느 고객지원 담당이든
  승인한다
- 다시 요청하는 경우: 시간 초과(`expired`)·안전 장치가 보류 건을 잃음(`review_missing`). 사람이
  거절한 주는 다시 올리지 않는다(사람이 버튼으로 다시 요청할 수 있다)
- 같은 주를 두 번 요청하지 않는다: 요청 중·승인됨이면 건너뛰고, 멱등 키는 시도 번호를 그대로 쓴다
- 스위치 `SUPPORT_SETTLEMENT_AUTO_REQUEST`: `on`·`off`. 비우면 목 모드(테스트)에서는 끄고 실제
  모드에서는 켠다. `run_all.sh`는 `on`
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import timedelta
from typing import Any

from ax_agent_template import config
from ax_contracts.schemas import RequestMeta
from support_agent import settlement, settlement_batches
from support_agent import settlement_books as sb
from support_agent import settlement_config as sc
from support_agent import settlement_handlers as sh

logger = logging.getLogger("ax.support.settlement_auto")

ENV_SWITCH = "SUPPORT_SETTLEMENT_AUTO_REQUEST"
ENV_SECONDS = "SUPPORT_SETTLEMENT_AUTO_REQUEST_SECONDS"
DEFAULT_SECONDS = 600.0
FIRST_DELAY_SECONDS = 30.0  # 서버를 켠 직후 첫 장부 읽기와 겹치지 않게
PREPARING_RETRY_SECONDS = 60.0  # 첫 장부가 아직이면 이만큼 뒤에 다시 본다
RETRY_REASONS = ("expired", "review_missing")  # 사람이 결정하지 않고 끝난 묶음만 다시 올린다

# 마지막 회차 결과 (주 → 결과 코드). 로그와 테스트가 본다
LAST: dict[str, str] = {}


def enabled() -> bool:
    value = os.getenv(ENV_SWITCH, "").strip().lower()
    if value in ("on", "off"):
        return value == "on"
    return not config.is_mock("support")


def interval_seconds() -> float:
    try:
        value = float(os.getenv(ENV_SECONDS) or DEFAULT_SECONDS)
    except ValueError:
        return DEFAULT_SECONDS
    return value if value > 0 else DEFAULT_SECONDS


def _meta(period: str) -> RequestMeta:
    context_id = settlement_batches.context_id_of(period)
    return RequestMeta(
        context_id=context_id,
        # 서버 안에서 바로 부르므로 A2A 토큰을 거치지 않는다. execute()는 안전 장치에 자기 서비스
        # 토큰을 따로 붙인다(meta.token을 쓰지 않는다)
        token="internal:settlement-auto",
        deadline=sc.now() + timedelta(minutes=5),
        idempotency_key=f"{context_id}:auto",  # request_week가 시도 번호 키로 바꾼다
    )


def _skip_reason(batch: dict[str, Any] | None) -> str | None:
    """이 주를 건너뛸 이유. None이면 요청해 본다."""
    if batch is None:
        return None
    status = batch.get("status")
    if status in ("requested", "approved"):
        return status
    if status == "rejected" and batch.get("closed_reason") not in RETRY_REASONS:
        return "rejected_by_human"
    return None


async def run_once() -> dict[str, str]:
    """한 회차: 지급일이 된 주를 보고 필요하면 요청한다. 주 → 결과 코드.

    첫 장부가 아직이면 `sb.Preparing`을 그대로 낸다(루프가 조금 뒤 다시 본다).
    """
    books = await sb.load_books()  # 먼저 저장된 장부로 줄 판매자가 있는지만 본다(가볍게)
    if books.unavailable_core():
        return {"*": "shop_unavailable"}
    batches = await settlement_batches.refreshed_all()
    today = sc.today()
    results: dict[str, str] = {}
    for week in settlement.due_weeks(today):
        skip = _skip_reason(batches.get(week))
        if skip:
            results[week] = skip
            continue
        # 조회와 같은 열쇠로 계산을 나눠 쓴다(스레드에서). 붙여 둔 결과는 고치지 않는다
        preview = await books.computed(
            ("statements", *settlement.week_key(week, today)),
            lambda week=week: settlement.statements(books, week, today),
        )
        if not settlement.ready(preview):
            results[week] = "nothing_to_settle"
            continue
        out = await sh.request_week(week, sh.AI_REQUESTER, _meta(week))
        if out.error is not None:
            results[week] = out.error[0]
            logger.warning("%s 정산 자동 요청 안 함: %s", week, out.error[1])
            if out.error[0] in ("safety_unavailable", sh.PREPARING, "shop_unavailable"):
                break  # 다른 주도 같은 이유로 안 된다. 다음 회차에 다시 본다
            continue
        results[week] = "requested" if out.requested else "already"
        if out.requested:
            logger.info("%s 판매자 정산 지급 승인을 고객지원 AI가 요청했다", week)
    LAST.clear()
    LAST.update(results)
    return results


async def _loop() -> None:
    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        wait = interval_seconds()
        try:
            await run_once()
        except sb.Preparing:
            wait = PREPARING_RETRY_SECONDS
        except Exception:  # noqa: BLE001 - 서버는 계속 돈다
            logger.warning("판매자 정산 자동 요청 중 오류", exc_info=True)
        await asyncio.sleep(wait)


def start(owned: frozenset[str]) -> asyncio.Task[None] | None:
    """자동 요청 루프를 시작한다. 꺼져 있거나 정산을 맡지 않은 서버면 시작하지 않는다."""
    if not enabled() or "request_seller_settlement" not in owned:
        return None
    return asyncio.create_task(_loop())


def stop(task: asyncio.Task[None] | None) -> None:
    if task is not None:
        task.cancel()
