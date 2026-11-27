"""요청마다 회사 고르기 (여러 회사 5단계 = 요청마다 회사로 저장소 고르기, 2026-11-23).

고객지원(#1602) `company_scope.py`와 같은 함수 다섯 개(`scope_id`·`active`·`visible`·`using`·
`scoped`)를 공용으로 올렸다. 서버(`server.py`)가 skill 요청마다 `meta.tenant_id`로 `using`을
걸어 두므로 도메인 핸들러는 따로 감싸지 않아도 `active()`로 그 요청의 회사를 읽는다.

- 어느 회사인가:
  - skill 요청: 서버가 핸들러를 부르는 동안 `meta.tenant_id`의 정식 id (contextvar. 그 요청에서
    띄운 작업(asyncio task)도 같은 값을 잇는다)
  - 그 밖(스스로 도는 루틴·회사 모르는 HTTP 경로): None. `visible`은 캣북스 건만 보인다(11/27)
  - 별칭 `shop-demo`는 `catbooks`로, 없거나 형식이 맞지 않는 값은 캣북스
- 회사 목록에 없는 값·형식이 틀린 값은 거절하지 않고 경고 로그만 남긴다(`checked`, 값마다 한 번).
  거절(`TENANT_REQUIRED`)은 설계 4장 강제 단계에서 켠다. 그전에는 경고만
- 저장소는 `visible(tenant_id)`로 건마다 거르거나 `active()`로 회사별 저장소를 고른다.
  다른 회사 건은 없는 것처럼 보인다(404와 같은 결과)
"""

from __future__ import annotations

import contextvars
import functools
import logging
import re
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from ax_agent_template.companies import canonical_company_id, get_company, is_company
from ax_agent_template.industry_terms import DEFAULT as DEFAULT_TERMS
from ax_agent_template.industry_terms import Terms, terms_for
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

CATBOOKS = TENANT_ID_CATBOOKS
# 경고를 한 번씩만 남길 값의 수 상한 (토큰을 가진 부서만 부르므로 많지 않다)
_WARN_LIMIT = 256

logger = logging.getLogger("ax.company_scope")
_ACTIVE: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_company", default=None
)
_warned: set[str] = set()


def scope_id(company_id: str | None) -> str:
    """정식 회사 ID. 없거나 맞지 않으면 캣북스."""
    return canonical_company_id(company_id) or CATBOOKS


def active() -> str | None:
    """지금 요청의 회사. skill 요청 밖(스스로 도는 루틴)이면 None."""
    return _ACTIVE.get()


def visible(tenant_id: str | None) -> bool:
    """이 회사의 건을 지금 보여도 되는가. 회사가 안 걸렸으면 캣북스 건만.

    11/27: 예전에는 회사 없음(캣북스 세션·스스로 도는 루틴) = 모든 회사 통과라 캣북스 화면·캣북스
    주기 차례에 다른 회사 건이 보였다. 회사 칸이 없거나 옛 값(`shop-demo`)인 건은 `scope_id`가
    캣북스로 보므로 그대로 캣북스에 남는다. 모든 회사를 도는 작업은 `company_jobs`로 회사마다 건다.
    """
    company = _ACTIVE.get() or CATBOOKS
    return scope_id(tenant_id) == company


@contextmanager
def using(company_id: str | None) -> Iterator[str]:
    """이 블록 안에서 저장소가 그 회사 건만 보게 한다."""
    token = _ACTIVE.set(scope_id(company_id))
    try:
        yield _ACTIVE.get() or CATBOOKS
    finally:
        _ACTIVE.reset(token)


def scoped(handler: Callable[[Any], Awaitable[Any]]) -> Callable[[Any], Awaitable[Any]]:
    """skill 핸들러 감싸기: 요청 meta.tenant_id를 그 요청의 회사로.

    서버가 이미 걸어 주므로 새 코드는 쓰지 않아도 된다. 고객지원처럼 직접 감싼 곳이 그대로 돌게 둔다
    (같은 값을 한 번 더 거는 것이라 결과가 같다).
    """

    @functools.wraps(handler)
    async def run(request: Any) -> Any:
        meta = getattr(request, "meta", None)
        with using(getattr(meta, "tenant_id", None)):
            return await handler(request)

    return run


def checked(tenant_id: str | None) -> str:
    """요청 회사 값을 정식 id로. 틀린 값·목록에 없는 회사는 경고만 남기고 거절하지 않는다.

    - 형식이 틀리거나 비었으면 캣북스(`scope_id`와 같다)
    - 형식은 맞는데 회사 목록에 없으면 그 id 그대로 쓴다(그 회사 건만 보이니 다른 회사와 섞이지
      않는다). 회사를 목록에 넣기 전에 요청이 먼저 와도 데모가 멈추지 않게 한다
    """
    cid = canonical_company_id(tenant_id)
    if cid is None:
        _warn_once(f"bad:{tenant_id!r}", "요청 회사 값이 맞지 않아 캣북스로 본다: %r", tenant_id)
        return CATBOOKS
    if cid != CATBOOKS and not is_company(cid):
        _warn_once(f"unknown:{cid}", "회사 목록에 없는 요청 회사다(거절하지 않음): %s", cid)
    return cid


_PROMPT_TEXT_MAX = 40  # 지시문에 싣는 회사 이름·업종 한 칸 길이 상한
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def _prompt_text(value: str) -> str:
    return _CONTROL.sub(" ", value).strip()[:_PROMPT_TEXT_MAX].strip()


def prompt_line() -> str:
    """LLM 지시문 앞에 붙일 지금 요청 회사 한 줄. 회사 설정(회사 목록)의 이름·업종을 읽는다.

    요청 밖이거나 회사 목록에 없으면 빈 문자열이다(지시문은 "회사"라는 말만 쓴다).
    업종을 지어내지 않는다: 설정에 없으면 이름만 싣는다.
    """
    cid = _ACTIVE.get()
    company = get_company(cid) if cid else None
    if company is None:
        return ""
    name = _prompt_text(company.name)
    if not name:
        return ""
    industry = _prompt_text(company.industry)
    line = f"이 회사: {name}" + (f" (업종: {industry})" if industry else "")
    words = terms_for(company.industry)
    if words != DEFAULT_TERMS:  # 업종에 맞는 말이 따로 있으면 그 말로 답하게 알려 준다
        line += f". 파는 것은 '{words.item}', 고객이 사는 일은 '{words.order}'(이)라고 부른다"
    return line + "\n"


def terms() -> Terms:
    """지금 요청 회사의 업종 낱말(상품·주문을 부르는 말). 회사를 모르면 기본 낱말."""
    cid = _ACTIVE.get()
    company = get_company(cid) if cid else None
    return terms_for(company.industry) if company else DEFAULT_TERMS


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned or len(_warned) >= _WARN_LIMIT:
        return
    _warned.add(key)
    logger.warning(message, *args)


def reset_for_tests() -> None:
    _warned.clear()
