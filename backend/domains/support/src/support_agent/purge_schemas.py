"""개인정보 삭제 skill 입출력: 고객 요청 삭제와 3년 보유기간 삭제.

고객 원문은 입출력에 싣지 않는다. 건수·이유 코드·날짜만 돌려준다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

PurgeMode = Literal["customer_request", "expired"]


class PreviewPurgeInput(AxModel):
    mode: PurgeMode
    inquiry_id: int | None = None  # customer_request: 삭제를 요청한 문의


class PurgeCustomerDataInput(AxModel):
    inquiry_id: int  # 삭제를 요청한 문의. 이 문의의 구매자 것을 지운다


class PurgeExpiredInput(AxModel):
    """입력 없음: 받은 지 3년이 지난 문의를 모두 지운다."""


class PurgeItem(AxModel):
    inquiry_id: int
    action: Literal["purge", "already_purged", "blocked", "not_found"]
    reasons: list[str] = Field(default_factory=list)  # 막힌 이유 코드 (status_open·claim_linked 등)
    purged: dict[str, int] = Field(default_factory=dict)  # 비운 것의 건수
    retained_until: str | None = None  # 보존 의무로 남겨 둔 문의를 지우는 날 (YYYY-MM-DD)


class PurgeReport(AxModel):
    mode: PurgeMode
    items: list[PurgeItem] = Field(default_factory=list)
    purge_count: int = 0  # 지울 수 있는(집행했으면 지운) 문의 수
    retained_count: int = 0  # 보존 의무로 남겨 둔 문의 수
    applied: bool = False  # 이 호출로 실제로 지웠는가
    # mode=expired: 사람이 SUPPORT_INQUIRY_RETENTION_APPLY=1로 켰는가 (아니면 건수만 센다)
    apply_enabled: bool | None = None


class PurgeExpiredClaimsInput(AxModel):
    """입력 없음: 보유 일수(기본 5년)가 지난 클레임의 글·이름을 모두 지운다."""


class ClaimPurgeItem(AxModel):
    claim_id: int
    purged: dict[str, int] = Field(
        default_factory=dict
    )  # 바꾼 것의 건수 (customer·notes·audit_details)


class ClaimPurgeReport(AxModel):
    items: list[ClaimPurgeItem] = Field(default_factory=list)
    count: int = 0  # 지울 수 있는(집행했으면 지운) 클레임 수
    retention_days: int = 1825  # 보유 일수 (SUPPORT_CLAIM_RETENTION_DAYS, 기본 5년)
    apply_enabled: bool = (
        False  # 사람이 SUPPORT_CLAIM_RETENTION_APPLY=1로 켰는가 (아니면 건수만 센다)
    )
    applied: bool = False  # 이 호출로 실제로 지웠는가
