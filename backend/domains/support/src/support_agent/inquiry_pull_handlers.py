"""문의함 읽기 상태 skill `inquiry_pull_status` (읽기).

판매 서버 문의함을 마지막으로 읽은 결과(연결됨·끊김, 남은 문의 수, 읽은 수, 실패 수)를 알려 준다.
상담 화면이 "연결 끊김"을 바로 보여 주는 데 쓴다. 숫자는 읽기 루프가 센 값이다 (`inquiry_pull.py`).
"""

from __future__ import annotations

from ax_contracts.schemas import AgentRequest, AgentResult
from ax_contracts.schemas.common import AxModel
from support_agent import handlers as h
from support_agent import inquiry_pull


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
    claims_pending: int | None = None  # 판매 서버 클레임함에 아직 남은 신청 수
    claims_ingested_total: int = 0
    claims_failed_total: int = 0
    enabled: bool = False
    interval_seconds: float = 0
    connected: bool | None = None  # 마지막 시도가 성공했는지 (한 번도 안 읽었으면 None)


async def inquiry_pull_status(request: AgentRequest) -> AgentResult:
    return h._done(request, inquiry_pull.status())


INQUIRY_PULL_HANDLERS = {"inquiry_pull_status": inquiry_pull_status}
