"""짝 파일: 고객사 API 자원(예약·투어)을 shop-api 자원(주문·상품)에 잇는 규칙.

파일은 `<AX_SITE_ADAPTER_DIR>/mapping.json`(기본 `backend/.data/site_adapter/`). 항목 식은 셋뿐이다.
`AX_SITE_ADAPTER_DIR`을 정하지 않았고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`, 설계 1-4)
`<회사 폴더>/catbooks/site_adapter/`(옛 폴더 `.data/site_adapter`는 처음 읽을 때 통째로 옮긴다).
- 점 경로 `contact.name`: 그 값 (값이 null이면 None, 경로가 없으면 `MissingField`)
- `sum:a,b`: 정수 칸들의 합 (null은 0)
- `const:<값>`: 그 글자 그대로
- `first:a,b`: 비어 있지 않은 첫 값 (모두 비면 None, 경로가 하나도 없으면 `MissingField`)
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from ax_agent_template import company_files

MAPPING_DIR_ENV = "AX_SITE_ADAPTER_DIR"
DEFAULT_DIR = Path(__file__).resolve().parents[5] / ".data" / "site_adapter"
MAPPING_FILE = "mapping.json"

Resource = Literal["orders", "products"]


class MappingError(Exception):
    """짝 파일이 깨졌거나 짝이 응답과 맞지 않는다."""


class MissingField(MappingError):
    def __init__(self, field: str) -> None:
        super().__init__(f"응답에 {field} 항목이 없어요")
        self.field = field


class Paging(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items_field: str = "data"
    next_field: str = "next_cursor"
    cursor_param: str = "cursor"
    since_param: str | None = "updated_since"
    limit_param: str = "limit"
    limit: int = 200


class ItemsMap(BaseModel):
    """주문 줄 목록 짝. `path`는 주문 안의 목록 칸.

    `fields`는 줄마다의 product_id·qty·unit_price·paid_amount.
    """

    model_config = ConfigDict(extra="forbid")
    path: str
    fields: dict[str, str]


class ResourceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str

    @field_validator("path")
    @classmethod
    def _local_path(cls, value: str) -> str:
        """고객사 주소 뒤에 붙는 경로만 받는다. 다른 호스트로 키가 새지 않게 한다."""
        bad = (
            not value.startswith("/")
            or value.startswith("//")
            or any(c in value for c in "@?#\\")
            or "://" in value
        )
        if bad:
            raise ValueError("path는 /로 시작하는 경로만 쓸 수 있어요(@ ? # // 금지)")
        return value

    paging: Paging = Paging()
    fields: dict[str, str]
    values: dict[str, dict[str, str]] = {}
    # 주문 한 건에 상품이 여러 개인 사이트: 주문 줄 목록과 줄마다의 짝
    # (없으면 주문 한 건 = 상품 하나)
    items: ItemsMap | None = None


class SiteMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    resources: dict[Resource, ResourceMap]


ADAPTER_DIR_NAME = "site_adapter"


def adapter_dir() -> Path:
    """변환기 폴더. 환경변수 → 회사 폴더(켰을 때) → 기본 `backend/.data/site_adapter`."""
    raw = os.getenv(MAPPING_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    if company_files.folders_on():
        return company_files.company_file(ADAPTER_DIR_NAME)
    return DEFAULT_DIR


def load_mapping() -> SiteMapping | None:
    """짝 파일. 없으면 None(아직 짝을 정하지 않음), 깨졌으면 `MappingError`."""
    path = adapter_dir() / MAPPING_FILE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise MappingError(f"짝 파일을 읽지 못했어요: {exc}") from exc
    try:
        return SiteMapping.model_validate(json.loads(text))
    except (ValueError, ValidationError) as exc:
        raise MappingError(f"짝 파일이 깨졌어요: {exc}") from exc


def pick(row: dict[str, Any], expr: str) -> Any:
    """원본 행에서 짝 식 하나의 값을 꺼낸다."""
    if expr.startswith("const:"):
        return expr[len("const:") :]
    if expr.startswith("sum:"):
        return sum(int(pick(row, part.strip()) or 0) for part in expr[len("sum:") :].split(","))
    if expr.startswith("first:"):
        found = False
        for part in expr[len("first:") :].split(","):
            try:
                value = pick(row, part.strip())
            except MissingField:
                continue
            found = True
            if value not in (None, ""):
                return value
        if not found:
            raise MissingField(expr)
        return None
    value: Any = row
    for key in expr.split("."):
        if not isinstance(value, dict) or key not in value:
            raise MissingField(expr)
        value = value[key]
    return value
