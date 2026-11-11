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
import hashlib
import json
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
# 읽어 갈 종류와 그 종류를 처리하는 skill. 서비스 카드에 이 skill이 있는 서버만 그 종류를 읽는다
PULL_SKILLS = {
    "inquiries": "create_inquiry",
    "claims": "create_claim",
    "views": "create_inquiry",
    "feedback": "submit_feedback",
    "ideas": "submit_idea",
    "returns": "receive_return",
}
ALL_PULL = frozenset(PULL_SKILLS.values())  # 모든 종류를 읽는다(단독 실행·시험에서 명시해 쓴다)
BUYER_ID = re.compile(r"^buy_([0-9]{4,9})$")

# 마지막으로 읽어 본 결과. 화면(연결 끊김 표시)이 읽는다. 재시작하면 비워진다
STATE: dict[str, Any] = {
    "last_attempt_at": None,
    "last_ok_at": None,
    "last_error": None,
    "pending": None,  # 판매 서버 문의함에 아직 남은 문의 수(마지막으로 읽었을 때)
    "ingested_total": 0,
    "failed_total": 0,
    "inbox_failed": None,  # 판매 서버 문의함에서 실패로 빼 둔 문의 수
    "claims_pending": None,  # 판매 서버 클레임함에 아직 남은 신청 수
    "claims_inbox_failed": None,  # 판매 서버 클레임함에서 실패로 빼 둔 신청 수
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
    from support_agent import shop_pull_more as more

    attempted = STATE["last_attempt_at"] is not None
    return {
        **STATE,
        **more.COUNTS,
        "enabled": enabled(),
        "interval_seconds": interval_seconds(),
        "connected": (STATE["last_error"] is None) if attempted else None,
    }


def reset_state() -> None:
    from support_agent import shop_pull_more as more

    more.reset_counts()
    STATE.update(
        last_attempt_at=None,
        last_ok_at=None,
        last_error=None,
        pending=None,
        ingested_total=0,
        failed_total=0,
        inbox_failed=None,
        claims_pending=None,
        claims_inbox_failed=None,
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
            idempotency_key=f"shop-inquiry:{item.get('key') or item['seq']}",
        ),
        skill="create_inquiry",
        input={
            "user_id": int(found.group(1)),
            "customer_name": item["customer_name"],
            "order_id": item["order_id"],
        },
        untrusted=untrusted,
    )


async def _report_failed(kind: str, item: dict[str, Any], reason: str) -> None:
    """처리하지 못한 접수를 판매 서버에 알린다 (다시 읽는 시각을 늦추고 횟수를 넘기면 실패로 뺀다).

    알리지 못해도(판매 서버에 닿지 않음) 그냥 둔다. 다음 주기에 다시 읽혀 다시 시도한다.
    """
    try:
        if kind == "claim":
            await shop_client.report_claim_failed(int(item["seq"]), reason)
        else:
            await shop_client.report_inquiry_failed(int(item["seq"]), reason)
    except shop_client.ShopError:
        logger.warning("%s %s: 실패를 판매 서버에 알리지 못함", kind, item.get("seq"))


async def _ingest(item: dict[str, Any]) -> bool:
    """문의 한 건을 처리하고 읽었다고 알린다. 처리됐으면 True, 못 했으면 실패를 알리고 False."""
    request = _request(item)
    if request is None:
        logger.warning("문의함 %s: 구매자 ID 형식이 달라 건너뜀", item.get("seq"))
        await _report_failed("inquiry", item, "BAD_BUYER_ID")
        return False
    result = await h.create_inquiry(request)
    created = result.output.get("id") if result.status == TaskStatus.COMPLETED else None
    if not isinstance(created, int):
        logger.warning("문의함 %s: 처리되지 않음 (%s)", item.get("seq"), result.outcome)
        await _report_failed("inquiry", item, str(result.outcome or "NOT_PROCESSED")[:40])
        return False
    await shop_client.mark_inquiry_synced(int(item["seq"]), created)
    return True


# 판매 서버에 되돌려 쓰는 클레임은 고객이 볼 수 있는 값만 남긴다. 남용 점수·내부 내력·상담원 메모·
# 안전 관문 실행 번호·결정한 사람 이름은 판매 서버에 가지 않는다.
CUSTOMER_CLAIM_FIELDS = (
    "id", "context_id", "order_id", "order_no", "buyer_id", "type", "reason", "status",
    "created_at", "updated_at", "outcome", "resolution", "requires_return", "evidence_count",
    "unsealed", "reasons", "lines", "shipping_refund", "expected_refund", "points_back",
    "refund_paid", "processed_at",
)  # fmt: skip


def customer_claim(claim: dict[str, Any]) -> dict[str, Any]:
    """고객에게 보여 줄 클레임. `CUSTOMER_CLAIM_FIELDS`만 남기고 없는 값은 만들지 않는다."""
    return {k: claim[k] for k in CUSTOMER_CLAIM_FIELDS if k in claim}


def claim_digest(view: dict[str, Any]) -> str:
    """판매 서버가 적어 둔 결과와 견주는 지문. 판매 서버의 `result_digest`와 같은 방식이다."""
    text = json.dumps(view, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


NOT_ACCEPTED = ("not_found", "duplicate_claim")  # 클레임으로 받지 않은 이유(고객에게 이유만 적는다)


def _claim_request(item: dict[str, Any]) -> AgentRequest:
    """클레임함 한 건을 `create_claim` 요청으로 바꾼다. 설명(고객이 쓴 문장)은 untrusted다."""
    return AgentRequest(
        meta=RequestMeta(
            context_id=item["context_id"],
            tenant_id=os.getenv(ENV_TENANT, "").strip() or TENANT_ID_DEFAULT,
            token=config.agent_token("support"),
            deadline=datetime.now(UTC) + DEADLINE,
            idempotency_key=f"shop-claim:{item.get('key') or item['seq']}",
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
    이유만 적어 더 읽지 않게 한다. 연결·보류·오류는 실패를 알려 다시 읽는 시각을 늦춘다
    (같은 키로 다시 시도하고, 횟수를 넘기면 판매 서버가 고객에게 접수하지 못했다고 닫는다).
    """
    result = await claim_handlers.create_claim(_claim_request(item))
    seq = int(item["seq"])
    if result.status == TaskStatus.COMPLETED and isinstance(result.output.get("id"), int):
        claim = result.output
        await shop_client.write_claim_result(
            seq, claim["id"], str(claim["status"]), customer_claim(claim)
        )
        return True
    if result.status == TaskStatus.COMPLETED and result.outcome in NOT_ACCEPTED:
        reason = {"message": result.output.get("message", ""), "outcome": result.outcome}
        await shop_client.write_claim_result(seq, None, "not_accepted", reason)
        return True
    logger.warning("클레임함 %s: 처리되지 않음 (%s)", item.get("seq"), result.outcome)
    await _report_failed("claim", item, str(result.outcome or "NOT_PROCESSED")[:40])
    return False


async def _refresh_claims() -> None:
    """끝나지 않은 신청의 결과가 달라졌으면 판매 서버에 다시 적는다 (달라진 것만).

    상태가 그대로여도 환불 완료액·처리 시각처럼 고객이 보는 값이 바뀌면 지문이 달라 다시 쓴다.
    진행 중인 신청이 한 쪽(100건)을 넘으면 다음 쪽을 이어서 읽는다.
    """
    after = 0
    while True:
        page, next_after = await shop_client.fetch_open_claims(after)
        for open_item in page:
            claim = claim_handlers.CLAIMS.get(int(open_item["support_claim_id"]))
            if claim is None:
                continue
            view = customer_claim(claim.model_dump(mode="json"))
            if claim.status == open_item["status"] and claim_digest(view) == open_item.get(
                "result_digest"
            ):
                continue
            await shop_client.write_claim_result(
                int(open_item["seq"]), claim.id, claim.status, view
            )
        if next_after is None:
            return
        after = next_after


async def _pull_inquiries(limit: int) -> dict[str, int]:
    """문의함을 한 번 읽어 새 문의를 처리한다. 판매 서버에 닿지 못하면 `ShopError`를 올린다."""
    items, total, failed_in_inbox = await shop_client.fetch_inquiry_inbox(limit)
    done = failed = 0
    shop_error: shop_client.ShopError | None = None
    for item in items:
        try:
            ok = await _ingest(item)
        except shop_client.ShopError as exc:
            # 이 건만 실패로 남긴다(처리는 됐는데 읽음 표시만 안 된 경우라 실패 횟수는 세지 않는다)
            logger.warning("문의함 %s: 판매 서버 오류 (%s)", item.get("seq"), exc.code)
            shop_error = shop_error or exc
            ok = False
        except Exception:  # noqa: BLE001 - 한 건이 실패해도 나머지는 계속 처리한다
            logger.warning("문의함 %s 처리 중 오류", item.get("seq"), exc_info=True)
            await _report_failed("inquiry", item, "ERROR")
            ok = False
        done += ok
        failed += not ok
    STATE["pending"] = max(0, total - done)
    STATE["inbox_failed"] = failed_in_inbox
    STATE["ingested_total"] += done
    STATE["failed_total"] += failed
    if shop_error is not None:  # 건별 오류는 나머지를 멈추지 않고, 주기 끝에 연결 문제로 알린다
        raise shop_error
    return {"read": len(items), "ingested": done, "failed": failed}


async def _pull_claims(limit: int) -> dict[str, int]:
    """클레임함을 한 번 읽어 새 신청을 판정하고, 끝나지 않은 신청의 달라진 결과를 다시 적는다."""
    items, total, failed_in_inbox = await shop_client.fetch_claim_inbox(limit)
    done = failed = 0
    shop_error: shop_client.ShopError | None = None
    for item in items:
        try:
            ok = await _ingest_claim(item)
        except shop_client.ShopError as exc:
            logger.warning("클레임함 %s: 판매 서버 오류 (%s)", item.get("seq"), exc.code)
            shop_error = shop_error or exc
            ok = False
        except Exception:  # noqa: BLE001 - 한 건이 실패해도 나머지는 계속 처리한다
            logger.warning("클레임함 %s 처리 중 오류", item.get("seq"), exc_info=True)
            await _report_failed("claim", item, "ERROR")
            ok = False
        done += ok
        failed += not ok
    STATE["claims_pending"] = max(0, total - done)
    STATE["claims_inbox_failed"] = failed_in_inbox
    STATE["claims_ingested_total"] += done
    STATE["claims_failed_total"] += failed
    try:
        await _refresh_claims()
    except shop_client.ShopError as exc:
        shop_error = shop_error or exc
    if shop_error is not None:
        raise shop_error
    return {"read": len(items), "ingested": done, "failed": failed}


async def pull_once(limit: int = READ_LIMIT, *, owned: frozenset[str]) -> dict[str, int]:
    """문의함과 클레임함을 한 번씩 읽는다. 읽은 수·처리 수·실패 수(두 함의 합)를 돌려준다.

    한쪽이 실패해도 다른 쪽은 그 주기에 계속 읽는다. 판매 서버에 닿지 못하거나 처리하지 못한
    접수가 있으면 `last_error`에 남고 다음 주기에 다시 읽는다.

    `owned`는 이 서버가 받는 skill 이름이다(서비스 카드). 이 skill로 처리하는 종류만 읽는다.
    모두 읽으려면 ALL_PULL을 명시한다(빠뜨려서 세 서버가 모두 읽는 일이 없게 필수다).
    """
    async with _LOCK:
        STATE["last_attempt_at"] = _now()
        total = {"read": 0, "ingested": 0, "failed": 0}
        error: str | None = None
        # 나머지 접수(평가·아이디어·반품 입고)와 문의 값 되돌려 쓰기는 같은 방식이라 별도 모듈이다
        from support_agent import shop_pull_more as more

        # 서비스마다 저장소가 따로다(inquiry·claims·reviews가 같은 앱을 각자 띄운다). 이 서버가
        # 받는 skill의 종류만 읽는다. 안 그러면 여러 서버가 같은 접수를 각자 처리하고, 고객이
        # 보는 값을 서로 덮어쓴다.
        candidates = (
            (PULL_SKILLS["inquiries"], _pull_inquiries),
            (PULL_SKILLS["claims"], _pull_claims),
            (PULL_SKILLS["views"], lambda _limit: more.refresh_views()),
            (PULL_SKILLS["feedback"], more.pull_feedback),
            (PULL_SKILLS["ideas"], more.pull_ideas),
            (PULL_SKILLS["returns"], more.pull_returns),
        )
        pulls = [pull for skill, pull in candidates if skill in owned]
        for pull in pulls:
            try:
                got = await pull(limit)
            except shop_client.ShopError as exc:
                error = error or exc.code
                continue
            for key, value in got.items():
                total[key] += value
            if got["failed"]:
                error = error or "INGEST_FAILED"
        STATE["last_error"] = error
        if error is None:
            STATE["last_ok_at"] = _now()
        return total


async def _loop(owned: frozenset[str]) -> None:
    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        try:
            await pull_once(owned=owned)
        except Exception:  # noqa: BLE001 - 서버는 계속 돈다
            logger.warning("문의함 읽기 중 오류", exc_info=True)
        await asyncio.sleep(interval_seconds())


def start(owned: frozenset[str]) -> asyncio.Task[None] | None:
    """읽기 루프를 시작한다. 꺼져 있거나 이 서버가 읽을 종류가 하나도 없으면 시작하지 않는다.

    `owned`는 이 서버가 받는 skill 이름이다. 서비스 카드에서 한 번 정해 넘기므로 매 주기 카드를
    다시 읽지 않는다. 받는 종류가 없는 서버(reviews)가 돌면서 연결됨 표시만 갱신하지 않게 한다.
    """
    if not enabled():
        return None
    if not owned & ALL_PULL:
        logger.info("이 서버는 읽어 갈 접수 종류를 맡지 않아 읽기 루프를 켜지 않는다")
        return None
    return asyncio.create_task(_loop(owned))


def stop(task: asyncio.Task[None] | None) -> None:
    if task is not None:
        task.cancel()
