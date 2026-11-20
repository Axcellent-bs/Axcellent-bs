"""모든 계약 모델이 같이 쓰는 기본 요소.

- `AxModel`: 계약 모델의 기반 클래스. 정의하지 않은 필드가 들어오면 거부한다
  (`allowedRequesters` 같은 camelCase 오타를 잡기 위해서다).
- `UtcDatetime`: 시간은 항상 UTC. 시간대가 없는 값은 거부하고, 다른 시간대는 UTC로 바꾼다.
- `TENANT_ID_DEFAULT`: 고객사 ID 기본값 (`shop-demo`).
- `TenantId`: 회사 ID 형식 (여러 회사 설계 1-1, 2026-11-20). 회사 ID는 기존 `tenant_id` 칸의 값이다.
  영어 소문자·숫자·`-`, 3~40자. 별칭(`shop-demo`)은 정식 id(`catbooks`)로 바꿔 담는다.
  기존 `tenant_id` 칸들은 `str` 그대로 두고, 새 계약에만 쓴다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, StringConstraints

TENANT_ID_DEFAULT = "shop-demo"

# 회사 ID 값 규칙. 회사 설정 폴더 id·키 저장소 tenant와 같은 값으로 맞춘다
TENANT_ID_PATTERN = r"^[a-z0-9-]{3,40}$"
# 캣북스의 정식 회사 ID (예진 결정 11/20)
TENANT_ID_CATBOOKS = "catbooks"
# 별칭 → 정식 id. 저장할 때는 늘 정식 id로 쓰고, 읽을 때만 별칭을 받는다
TENANT_ID_ALIASES: dict[str, str] = {TENANT_ID_DEFAULT: TENANT_ID_CATBOOKS}


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]


def _canonical_tenant_id(value: str) -> str:
    return TENANT_ID_ALIASES.get(value, value)


TenantId = Annotated[
    str, StringConstraints(pattern=TENANT_ID_PATTERN), AfterValidator(_canonical_tenant_id)
]


class AxModel(BaseModel):
    """계약 모델 기반 클래스. 우리가 정의하는 필드는 모두 snake_case다."""

    model_config = ConfigDict(extra="forbid")
