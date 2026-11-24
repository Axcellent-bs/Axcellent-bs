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

회사 계정 연결(원격 MCP + OAuth, 회사 구독 크레딧, 11/23 추가. 칸은 모두 선택이라 옛 응답도 맞다)
- `source`: 그림·영상을 어느 길로 만드는가. `mcp`(회사가 연결한 Higgsfield 계정, 구독 크레딧) |
  `api_key`(키, API 크레딧) | null(둘 다 없음). 회사 계정 연결이 있으면 늘 `mcp`가 먼저다
- `source=mcp`일 때 `connection`은 그 연결의 상태다: `ok` | `invalid_key`(Higgsfield가 연결을
  거절 → 다시 연결) | `unreachable`. `key_configured`는 그대로 API 키가 있는지다
- `credits`·`plan`: 연결한 계정의 남은 크레딧과 구독 요금제(Higgsfield `balance`). 모르면 null
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

HIGGSFIELD_STATUS_SKILL = "higgsfield_status"

HiggsfieldConnection = Literal["ok", "invalid_key", "unreachable", "not_configured"]
HiggsfieldSource = Literal["mcp", "api_key"]


class HiggsfieldStatusInput(AxModel):
    """입력 칸은 없다."""


class HiggsfieldStatusOutput(AxModel):
    key_configured: bool
    connection: HiggsfieldConnection
    http_status: int | None = None  # 확인 요청의 HTTP 코드(키·본문은 담지 않는다)
    image_model: str | None = Field(default=None, max_length=120)
    checked_at: datetime
    message_ko: str = Field(min_length=1, max_length=200)
    source: HiggsfieldSource | None = None
    credits: float | None = Field(default=None, ge=0)
    plan: str | None = Field(default=None, max_length=40)
    # 아래는 예전 응답은 없다 (11/24). 실패 사유 코드(network·timeout·http_<n>·refresh_failed·
    # tool_error·bad_response·rpc_error·client_error·unauthorized·reconnect_needed·not_connected·
    # client_id_missing 등)와 Higgsfield가 준 문장을 정리한 것(주소·토큰 모양 가림, 120자).
    # connection 값은 그대로라 옛 화면은 지금처럼 보인다. 성공이면 둘 다 None
    reason_code: str | None = Field(default=None, max_length=60)
    detail_ko: str | None = Field(default=None, max_length=160)
