"""data AI가 쓸 LLM 설정 (여러 회사 6+, 2026-11-23). 고객지원 #1588과 같은 자리·같은 방식이다.

서버 설정(`DATA_LLM_*`·`LLM_*`, `LLMSettings.from_env("data")`) 위에 **그 요청의 회사**
(`company_scope.active()`) AI 키·주소·모델이 있으면 회사 값을 얹는다(`company_ai.apply`).
`company_ai.settings_for`는 설치의 지금 회사(`tenant.current()`)를 쓰므로 data는 쓰지 않는다
(data는 요청마다 회사가 다르다).

- 회사 키가 없거나, 키 저장소가 db 모드가 아니거나, 회사 연결 전이면 서버 설정 그대로다
- 부를 때마다 읽으므로 회사 키를 바꿔도 다시 켤 필요가 없다
- 회사 주소·모델이 비면 서버 값을 쓴다. 임베딩 값은 건드리지 않는다(회사 임베딩은 결정 대기)
- `source()`: 화면용 (종류, 이름). 키 값은 넣지 않고 끝 4자리(`company_ai.hint`)만 쓴다
- `key_scope()`: 키 오류 멈춤(`llm_halt`)을 나누는 값. 회사 키면 `company:<회사>:<키 지문>`이라
  키를 바꾸면 바로 풀리고, 서버 키면 `server`라 서버 키를 쓰는 회사들만 같이 멈춘다
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from typing import Literal

from ax_agent_template import company_ai
from ax_agent_template.companies import get_company
from ax_agent_template.llm import LLMSettings
from data_agent import company_scope

DEPT = "data"
KeyKind = Literal["company", "server", "none"]
SERVER_SCOPE = "server"


def _company() -> tuple[str, company_ai.CompanyLlm | None]:
    cid = company_scope.active()
    return cid, company_ai.company_llm(cid)


def settings(env: Mapping[str, str] | None = None) -> LLMSettings:
    """서버 설정 위에 요청 회사의 키·주소·모델을 얹은 설정."""
    return company_ai.apply(LLMSettings.from_env(DEPT, env), company_scope.active())


def server_key_source(env: Mapping[str, str]) -> str:
    """서버 키의 이름 (값이 아님): data 전용 → 공통 폴백 → 없음. LLMSettings와 같은 순서."""
    if (env.get("DATA_LLM_API_KEY") or "").strip():
        return "DATA_LLM_API_KEY"
    if (env.get("LLM_API_KEY") or "").strip():
        return "공통 LLM_API_KEY(폴백)"
    return "키 없음"


def company_name(cid: str) -> str:
    company = get_company(cid)
    return company.name if company is not None and company.name else cid


def source(env: Mapping[str, str] | None = None) -> tuple[KeyKind, str]:
    """(종류, 화면용 이름). 회사 키면 "<회사 이름> 회사 키(…끝4자리)", 아니면 서버 키 이름."""
    env = os.environ if env is None else env
    cid, company = _company()
    if company is not None:
        tail = company_ai.hint(company.api_key)
        return "company", f"{company_name(cid)} 회사 키" + (f"(…{tail})" if tail else "")
    name = server_key_source(env)
    return ("none" if name == "키 없음" else "server"), name


def key_scope() -> str:
    """키 오류 멈춤 단위. 회사 키면 회사·키 지문, 서버 키면 `server`.

    지문은 키 해시 앞 12자다(끝 4자리가 같은 다른 키로 바꿔도 풀린다). 메모리에만 두고 화면·로그에는
    내지 않는다."""
    cid, company = _company()
    if company is None:
        return SERVER_SCOPE
    print_ = hashlib.sha256(company.api_key.encode()).hexdigest()[:12]
    return f"company:{cid}:{print_}"
