"""짝 파일: 고객사 API 자원(예약·투어)을 shop-api 자원(주문·상품)에 잇는 규칙.

파일은 `<AX_SITE_ADAPTER_DIR>/mapping.json`(기본 `backend/.data/site_adapter/`). 항목 식은 셋뿐이다.
`AX_SITE_ADAPTER_DIR`을 정하지 않았고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`, 설계 1-4)
`<회사 폴더>/catbooks/site_adapter/`(옛 폴더 `.data/site_adapter`는 처음 읽을 때 통째로 옮긴다).
`AX_COMPANY_FOLDERS=off`면 옛 폴더가 비어 있을 때 회사 폴더 값으로 되살린다.
위는 캣북스 얘기다. 다른 회사는 `<회사 폴더>/<회사 ID>/site_adapter/`에 따로 둔다(`adapter_dir`).
- 점 경로 `contact.name`: 그 값 (값이 null이면 None, 경로가 없으면 `MissingField`)
- `sum:a,b`: 정수 칸들의 합 (null은 0)
- `const:<값>`: 그 글자 그대로
- `first:a,b`: 비어 있지 않은 첫 값 (모두 비면 None, 경로가 하나도 없으면 `MissingField`)

쓰기 짝(`writes`, 동작 → `WriteMap`)은 추가 필드다. 없으면 모든 쓰기가 할 일함으로 간다(예전
짝 파일 그대로 읽힘). 쓰기 본문 식은 위 셋에 `path:`·`key_code:`·`now_plus:`·`json:`·`link:`를
더한다. 여기서는 모양만 검사하고, 계산은 직접 쓰기(`direct.evaluate`)에서 한다.

받은함 짝(`inboxes`, 받은함 → `ResourceMap`)도 추가 필드다. 지금은 `claims`(취소·환불 신청)만
있고(변환은 `claims.py`), `inquiries`(1:1 문의, `inquiries.py`)도 있다.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from ax_agent_template import company_files, company_scope
from ax_agent_template.companies import company_path

MAPPING_DIR_ENV = "AX_SITE_ADAPTER_DIR"
DEFAULT_DIR = Path(__file__).resolve().parents[5] / ".data" / "site_adapter"
MAPPING_FILE = "mapping.json"

Resource = Literal["orders", "products"]
# 고객지원 받은함 (① 읽기 중 접수함). 문의(`inquiries`)는 다음 PR
Inbox = Literal["claims", "inquiries"]
# 쓰기 동작. shop-api 경로 → 동작은 변환기 코드(`writes.py`)에 고정한다(짝 파일이 정하지 않는다)
WriteAction = Literal[
    "refund",
    "coupon_issue",
    "banner_post",
    "banner_end",
    "claim_result",
    "inquiry_answer",
    "payment_rollback",
    "fault_release",
]


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


def local_path(value: str) -> str:
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


class ResourceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str

    @field_validator("path")
    @classmethod
    def _local_path(cls, value: str) -> str:
        return local_path(value)

    paging: Paging = Paging()
    fields: dict[str, str]
    values: dict[str, dict[str, str]] = {}
    # 주문 한 건에 상품이 여러 개인 사이트: 주문 줄 목록과 줄마다의 짝
    # (없으면 주문 한 건 = 상품 하나)
    items: ItemsMap | None = None


_KEY_CODE = re.compile(r"[A-Z0-9_-]{1,30}")
_LINK = re.compile(r"[a-z_]+\.[a-z_]+")
_PATH_PARAM = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_SITE_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def check_write_expr(expr: str) -> str:
    """쓰기 본문 식 하나의 모양만 본다(계산은 W3). 틀리면 ValueError."""
    if expr.startswith("key_code:"):
        if not _KEY_CODE.fullmatch(expr[len("key_code:") :]):
            raise ValueError(f"key_code: 접두는 A-Z 0-9 _ - 30자 이하여야 해요: {expr}")
    elif expr.startswith("now_plus:"):
        if not expr[len("now_plus:") :].isdigit():
            raise ValueError(f"now_plus:는 날 수(정수)여야 해요: {expr}")
    elif expr.startswith("json:"):
        try:
            json.loads(expr[len("json:") :])
        except ValueError as exc:
            raise ValueError(f"json: 값이 JSON이 아니에요: {expr}") from exc
    elif expr.startswith("link:"):
        if not _LINK.fullmatch(expr[len("link:") :]):
            raise ValueError(f"link:는 <자원>.<칸> 모양이어야 해요: {expr}")
    elif expr.startswith("first:"):
        for part in expr[len("first:") :].split(","):
            check_write_expr(part.strip())
    elif expr.startswith(("path:", "const:", "sum:")):
        pass
    elif not expr or any(not part for part in expr.split(".")):
        raise ValueError(f"짝 식이 비었거나 점 경로가 깨졌어요: {expr!r}")
    return expr


class WriteMap(BaseModel):
    """쓰기 짝 하나: shop-api 쓰기 동작 → 고객사 쓰기 경로·본문."""

    model_config = ConfigDict(extra="forbid")
    method: Literal["POST", "PUT", "PATCH", "DELETE"]
    path: str
    path_params: dict[str, str] = {}
    body: dict[str, str] = {}
    ok: list[int] = [200, 201]
    # 고객사 응답 → shop-api 응답 칸 (필요한 동작만, 예: 쿠폰 `coupon_id`)
    result: dict[str, str] = {}
    # 추가 필드: "쓰기는 반영됐는데 뒤따르는 일만 실패"를 뜻하는 고객사 오류 코드
    # (예: 누비투어 취소 결정 502 `REFUND_FAILED` = 결정은 반영, 결제사 환불만 실패).
    # 이 코드면 `unknown`이 아니라 `partial`로 적고 할 일함에 따로 까닭을 남긴다
    partial_codes: list[str] = []

    @field_validator("path")
    @classmethod
    def _local_path(cls, value: str) -> str:
        return local_path(value)

    @field_validator("partial_codes")
    @classmethod
    def _site_codes(cls, value: list[str]) -> list[str]:
        if any(not _SITE_CODE.fullmatch(code) for code in value):
            raise ValueError("partial_codes는 A-Z a-z 0-9 _ . - 64자 이하 코드만 쓸 수 있어요")
        return value

    @field_validator("ok")
    @classmethod
    def _ok_codes(cls, value: list[int]) -> list[int]:
        if not value or any(not 200 <= code < 300 for code in value):
            raise ValueError("ok는 2xx 응답 코드만 쓸 수 있어요")
        return value

    @field_validator("path_params", "body")
    @classmethod
    def _exprs(cls, value: dict[str, str]) -> dict[str, str]:
        for expr in value.values():
            check_write_expr(expr)
        return value

    @model_validator(mode="after")
    def _params_match_path(self) -> WriteMap:
        names = set(_PATH_PARAM.findall(self.path))
        if names != set(self.path_params):
            raise ValueError(
                f"path의 {{이름}}과 path_params가 맞지 않아요: {sorted(names)} / "
                f"{sorted(self.path_params)}"
            )
        return self


class SiteMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    resources: dict[Resource, ResourceMap]
    # 추가 필드: 동작별 쓰기 짝. 없으면 모든 쓰기가 할 일함 (예전 짝 파일 그대로 읽힘)
    writes: dict[WriteAction, WriteMap] = {}
    # 추가 필드: 고객지원 받은함 짝 (`claims` = 취소·환불 신청). 없으면 그 받은함은 404
    # `NOT_MAPPED`(예전 그대로). 회사 연결 화면의 주문·상품 짝 저장(`resources`)과 따로 둔다
    inboxes: dict[Inbox, ResourceMap] = {}


ADAPTER_DIR_NAME = "site_adapter"


def adapter_dir(company_id: str | None = None) -> Path:
    """그 회사의 변환기 폴더(짝·키·장부). 회사는 준 값 → 지금 요청 회사 → 캣북스.

    - 캣북스: 환경변수 → 회사 폴더(켰을 때, 옛 폴더는 처음에 옮긴다) → 기본
      `backend/.data/site_adapter` (예전 그대로)
    - 다른 회사(여러 회사 설계 6.3): 늘 `<회사 폴더>/<회사 ID>/site_adapter`. 환경변수·옛 폴더는
      캣북스 것이라 쓰지도 옮기지도 않는다. 회사 폴더를 정하지 않았으면
      `backend/.data/companies/<회사 ID>/site_adapter`
    """
    cid = company_scope.scope_id(company_id or company_scope.active())
    if cid != company_scope.CATBOOKS:
        found = company_path(cid, ADAPTER_DIR_NAME)
        return (
            found
            if found is not None
            else DEFAULT_DIR.parent / "companies" / cid / ADAPTER_DIR_NAME
        )
    raw = os.getenv(MAPPING_DIR_ENV, "").strip()
    if not raw and company_files.folders_on():
        return company_files.company_file(ADAPTER_DIR_NAME)
    folder = Path(raw) if raw else DEFAULT_DIR
    # 되돌리기(off): 옛 폴더가 비어 있으면 회사 폴더 값으로 되살린다
    company_files.restore_old(ADAPTER_DIR_NAME, target=folder)
    return folder


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
