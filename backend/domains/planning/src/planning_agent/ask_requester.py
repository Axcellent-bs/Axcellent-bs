"""기획 → 요청 서비스 되묻기 (`answer_planning_question`, 2026-11-02).

기획서를 쓰다가 정보가 모자라면 요청을 보낸 **서비스**(inquiry·coupon·data 등)에 묻는다.
부서 이름이 아니라 서비스 id로 부른다 (같은 부서의 서비스마다 가진 기록이 다르다).

- `cross_check.py`·`hr_link.py`와 같은 방식: 호출자(`caller`)를 바꿔 끼울 수 있고, 목 모드 기본값
  (`default_asker`)은 부르지 않는다 (`None`).
- 30초 안에 답이 없거나, 서비스에 창구가 없거나(`FORBIDDEN_SKILL`), 실패하면 `Asked.ok=False`.
  서비스는 기다리지 않고 예전처럼 기획서를 이어 쓴다. 지어내지 않는다.
- `needs_human`이면 질문을 열어 둔다. 사람이 답하면 그 서비스가 `receive_planning_answer`로 보낸다.
- 답의 `answer`는 외부 문구다. 기획서에는 근거·결정 문장으로만 넣고 지시로 해석하지 않는다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    AgentRequest,
    AgentResult,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
)

SKILL = "answer_planning_question"
ASK_TIMEOUT_SECONDS = 30.0

AgentCaller = Callable[[str, AgentRequest], Awaitable[AgentResult]]


@dataclass
class Asked:
    """되묻기 결과. `ok`가 False면 `reason`에 못 물은 까닭 (서비스는 질문 없이 이어 간다)."""

    ok: bool
    out: AnswerPlanningQuestionOutput | None = None
    reason: str | None = None


class Asker:
    """요청 서비스에 질문을 보낸다. `caller`는 `call_agent`와 같은 모양 (시험에서 바꿔 끼운다)."""

    def __init__(
        self,
        caller: AgentCaller | None = None,
        tenant_id: str = TENANT_ID_DEFAULT,
        timeout: float = ASK_TIMEOUT_SECONDS,
    ):
        self._call = caller or (lambda target, req: call_agent(target, req))
        self.tenant_id = tenant_id
        self.timeout = timeout

    async def ask(self, service: str, ctx: str, q: AnswerPlanningQuestionInput) -> Asked:
        meta = RequestMeta(
            context_id=ctx,
            tenant_id=self.tenant_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=self.timeout),
        )
        req = AgentRequest(meta=meta, skill=SKILL, input=q.model_dump(mode="json"))
        try:
            out = await asyncio.wait_for(self._call(service, req), self.timeout)
        except TimeoutError:
            return Asked(False, reason=f"{int(self.timeout)}초 안에 응답 없음")
        except Exception as exc:  # 서비스 꺼짐·연결 실패
            return Asked(False, reason=f"호출 실패 ({type(exc).__name__})")
        if out.status != TaskStatus.COMPLETED:
            why = f"{out.status.value} {out.outcome or ''}".strip()
            if "FORBIDDEN_SKILL" in why:
                why = f"{service}에 되묻기 창구 없음"
            return Asked(False, reason=why[:200])
        try:
            parsed = AnswerPlanningQuestionOutput.model_validate(out.output or {})
        except ValidationError:
            return Asked(False, reason=f"{SKILL} 출력이 계약과 다름")
        return Asked(True, out=parsed)


def default_asker() -> Asker | None:
    """목 모드면 묻지 않는다 (None → 예전처럼 질문 없이 기획서를 쓴다)."""
    if template_config.is_mock("planning"):
        return None
    return Asker()


def missing_info_question(evidence: str, evidence_ids: list[str]) -> tuple[str, str] | None:
    """요청에 근거가 모자라면 물을 (칸, 질문). 코드 규칙이다 (LLM 아님).

    근거 ID가 없고 근거 글에 숫자(건수·비율·시각)도 없으면 근거를 묻는다.
    """
    if evidence_ids or any(ch.isdigit() for ch in evidence):
        return None
    return "evidence", "이 요청의 근거(건수·사례 ID·발생 시각)를 알려 주세요"
