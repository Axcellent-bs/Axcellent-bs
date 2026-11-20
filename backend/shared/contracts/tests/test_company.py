"""회사 ID 규칙과 회사·소속 계약 (여러 회사 설계 1-1, 2026-11-20).

- 회사 ID = 기존 `tenant_id` 칸의 값. 영어 소문자·숫자·`-`, 3~40자
- 캣북스의 정식 id는 `catbooks`. `shop-demo`는 별칭이라 들어오면 `catbooks`로 바꿔 담는다
- 기존 칸(`RequestMeta.tenant_id` 등)과 `TENANT_ID_DEFAULT`는 그대로다 (1단계는 동작이 같아야 한다)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from ax_contracts.schemas import (
    TENANT_ID_ALIASES,
    TENANT_ID_CATBOOKS,
    TENANT_ID_DEFAULT,
    TENANT_ID_PATTERN,
    Company,
    ErrorCode,
    Membership,
    RequestMeta,
    TenantId,
)

_TENANT = TypeAdapter(TenantId)


def test_catbooks_id_and_alias() -> None:
    assert TENANT_ID_CATBOOKS == "catbooks"
    assert TENANT_ID_ALIASES == {"shop-demo": "catbooks"}


@pytest.mark.parametrize("value", ["catbooks", "nubi", "studyon", "abc", "a-1", "x" * 40])
def test_tenant_id_accepts_rule(value: str) -> None:
    assert re.fullmatch(TENANT_ID_PATTERN, value)
    assert _TENANT.validate_python(value) == value


@pytest.mark.parametrize(
    "value", ["", "ab", "x" * 41, "Catbooks", "cat_books", "cat books", "캣북스", "cat.books"]
)
def test_tenant_id_rejects_outside_rule(value: str) -> None:
    with pytest.raises(ValidationError):
        _TENANT.validate_python(value)


def test_tenant_id_alias_becomes_canonical() -> None:
    assert _TENANT.validate_python("shop-demo") == "catbooks"


def test_existing_tenant_fields_unchanged() -> None:
    # 기존 칸은 str 그대로, 기본값도 그대로 (계약은 추가만)
    assert TENANT_ID_DEFAULT == "shop-demo"
    meta = RequestMeta(
        context_id="ctx_inc_0001", token="tok", deadline=datetime.now(UTC) + timedelta(minutes=1)
    )
    assert meta.tenant_id == "shop-demo"
    assert "tenant_id" not in meta.model_fields_set


def test_tenant_error_codes() -> None:
    assert ErrorCode("TENANT_REQUIRED") is ErrorCode.TENANT_REQUIRED
    assert ErrorCode("TENANT_MISMATCH") is ErrorCode.TENANT_MISMATCH
    # 기존 코드는 그대로 남는다
    assert ErrorCode("MISSING_CONTEXT") is ErrorCode.MISSING_CONTEXT


def test_membership() -> None:
    m = Membership.model_validate({"company_id": "shop-demo", "role": "admin"})
    assert m.company_id == "catbooks"
    assert m.role == "admin"
    assert Membership(company_id="nubi", role="member").role == "member"
    with pytest.raises(ValidationError):
        Membership(company_id="nubi", role="operator")  # 운영자는 회사 역할이 아니다
    with pytest.raises(ValidationError):
        Membership(company_id="NUBI", role="member")
    with pytest.raises(ValidationError):
        Membership.model_validate({"company_id": "nubi", "role": "member", "extra": 1})


def test_company() -> None:
    kst = timezone(timedelta(hours=9))
    c = Company(
        company_id="catbooks",
        name="캣북스",
        created_at=datetime(2026, 11, 20, 9, 0, tzinfo=kst),
    )
    assert c.industry == ""
    assert c.site_url == ""
    assert c.created_at == datetime(2026, 11, 20, 0, 0, tzinfo=UTC)
    full = Company(
        company_id="shop-demo",
        name="캣북스",
        industry="서점",
        site_url="https://catbooks.example",
        created_at=datetime(2026, 11, 20, tzinfo=UTC),
    )
    assert full.company_id == "catbooks"
    with pytest.raises(ValidationError):
        Company(company_id="nubi", name="", created_at=datetime(2026, 11, 20, tzinfo=UTC))
    with pytest.raises(ValidationError):
        Company(company_id="nubi", name="누비투어", created_at=datetime(2026, 11, 20))
