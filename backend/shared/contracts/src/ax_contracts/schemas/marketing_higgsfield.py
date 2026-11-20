"""마케팅 Higgsfield 연결 상태 (read, 2026-11-20).

마케팅 화면(SNS·미디어 › 이미지 시트 위)이 Higgsfield에 실제로 붙어 있는지 보여 주려고 읽는다.
키 값은 돌려주지 않는다. 키가 있으면 Higgsfield에 가벼운 읽기 요청을 한 번 보내 키가 받아들여지는지
본다(없는 요청 번호의 상태 조회. 이미지를 만들지 않고 크레딧을 쓰지 않는다).

- `connection`
  - `not_configured`: 키(`MARKETING_HIGGSFIELD_API_KEY`)가 없다
  - `ok`: Higgsfield가 키를 받아들였다
  - `invalid_key`: Higgsfield가 401·403으로 키를 거절했다
  - `unreachable`: 시간 초과·네트워크 오류·5xx로 확인하지 못했다
- `image_model`: 나노바나나 등 이미지 모델 경로 설정값
  (`MARKETING_HIGGSFIELD_IMAGE_MODEL`). 없으면 null.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

HIGGSFIELD_STATUS_SKILL = "higgsfield_status"

HiggsfieldConnection = Literal["ok", "invalid_key", "unreachable", "not_configured"]


class HiggsfieldStatusInput(AxModel):
    """입력 칸은 없다."""


class HiggsfieldStatusOutput(AxModel):
    key_configured: bool
    connection: HiggsfieldConnection
    http_status: int | None = None  # 확인 요청의 HTTP 코드(키·본문은 담지 않는다)
    image_model: str | None = Field(default=None, max_length=120)
    checked_at: datetime
    message_ko: str = Field(min_length=1, max_length=200)
