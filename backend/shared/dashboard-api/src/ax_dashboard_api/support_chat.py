"""고객지원 AI 직접 대화(`/support-chat`) 보조: 보는 사람의 자격과, 대화가 읽는 집계.

대화 자체(저장·답·요약·열람 규칙)는 support의 `chat_*` skill이 한다 (`domains/support/.../chat.py`).
여기서는 두 가지만 한다.

1. **보는 사람**(`viewer_of`): 요청의 `Authorization`에서 사람 토큰 이름과 자격(member·staff·
   admin)을 정한다. 읽기(GET)에도 사람 토큰이 필요하다(없으면 401, 모르면 403). 담당(staff)은
   조직도(`AX_ORG_CONFIG`)에서 고객지원 서비스(inquiry·claims·reviews)의 담당자 이름 +
   `SUPPORT_CHAT_STAFF`(쉼표로 이름을 더함)다. 관리자는 에이전트 토큰 명부의 `admin` 토큰이다.
   지금은 모든 요청이 같은 사람("공용")이라 서버가 사람별로 가를 수 없다. 토큰을 사람별로 나눈
   뒤부터 가려진다.
2. **대화가 읽는 집계**(`snapshot`): 문의 현황·클레임 현황·후기 통계를 support 읽기 skill로 받아
   숫자만 추린다. 고객 이름·제목·미리보기·원문은 담지 않는다 (글자 값은 버린다).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from ax_agent_template.routing import assignment_for
from ax_agent_template.server import verify_token
from ax_dashboard_api import support_client
from ax_dashboard_api.auth import bearer, human_from_authorization
from ax_dashboard_api.envelope import ApiError, or_unavailable

STAFF_ENV = "SUPPORT_CHAT_STAFF"
SUPPORT_SERVICES = ("inquiry", "claims", "reviews")  # 고객지원 AI의 서비스 3개 (services.py)
ADMIN_AGENT = "admin"
UNKNOWN = "확인할 수 없음"


def staff_names() -> set[str]:
    """고객지원 담당자: 조직도의 서비스별 담당자 + 환경 변수로 더한 이름."""
    names = {n.strip() for n in os.getenv(STAFF_ENV, "").split(",") if n.strip()}
    for service in SUPPORT_SERVICES:
        assignment = assignment_for(service, "chat_list")
        if assignment is not None and assignment.assignee:
            names.add(assignment.assignee)
    return names


def viewer_of(authorization: str | None) -> tuple[str, str]:
    """(사람 토큰 이름, 자격). 읽기에도 사람 토큰이 필요하다 (인사 AI 직접 대화와 같다).

    토큰이 없으면 401, 사람 토큰도 admin 토큰도 아니면 403이다. 관리자는 에이전트 토큰 명부의
    `admin` 토큰이다."""
    token = bearer(authorization)
    try:
        name = human_from_authorization(authorization)
    except ApiError:
        if token:
            try:
                if verify_token(token) == ADMIN_AGENT:
                    return ADMIN_AGENT, "admin"
            except PermissionError:
                pass
        raise
    return name, "staff" if name in staff_names() else "member"


# ---------------------------------------------------------------- 대화가 읽는 집계
def _numbers(value: Any) -> Any:
    """숫자와 숫자 표만 남긴다. 글자 값(이름·제목·원문이 들어올 수 있는 자리)은 버린다."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, dict):
        kept = {k: _numbers(v) for k, v in value.items() if isinstance(k, str)}
        return {k: v for k, v in kept.items() if v not in (None, {})}
    return None


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        code = row.get(key)
        if isinstance(code, str) and code.isascii() and len(code) <= 20:  # 분류·긴급도 코드만
            counts[code] = counts.get(code, 0) + 1
    return counts


def inquiries(overview: dict[str, Any] | None) -> dict[str, Any]:
    if overview is None:
        return {"available": False, "note": UNKNOWN}
    waiting = [w for w in overview.get("waiting") or [] if isinstance(w, dict)]
    minutes = [w["waiting_minutes"] for w in waiting if isinstance(w.get("waiting_minutes"), int)]
    return {
        "available": True,
        "kpis": _numbers(overview.get("kpis") or {}),
        "waiting": {
            "count": len(waiting),
            "by_urgency": _count_by(waiting, "urgency"),
            "by_category": _count_by(waiting, "category"),
            "longest_minutes": max(minutes) if minutes else 0,
        },
    }


def claims(stats: dict[str, Any] | None) -> dict[str, Any]:
    if stats is None:
        return {"available": False, "note": UNKNOWN}
    return {"available": True, "stats": _numbers(stats)}


def feedback(overview: dict[str, Any] | None) -> dict[str, Any]:
    if overview is None:
        return {"available": False, "note": UNKNOWN}
    return {"available": True, "overview": _numbers(overview)}


async def _read(skill: str) -> dict[str, Any] | None:
    """읽기 skill 하나. 꺼진 서비스·실패한 읽기는 지어내지 않고 "확인할 수 없음"으로 둔다."""
    try:
        return await or_unavailable(support_client.call(skill), None)
    except ApiError:
        return None


async def snapshot() -> dict[str, Any]:
    """대화에 넘길 집계 (문의 현황·클레임 현황·후기 통계, 숫자만)."""
    return {
        "as_of": datetime.now(UTC).isoformat(timespec="seconds"),
        "inquiries": inquiries(await _read("inquiry_overview")),
        "claims": claims(await _read("claim_stats")),
        "feedback": feedback(await _read("feedback_overview")),
    }
