"""회사와 소속 (여러 회사 설계 1-1, 2026-11-20).

사이트 하나에 여러 회사가 산다. 계정은 회사 밖(전역)에 있고, 계정마다 소속(`Membership`)이
있다. 회사 ID는 기존 `tenant_id` 칸의 값과 같다(`TenantId`).

- `Company`: 회사 목록 한 줄. 회사 목록·지금 회사 응답에 쓴다
- `Membership`: 계정이 속한 회사와 그 회사 안의 역할. 본사 운영자(`operator`)는 회사 역할이 아니다
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, TenantId, UtcDatetime

CompanyRole = Literal["admin", "member"]


class Company(AxModel):
    """회사 하나."""

    company_id: TenantId
    name: str = Field(min_length=1, max_length=80)
    industry: str = Field("", max_length=80)
    site_url: str = Field("", max_length=300)  # 회사 사이트 주소(#1397). 없으면 빈 값
    created_at: UtcDatetime


class Membership(AxModel):
    """계정의 회사 소속 하나."""

    company_id: TenantId
    role: CompanyRole
