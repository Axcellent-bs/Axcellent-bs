"""판매 서버 문의함에서 새 문의를 읽어 와 처리한다 (예진 님 제안: 저장 → AXcellent가 읽어 오기).

흐름: 판매 서버(shop-api)가 고객 문의를 저장해 두면, 고객지원이 정해진 간격으로 문의함을 읽어
`create_inquiry`로 처리하고 읽었다고 알린다. 판매 서버가 고객지원을 부르지 않으므로 고객사 시스템은
우리 주소·토큰을 들고 있을 필요가 없고, 중간이 끊기면 `status()`가 바로 "연결 끊김"을 알린다.

- 같은 판매 서버 접수 번호는 같은 멱등 키(`shop-inquiry:<번호>`)라 두 번 읽어도 문의가 하나다.
  읽었다는 표시가 실패해도 다음 주기에 같은 문의를 다시 읽어 표시만 이어서 한다.
- 처리가 안 된 문의(보류·오류)는 판매 서버에 읽지 않은 채로 남아 다음 주기에 다시 시도한다.
- 한 번에 한 주기만 돈다. 문의는 접수 순서대로 하나씩 처리한다.
- 켜고 끄는 스위치 `SUPPORT_INQUIRY_PULL`(`on`·`off`): 비우면 실제 모드는 켜짐,
  목 모드(시험)는 꺼짐. 간격은 `SUPPORT_INQUIRY_PULL_SECONDS`(기본 10초).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template import config
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from support_agent import claim_handlers, shop_client
from support_agent import handlers as h

logger = logging.getLogger("ax.support.inquiry_pull")

ENV_SWITCH = "SUPPORT_INQUIRY_PULL"
ENV_SECONDS = "SUPPORT_INQUIRY_PULL_SECONDS"
ENV_TENANT = "SUPPORT_INQUIRY_PULL_TENANT"
FIRST_DELAY_SECONDS = 5.0
DEFAULT_SECONDS = 10.0
READ_LIMIT = 20
DEADLINE = timedelta(minutes=3)  # 문의 하나를 처리하는 데 주는 시간 (분류 모델이 느릴 수 있다)
BUYER_ID = re.compile(r"^buy_([0-9]{4,9})$")

# 마지막으로 읽어 본 결과. 화면(연결 끊김 표시)이 읽는다. 재시작하면 비워진다
STATE: dict[str, Any] = {
    "last_attempt_at": None,
    "last_ok_at": None,
    "last_error": None,
    "pending": None,  # 판매 서버 문의함에 아직 남은 문의 수(마지막으로 읽었을 때)
    "ingested_total": 0,
    "failed_total": 0,
    "claims_pending": None,  # 판매 서버 클레임함에 아직 남은 신청 수
    "claims_ingested_total": 0,
    "claims_failed_total": 0,
}
_LOCK = asyncio.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


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


def status() -> dict[str, Any]:
    """읽기 상태. `connected`는 마지막 시도가 성공했는지다 (한 번도 안 읽었으면 None)."""
    attempted = STATE["last_attempt_at"] is not None
    return {
        **STATE,
        "enabled": enabled(),
        "interval_seconds": interval_seconds(),
        "connected": (STATE["last_error"] is None) if attempted else None,
    }


def reset_state() -> None:
    STATE.update(
        last_attempt_at=None,
        last_ok_at=None,
        last_error=None,
        pending=None,
        ingested_total=0,
        failed_total=0,
        claims_pending=None,
        claims_ingested_total=0,
        claims_failed_total=0,
    )


def _request(item: dict[str, Any]) -> AgentRequest | None:
    """문의함 한 건을 `create_inquiry` 요청으로 바꾼다. 구매자 ID가 이상하면 None."""
    found = BUYER_ID.match(str(item.get("buyer_id", "")))
    if found is None:
        return None
    untrusted: dict[str, Any] = {"subject": item["subject"], "message": item["message"]}
    if item.get("images"):
        untrusted["images"] = item["images"]
    return AgentRequest(
        meta=RequestMeta(
            context_id=item["context_id"],
            tenant_id=os.getenv(ENV_TENANT, "").strip() or TENANT_ID_DEFAULT,
            token=config.agent_token("support"),
            deadline=datetime.now(UTC) + DEADLINE,
            idempotency_key=f"shop-inquiry:{item['seq']}",
        ),
        skill="create_inquiry",
        input={
            "user_id": int(found.group(1)),
            "customer_name": item["customer_name"],
            "order_id": item["order_id"],
        },
        untrusted=untrusted,
    )


async def _ingest(item: dict[str, Any]) -> bool:
    """문의 한 건을 처리하고 읽었다고 알린다. 처리됐으면 True."""
    request = _request(item)
    if request is None:
        logger.warning("문의함 %s: 구매자 ID 형식이 달라 건너뜀", item.get("seq"))
        return False
    result = await h.create_inquiry(request)
    created = result.output.get("id") if result.status == TaskStatus.COMPLETED else None
    if not isinstance(created, int):
        logger.warning("문의함 %s: 처리되지 않음 (%s)", item.get("seq"), result.outcome)
        return False
    await shop_client.mark_inquiry_synced(int(item["seq"]), created)
    return True


NOT_ACCEPTED = ("not_found", "duplicate_claim")  # 클레임으로 받지 않은 이유(고객에게 이유만 적는다)


def _claim_request(item: dict[str, Any]) -> AgentRequest:
    """클레임함 한 건을 `create_claim` 요청으로 바꾼다. 설명(고객이 쓴 문장)은 untrusted다."""
    return AgentRequest(
        meta=RequestMeta(
            context_id=item["context_id"],
            tenant_id=os.getenv(ENV_TENANT, "").strip() or TENANT_ID_DEFAULT,
            token=config.agent_token("support"),
            deadline=datetime.now(UTC) + DEADLINE,
            idempotency_key=f"shop-claim:{item['seq']}",
        ),
        skill="create_claim",
        input={
            "buyer_id": item["buyer_id"],
            "customer_name": item["customer_name"],
            "order_id": item["order_id"],
            "type": item["type"],
            "reason": item["reason"],
            "items": item.get("items"),
            "unsealed": bool(item.get("unsealed")),
            "evidence_count": int(item.get("evidence_count") or 0),
        },
        untrusted={"description": item.get("description") or ""},
    )


async def _ingest_claim(item: dict[str, Any]) -> bool:
    """신청 한 건을 판정하고 결과를 판매 서버에 적는다. 적었으면 True.

    클레임이 만들어졌으면 그 클레임을, 클레임으로 받지 않았으면(없는 주문·진행 중인 같은 품목)
    이유만 적어 더 읽지 않게 한다. 연결·보류·오류는 읽지 않은 채로 둔다
    (다음 주기에 같은 키로 다시 시도한다).
    """
    result = await claim_handlers.create_claim(_claim_request(item))
    seq = int(item["seq"])
    if result.status == TaskStatus.COMPLETED and isinstance(result.output.get("id"), int):
        claim = result.output
        await shop_client.write_claim_result(seq, claim["id"], str(claim["status"]), claim)
        return True
    if result.status == TaskStatus.COMPLETED and result.outcome in NOT_ACCEPTED:
        reason = {"message": result.output.get("message", ""), "outcome": result.outcome}
        await shop_client.write_claim_result(seq, None, "not_accepted", reason)
        return True
    logger.warning("클레임함 %s: 처리되지 않음 (%s)", item.get("seq"), result.outcome)
    return False


async def _refresh_claims() -> None:
    """끝나지 않은 신청의 상태가 바뀌었으면 판매 서버에 다시 적는다 (바뀐 것만)."""
    for open_item in await shop_client.fetch_open_claims():
        claim = claim_handlers.CLAIMS.get(int(open_item["support_claim_id"]))
        if claim is None or claim.status == open_item["status"]:
            continue
        await shop_client.write_claim_result(
            int(open_item["seq"]), claim.id, claim.status, claim.model_dump(mode="json")
        )


async def _pull_claims(limit: int) -> None:
    items, total = await shop_client.fetch_claim_inbox(limit)
    done = failed = 0
    for item in items:
        try:
            ok = await _ingest_claim(item)
        except shop_client.ShopError:
            raise
        except Exception:  # noqa: BLE001 - 한 건이 실패해도 나머지는 계속 처리한다
            logger.warning("클레임함 %s 처리 중 오류", item.get("seq"), exc_info=True)
            ok = False
        done += ok
        failed += not ok
    STATE["claims_pending"] = max(0, total - done)
    STATE["claims_ingested_total"] += done
    STATE["claims_failed_total"] += failed
    if failed:
        raise RuntimeError("INGEST_FAILED")
    await _refresh_claims()


async def pull_once(limit: int = READ_LIMIT) -> dict[str, int]:
    """문의함을 한 번 읽어 새 문의를 처리한다. 읽은 수·처리 수·실패 수를 돌려준다.

    판매 서버에 닿지 못하면 `last_error`만 남기고 끝난다 (다음 주기에 다시 읽는다).
    """
    async with _LOCK:
        STATE["last_attempt_at"] = _now()
        try:
            items, total = await shop_client.fetch_inquiry_inbox(limit)
        except shop_client.ShopError as error:
            STATE["last_error"] = error.code
            return {"read": 0, "ingested": 0, "failed": 0}
        done = failed = 0
        for item in items:
            try:
                ok = await _ingest(item)
            except Exception:  # noqa: BLE001 - 한 건이 실패해도 나머지는 계속 처리한다
                logger.warning("문의함 %s 처리 중 오류", item.get("seq"), exc_info=True)
                ok = False
            done += ok
            failed += not ok
        STATE["pending"] = max(0, total - done)
        STATE["ingested_total"] += done
        STATE["failed_total"] += failed
        error = "INGEST_FAILED" if failed else None
        try:  # 클레임함도 같은 주기에 읽는다 (문의가 실패해도 클레임은 계속 읽는다)
            await _pull_claims(limit)
        except shop_client.ShopError as exc:
            error = error or exc.code
        except RuntimeError:
            error = "INGEST_FAILED"
        STATE["last_error"] = error
        if error is None:
            STATE["last_ok_at"] = _now()
        return {"read": len(items), "ingested": done, "failed": failed}


async def _loop() -> None:
    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        try:
            await pull_once()
        except Exception:  # noqa: BLE001 - 서버는 계속 돈다
            logger.warning("문의함 읽기 중 오류", exc_info=True)
        await asyncio.sleep(interval_seconds())


def start() -> asyncio.Task[None] | None:
    """읽기 루프를 시작한다. 꺼져 있으면 시작하지 않는다."""
    return asyncio.create_task(_loop()) if enabled() else None


def stop(task: asyncio.Task[None] | None) -> None:
    if task is not None:
        task.cancel()
