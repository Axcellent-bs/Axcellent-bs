"""문의함 읽기 상태 skill `inquiry_pull_status` (읽기).

판매 서버 문의함을 마지막으로 읽은 결과(연결됨·끊김, 남은 문의 수, 읽은 수, 실패 수)를 알려 준다.
상담 화면이 "연결 끊김"을 바로 보여 주는 데 쓴다. 숫자는 읽기 루프가 센 값이다 (`inquiry_pull.py`).
"""

from __future__ import annotations

from typing import Any, Literal

from ax_contracts.schemas import AgentRequest, AgentResult
from ax_contracts.schemas.common import AxModel
from support_agent import handlers as h
from support_agent import inquiry_pull, shop_client


class InquiryPullStatusInput(AxModel):
    """입력은 없다."""


class InquiryPullStatus(AxModel):
    last_attempt_at: str | None = None
    last_ok_at: str | None = None
    last_error: str | None = (
        None  # 연결 끊김이면 오류 코드 (UNAVAILABLE 등), 처리 실패면 INGEST_FAILED
    )
    pending: int | None = None  # 판매 서버 문의함에 아직 남은 문의 수(마지막으로 읽었을 때)
    ingested_total: int = 0
    failed_total: int = 0
    inbox_failed: int | None = None  # 판매 서버 문의함에서 실패로 빼 둔 문의 수
    claims_inbox_failed: int | None = None  # 판매 서버 클레임함에서 실패로 빼 둔 신청 수
    claims_pending: int | None = None  # 판매 서버 클레임함에 아직 남은 신청 수
    claims_ingested_total: int = 0
    claims_failed_total: int = 0
    views_written_total: int = 0  # 고객이 보는 문의 값을 판매 서버에 되돌려 쓴 수
    feedback_ingested_total: int = 0  # 판매 서버에 저장된 평가를 읽어 처리한 수
    ideas_ingested_total: int = 0  # 판매 서버 아이디어함을 읽어 처리한 수
    returns_ingested_total: int = 0  # 판매 서버에 기록된 반품 입고를 읽어 처리한 수
    enabled: bool = False
    interval_seconds: float = 0
    connected: bool | None = None  # 마지막 시도가 성공했는지 (한 번도 안 읽었으면 None)


async def inquiry_pull_status(request: AgentRequest) -> AgentResult:
    return h._done(request, inquiry_pull.status())


class StuckIntakeInput(AxModel):
    """입력은 없다."""


class StuckIntakeItem(AxModel):
    kind: Literal["inquiry", "claim"]
    seq: int  # 고객이 받은 접수 번호
    reason: str | None = None  # 처리하지 못한 사유 코드 (고객 글은 싣지 않는다)
    attempts: int = 0
    failed_at: str | None = None
    created_at: str | None = None


class StuckIntake(AxModel):
    items: list[StuckIntakeItem]
    total: int


class RetryStuckIntakeInput(AxModel):
    kind: Literal["inquiry", "claim"]
    seq: int


class RetryStuckIntake(AxModel):
    kind: Literal["inquiry", "claim"]
    seq: int
    requeued: bool


def _unavailable(request: AgentRequest, exc: shop_client.ShopError) -> AgentResult:
    return h._done(
        request, {"message": "판매 서버에 닿지 못했다", "code": exc.code}, outcome="unavailable"
    )


async def list_stuck_intake(request: AgentRequest) -> AgentResult:
    try:
        items = await shop_client.fetch_stuck_intake()
    except shop_client.ShopError as exc:
        return _unavailable(request, exc)
    return h._done(request, {"items": items, "total": len(items)})


@h._caller_meta
async def retry_stuck_intake(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, RetryStuckIntakeInput)
    if isinstance(parsed, AgentResult):
        return parsed

    async def apply() -> tuple[dict[str, Any], bool]:
        try:
            done = await shop_client.retry_stuck_intake(parsed.kind, parsed.seq)
        except shop_client.ShopError as exc:
            return {
                "kind": parsed.kind,
                "seq": parsed.seq,
                "requeued": False,
                "error": exc.code,
            }, True
        return {
            "kind": parsed.kind,
            "seq": parsed.seq,
            "requeued": bool(done.get("requeued")),
        }, False

    return await h._write(
        request,
        "retry_stuck_intake",
        f"{parsed.kind}:{parsed.seq}",
        {"kind": parsed.kind, "seq": parsed.seq},
        apply,
    )


INQUIRY_PULL_HANDLERS = {
    "inquiry_pull_status": inquiry_pull_status,
    "list_stuck_intake": list_stuck_intake,
    "retry_stuck_intake": retry_stuck_intake,
}
