"""판매 서버에 저장된 나머지 접수를 읽어 처리한다: 문의 값 되돌려 쓰기, 평가, 아이디어, 반품 입고.

문의·클레임 읽어 오기(inquiry_pull.py)와 같은 방식이다(예진 님 제안: 저장 → AXcellent가 읽어 가기).
판매 서버는 고객지원을 부르지 않는다. 고객이 남긴 평가·아이디어와 운영자가 기록한 반품 입고는
판매 서버에 저장되어 있고, 고객지원이 정해진 간격으로 읽어 같은 skill(submit_feedback·submit_idea·
receive_return)로 처리한 뒤 결과 코드를 적는다. 고객이 보는 문의 상태·답변은 거꾸로 고객지원이
판매 서버에 되돌려 쓴다(달라진 것만).

- 처리 결과가 "받지 않음"(해결 전·이미 평가·환불류 아이디어 등)이어도 처리한 것으로 표시하고 결과
  코드를 남긴다. 그래야 같은 접수를 계속 다시 읽지 않는다.
- 연결 끊김·안전 관문 보류처럼 처리되지 않은 접수는 표시하지 않아 다음 주기에 같은 키로 다시 읽는다.
- 같은 접수는 같은 멱등 키라 두 번 읽어도 한 번만 처리된다.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from ax_agent_template import config
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from support_agent import claim_handlers, feedback_handlers, idea_handlers, shop_client
from support_agent import handlers as h
from support_agent.inquiry_pull import BUYER_ID, DEADLINE, ENV_TENANT

logger = logging.getLogger("ax.support.shop_pull_more")

# 처리한 수. 상태 skill이 읽는다 (재시작하면 비워진다)
COUNTS: dict[str, int] = {
    "views_written_total": 0,
    "feedback_ingested_total": 0,
    "ideas_ingested_total": 0,
    "returns_ingested_total": 0,
}


def reset_counts() -> None:
    _RETRIES.clear()
    _CYCLE["n"] = 0
    for key in COUNTS:
        COUNTS[key] = 0


def _request(
    skill: str,
    key: str,
    context_id: str,
    input_: dict[str, Any],
    untrusted: dict[str, Any] | None = None,
) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id=context_id,
            tenant_id=os.getenv(ENV_TENANT, "").strip() or TENANT_ID_DEFAULT,
            token=config.agent_token("support"),
            deadline=datetime.now(UTC) + DEADLINE,
            idempotency_key=key,
        ),
        skill=skill,
        input=input_,
        untrusted=untrusted or {},
    )


def _buyer_number(buyer_id: str) -> int | None:
    found = BUYER_ID.match(buyer_id)
    return int(found.group(1)) if found else None


def view_digest(view: dict[str, Any]) -> str:
    """고객에게 보여 줄 문의 값의 지문. 달라진 것만 다시 쓰는 데 쓴다."""
    text = json.dumps(view, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(text.encode()).hexdigest()[:16]


def customer_inquiry_view(inquiry_id: int) -> dict[str, Any] | None:
    """문의 한 건의 고객용 값(제목·상태·주문·메시지 글·평가 여부·미리보기). 없으면 None.

    내부 분석·초안·분류·상담원 메모는 싣지 않는다 (h.customer_inquiry가 허용한 키만 남긴다).
    """
    conversation = h.STORE.get(inquiry_id)
    if conversation is None or h._is_purged(conversation.id):
        return None
    view = h.customer_inquiry(conversation.model_dump(mode="json"))  # 클레임 목록은 필요 없다
    view["preview"] = conversation.preview
    return view


SWEEP_CYCLES_DEFAULT = 60  # 이 주기마다 한 번 해결·평가가 끝난 문의까지 모두 훑는다(기본 10분)
_CYCLE = {"n": 0}


def sweep_cycles() -> int:
    try:
        value = int(os.getenv("SUPPORT_VIEW_SWEEP_CYCLES") or SWEEP_CYCLES_DEFAULT)
    except ValueError:
        return SWEEP_CYCLES_DEFAULT
    return value if value > 0 else SWEEP_CYCLES_DEFAULT


async def refresh_views() -> dict[str, int]:
    """읽어 간 문의 가운데 고객 화면 값이 달라졌으면 판매 서버에 다시 쓴다 (달라진 것만).

    한 건에서 판매 서버 오류가 나도 나머지는 계속 처리하고, 주기 끝에 오류를 올린다.
    """
    after = written = 0
    shop_error: shop_client.ShopError | None = None
    sweep = _CYCLE["n"] % sweep_cycles() == 0  # 가끔 한 번은 끝난 문의도 훑어 삭제를 놓치지 않는다
    _CYCLE["n"] += 1
    while True:
        page, next_after = await shop_client.fetch_open_inquiry_views(after, sweep)
        for item in page:
            try:
                written += await _refresh_one(item)
            except shop_client.ShopError as exc:
                shop_error = shop_error or exc
        if next_after is None:
            break
        after = next_after
    COUNTS["views_written_total"] += written
    if shop_error is not None:
        raise shop_error
    return {"read": 0, "ingested": written, "failed": 0}


async def _refresh_one(item: dict[str, Any]) -> int:
    conversation = h.STORE.get(int(item["support_inquiry_id"]))
    if conversation is None:
        return 0  # 다시 시작해 비었을 수 있다: 판매 서버의 마지막 값을 그대로 둔다
    if h._is_purged(conversation.id):  # 개인정보로 지웠다: 고객 화면에서 뺀다
        await shop_client.mark_inquiry_gone(int(item["seq"]))
        return 1
    view = customer_inquiry_view(conversation.id)
    if view is None:
        return 0
    digest = view_digest(view)
    if digest == item.get("view_digest"):
        return 0
    await shop_client.write_inquiry_view(int(item["seq"]), view, digest)
    return 1


# 고객에게 보여 줄 아이디어 안내(고객지원 내부 문장이 아니라 고객용 문장)
IDEA_NOTICES = {
    "use_inquiry": "환불·취소·불량 같은 문제는 1:1 문의로 남겨 주세요.",
    "spam": "광고나 링크가 들어 있어 받지 못했어요.",
    "too_short": "조금 더 자세히 적어 주세요.",
    "rate_limited": "하루에 남길 수 있는 의견 수를 넘었어요. 나중에 다시 남겨 주세요.",
}
RETRY_LIMIT_DEFAULT = 6
# 다시 읽는 결과(not_found·invalid_state)를 몇 번째 주기째 보는 중인지. 재시작하면 처음부터 센다
_RETRIES: dict[str, int] = {}


def retry_limit() -> int:
    try:
        value = int(os.getenv("SUPPORT_PULL_RETRY_LIMIT") or RETRY_LIMIT_DEFAULT)
    except ValueError:
        return RETRY_LIMIT_DEFAULT
    return value if value > 0 else RETRY_LIMIT_DEFAULT


def _gave_up(key: str) -> bool:
    """시점 결과를 한도만큼 다시 읽었으면 True(더 읽지 않고 닫는다). 계속 다시 읽는 것을 끝낸다."""
    _RETRIES[key] = _RETRIES.get(key, 0) + 1
    return _RETRIES[key] >= retry_limit()


# 시점 때문에 생길 수 있는 결과(클레임이 아직 입고 단계가 아니다, 고객지원이 다시 시작해 비었다):
# 최종 결과로 닫지 않고 다음 주기에 다시 읽는다
RETRY_OUTCOMES = frozenset({"not_found", "invalid_state"})


def _done_outcome(result: AgentResult, retry: frozenset[str] = frozenset()) -> str | None:
    """처리가 끝난 결과면 짧은 결과 코드, 연결·보류·오류처럼 다시 해 봐야 하면 None."""
    if result.status != TaskStatus.COMPLETED:
        return None
    outcome = str(result.outcome or "")
    if outcome.startswith("not_executed") or outcome in retry:
        return None
    return outcome or "done"


async def _each(
    items: list[dict[str, Any]], handle: Callable[[dict[str, Any]], Awaitable[bool]]
) -> tuple[int, int, shop_client.ShopError | None]:
    """접수를 하나씩 처리한다. 한 건에서 판매 서버 오류가 나도 나머지는 계속 처리한다."""
    done = failed = 0
    shop_error: shop_client.ShopError | None = None
    for item in items:
        try:
            ok = await handle(item)
        except shop_client.ShopError as exc:
            shop_error = shop_error or exc
            ok = False
        done += ok
        failed += not ok
    return done, failed, shop_error


def _result(
    read: int, done: int, failed: int, error: shop_client.ShopError | None
) -> dict[str, int]:
    if error is not None:
        raise error
    return {"read": read, "ingested": done, "failed": failed}


async def pull_feedback(limit: int) -> dict[str, int]:
    """저장된 평가를 읽어 submit_feedback으로 후기로 남긴다."""
    items, _total = await shop_client.fetch_feedback_inbox(limit)

    async def handle(item: dict[str, Any]) -> bool:
        seq, key = int(item["seq"]), str(item["key"])
        user_id = _buyer_number(str(item["buyer_id"]))
        if user_id is None:
            await shop_client.mark_feedback_synced(seq, "bad_buyer_id")
            return True
        request = _request(
            "submit_feedback",
            "shop-feedback:" + key,
            "ctx_shop_" + key[:12],
            {
                "inquiry_id": int(item["support_inquiry_id"]),
                "rating": int(item["rating"]),
                "resolved": bool(item["resolved"]),
                "user_id": user_id,
                "source": "customer",
            },
            {"comment": item.get("comment") or ""},
        )
        result = await feedback_handlers.submit_feedback(request)
        outcome = _done_outcome(result, RETRY_OUTCOMES)
        if outcome is None:
            if result.outcome in RETRY_OUTCOMES and _gave_up("feedback:" + key):
                # 한도만큼 다시 읽어도 안 받는다: 닫는다(고객은 평가를 다시 남길 수 있다)
                _RETRIES.pop("feedback:" + key, None)
                await shop_client.mark_feedback_synced(seq, "gave_up")
                return True
            logger.warning("평가 %s: 처리되지 않음 (%s)", seq, result.outcome)
            return False
        _RETRIES.pop("feedback:" + key, None)
        if outcome == "done":
            outcome = "rated" if result.output.get("rated") is True else "already_rated"
        await shop_client.mark_feedback_synced(seq, outcome)
        return True

    done, failed, error = await _each(items, handle)
    COUNTS["feedback_ingested_total"] += done
    return _result(len(items), done, failed, error)


async def pull_ideas(limit: int) -> dict[str, int]:
    """저장된 아이디어를 읽어 submit_idea로 처리한다. 받지 않은 건 안내 한 줄을 되돌려 쓴다."""
    items, _total = await shop_client.fetch_idea_inbox(limit)

    async def handle(item: dict[str, Any]) -> bool:
        seq, key = int(item["seq"]), str(item["key"])
        user_id = _buyer_number(str(item["buyer_id"]))
        if user_id is None:
            await shop_client.mark_idea_synced(seq, "bad_buyer_id")
            return True
        request = _request(
            "submit_idea",
            "shop-idea:" + key,
            str(item["context_id"]),
            {"kind": item["kind"], "user_id": user_id},
            {"text": item["text"]},
        )
        result = await idea_handlers.submit_idea(request)
        outcome = _done_outcome(result)
        if outcome is None:
            logger.warning("아이디어 %s: 처리되지 않음 (%s)", seq, result.outcome)
            return False
        idea_id = result.output.get("idea_id")
        if outcome == "done":
            await shop_client.mark_idea_synced(seq, "received", str(idea_id) if idea_id else None)
        else:  # 받지 않았다(환불류·광고·짧은 글·한도): 고객에게 보여 줄 안내 한 줄을 함께 적는다
            await shop_client.mark_idea_synced(seq, outcome, None, IDEA_NOTICES.get(outcome))
        return True

    done, failed, error = await _each(items, handle)
    COUNTS["ideas_ingested_total"] += done
    return _result(len(items), done, failed, error)


async def pull_returns(limit: int) -> dict[str, int]:
    """기록된 반품 입고를 읽어 receive_return으로 클레임을 입고 처리한다."""
    items, _total = await shop_client.fetch_return_inbox(limit)

    async def handle(item: dict[str, Any]) -> bool:
        claim_id = int(item["claim_id"])
        claim = claim_handlers.CLAIMS.get(claim_id)
        if claim is not None and str(claim.order_id) != str(item["order_id"]):
            # 판매 서버가 기록한 주문과 클레임의 주문이 다르면 처리하지 않고 표시만 한다
            await shop_client.mark_return_synced(claim_id, "order_mismatch")
            return True
        if claim is not None and claim.return_received_at:
            # 입고는 처리됐는데 표시만 못 한 경우: 상태 확인보다 먼저 같은 결과로 닫는다
            await shop_client.mark_return_synced(claim_id, "received")
            return True
        request = _request(
            "receive_return",
            "shop-return:" + str(claim_id),
            str(item.get("context_id") or "ctx_shop_return" + str(claim_id)),
            {"claim_id": claim_id, "inspection": item.get("inspection")},
        )
        result = await claim_handlers.receive_return(request)
        outcome = _done_outcome(result, RETRY_OUTCOMES)
        if outcome is None:
            if result.outcome in RETRY_OUTCOMES and _gave_up("return:" + str(claim_id)):
                _RETRIES.pop("return:" + str(claim_id), None)
                await shop_client.mark_return_synced(claim_id, "gave_up")
                return True
            logger.warning("반품 입고 %s: 처리되지 않음 (%s)", claim_id, result.outcome)
            return False
        _RETRIES.pop("return:" + str(claim_id), None)
        await shop_client.mark_return_synced(claim_id, "received" if outcome == "done" else outcome)
        return True

    done, failed, error = await _each(items, handle)
    COUNTS["returns_ingested_total"] += done
    return _result(len(items), done, failed, error)
