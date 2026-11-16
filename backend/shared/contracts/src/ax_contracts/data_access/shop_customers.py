"""판매 서버 고객 내보내기 (data 수집기가 사용, 고객·시장 분석의 원천).

`GET /api/shop/customers-export?cursor&limit` → `ShopCustomersExportResult`. 읽기 전용이다.

- 커서 규칙은 판매 기록 내보내기(`shop_sales`)와 같다: `(updated_at, buyer_id)` 오름차순,
  `next_cursor`를 다음 요청의 `cursor`로 넘긴다. 받을 것이 없으면 같은 커서를 돌려준다.
  속성이 바뀐 고객은 `updated_at`이 바뀌어 다시 나오고, 받는 쪽은 같은 `buyer_id`를 덮어쓴다.
- 개인정보: 가명 `buyer_id`와 가입 시각, 회원이 고른 선택 속성 묶음(`attributes`)만 담는다.
  이름·연락처·주소·생년월일은 담지 않는다(연령은 연령대 코드로만).
- `attributes`는 `{속성 key: 값 code}`. 어떤 key가 있는지는 회사 설정(카탈로그 `[sales]
  attributes`)이 정한다. 받는 쪽은 설정에 없는 key·값을 "매핑 안 됨"으로 센다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime

CUSTOMERS_EXPORT_LIMIT_MAX = 1000


class ShopCustomersExportQuery(AxModel):
    """입력. `cursor`가 없으면 처음부터."""

    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=500, ge=1, le=CUSTOMERS_EXPORT_LIMIT_MAX)
    tenant_id: str = TENANT_ID_DEFAULT


class CustomerRow(AxModel):
    """고객 한 명 (가명)."""

    buyer_id: str
    joined_at: UtcDatetime
    updated_at: UtcDatetime
    attributes: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code (선택 항목)
    tenant_id: str = TENANT_ID_DEFAULT


class ShopCustomersExportResult(AxModel):
    """출력. `customers`는 `(updated_at, buyer_id)` 오름차순."""

    customers: list[CustomerRow] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    collected_at: UtcDatetime | None = None
    tenant_id: str = TENANT_ID_DEFAULT
