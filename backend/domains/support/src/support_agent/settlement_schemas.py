"""판매자 정산 skill의 입력 모델. 출력은 재무 화면이 쓰던 정산서 모양 그대로다(dict)."""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel

PERIOD_PATTERN = r"^\d{4}-W\d{2}$"


class SellerSettlementInput(AxModel):
    period: str | None = Field(default=None, pattern=PERIOD_PATTERN)


class SellerSettlementWeeksInput(AxModel):
    periods: list[str] = Field(min_length=1, max_length=30)


class SellerSettlementBatchesInput(AxModel):
    pass


class SellerSettlementDeadlinesInput(AxModel):
    pass


class RequestSellerSettlementInput(AxModel):
    period: str | None = Field(default=None, pattern=PERIOD_PATTERN)
    # 버튼을 누른 사람 (대시보드 API가 사람 토큰에서 꺼내 넣는다). 요청한 사람은 승인하지 못한다
    requested_by: str = Field(min_length=1, max_length=64)
