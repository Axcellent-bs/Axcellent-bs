"""기획서 초안을 LLM으로 쓴다 (2026-11-02).

요청 한 건(제목·근거·보낸 서비스)을 받아 문제·목표·요구사항 문단(1~3)·완료 기준(1~3)을 쓴다.

- LLM은 글만 쓴다. 문단 수·길이 검사, 문단 id(R1…·C1…), 근거 목록, 확정 판정은 코드가 한다.
- 요청 제목·근거는 다른 부서가 쓴 외부 문장이라 데이터 블록(`untrusted`)으로만 넣는다.
- 목 모드·LLM 설정 없음·30초 초과·형식 오류면 `None` → 서비스가 예전 규칙 초안(제목 그대로
  요구사항 1, 고정 완료 기준 1)으로 쓴다. 지어낸 숫자·경로는 검사로 거르지 못하니 근거에는
  요청 근거만 남긴다(LLM 문장을 근거로 쓰지 않는다).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.llm import LLMClient, LLMSettings

SPEC_TIMEOUT_SECONDS = 30.0
MAX_PARAS = 3
MAX_LEN = 160  # 문단 한 줄 최대 글자 수

SPEC_PROMPT = """너는 고객사 사내 기획 담당 AI다.
아래 데이터의 변경 요청 한 건으로 기획서 초안을 쓴다.

규칙:
- problem: 지금 무엇이 문제인지 한 문장 (요청 근거의 사실만, 숫자를 새로 만들지 않는다)
- goal: 바뀐 뒤 무엇이 달라지는지 한 문장
- requirements: 개발자가 바로 작업할 수 있는 요구사항 1~3개. 각 한 문장, 서로 겹치지 않게
- done_criteria: 다 됐는지 확인할 수 있는 완료 기준 1~3개. 화면·동작으로 확인 가능한 문장
- 요청에 없는 기능을 덧붙이지 않는다. 결제·개인정보 같은 범위를 넓히지 않는다
- 존댓말 대신 "~한다" 문장으로, 각 문장은 160자 이하
"""


class _SpecOut(BaseModel):
    problem: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    requirements: list[str] = Field(min_length=1)
    done_criteria: list[str] = Field(min_length=1)


@dataclass
class SpecDraft:
    problem: str
    goal: str
    requirements: list[str]
    done_criteria: list[str]
    model: str | None = None


def _clean(items: list[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = " ".join(item.split())[:MAX_LEN]
        if text and text not in out:
            out.append(text)
    return out[:MAX_PARAS]


class SpecWriter:
    """LLM으로 초안을 쓴다. `llm`은 `structured()`를 가진 클라이언트 (시험에서 바꿔 끼운다)."""

    def __init__(self, llm: LLMClient, timeout: float = SPEC_TIMEOUT_SECONDS):
        self._llm = llm
        self.timeout = timeout

    async def draft(
        self, *, title: str, evidence: str, requester: str, context_id: str
    ) -> SpecDraft | None:
        data = {"title": title, "evidence": evidence, "requester": requester}
        try:
            got = await asyncio.wait_for(
                self._llm.structured(SPEC_PROMPT, _SpecOut, untrusted=data, context_id=context_id),
                self.timeout,
            )
        except (TimeoutError, ValidationError, Exception):  # noqa: BLE001 - 규칙 초안으로 대신
            return None
        reqs, done = _clean(got.requirements), _clean(got.done_criteria)
        problem = " ".join(got.problem.split())[:MAX_LEN]
        goal = " ".join(got.goal.split())[:MAX_LEN]
        if not (reqs and done and problem and goal):
            return None
        settings = getattr(self._llm, "settings", None)
        return SpecDraft(problem, goal, reqs, done, getattr(settings, "model", None))


def default_writer() -> SpecWriter | None:
    """목 모드거나 기획 LLM 설정이 없으면 None (규칙 초안)."""
    if template_config.is_mock("planning"):
        return None
    settings = LLMSettings.from_env("planning")
    if not (settings.model and settings.base_url):
        return None
    return SpecWriter(LLMClient("planning", settings=settings))
