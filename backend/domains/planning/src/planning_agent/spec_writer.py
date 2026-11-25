"""기획서 초안을 LLM으로 쓴다 (2026-11-02).

요청 한 건(제목·근거·보낸 서비스)을 받아 문제·목표·요구사항 문단(1~3)·완료 기준(1~3)을 쓴다.

- LLM은 글만 쓴다. 문단 수·길이 검사, 문단 id(R1…·C1…), 근거 목록, 확정 판정은 코드가 한다.
- 요청 제목·근거는 다른 부서가 쓴 외부 문장이라 데이터 블록(`untrusted`)으로만 넣는다.
- 목 모드·LLM 설정 없음·30초 초과·형식 오류면 `None` → 서비스가 예전 규칙 초안(제목 그대로
  요구사항 1, 고정 완료 기준 1)으로 쓴다. 지어낸 숫자·경로는 검사로 거르지 못하니 근거에는
  요청 근거만 남긴다(LLM 문장을 근거로 쓰지 않는다).
- 근거의 관찰 값은 요구사항·완료 기준이 아니다 (11/25 SPEC-30 R2: 원인 후보 설명의 "공지 게시
  시각·채널"이 화면 표시 요구로 들어가 개발이 엉뚱한 작업을 했다). 지시문에 적고, 근거에만 있는
  시각(ISO 날짜·시각)을 그대로 옮긴 문장은 코드가 뺀다. 다 빠지면 규칙 초안으로 돌아간다.
"""

from __future__ import annotations

import asyncio
import re
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
- evidence(근거)는 왜 이 요청이 왔는지 보여 주는 배경이다. 근거 속 원인 후보 설명·관찰 값(시각·
  채널·건수·비슷한 과거 사례)을 요구사항·완료 기준으로 옮기지 않는다. 그런 값은 problem에만 짧게
  쓴다. 요구사항은 title이 바라는 변경만 다룬다
- 개발은 이 회사의 사이트·서비스 코드를 고친다. 장애 원인 확인 요청이면 요구사항은 그 장애가 난
  회사 서비스(예: 결제)의 원인을 찾아 고치는 일이다. AXcellent 쪽 화면(장애 상세 화면, 원인 후보·
  신뢰도 표시, 운영자 원인 확정 입력)은 회사 코드가 아니라 요구사항으로 쓰지 않는다
- 요구사항 하나는 개발 작업 하나가 된다. 그래서 만들 것이 있는 변경만 요구사항에 쓴다. "다른 파일은
  수정하지 않는다", "기존 내용은 그대로 둔다" 같은 제약·금지는 요구사항이 아니라 완료 기준에 쓴다
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


# 근거에서 옮겨 온 관찰 시각 (예: 2026-11-13T07:01:44Z, 2026-11-13 07:01)
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:\.\d+)?Z?")


def _from_evidence(item: str, title: str, evidence: str) -> bool:
    """근거에만 있는 시각을 그대로 담은 문장이면 True.
    제목에 그 시각이 있으면 요청한 것이라 둔다."""
    return any(m in evidence and m not in title for m in _STAMP.findall(item))


# 만들 것이 없는 제약·금지 문장 끝 (11/25 SPEC-26 R1 "다른 파일은 수정하지 않는다" → 작업 T-28)
_CONSTRAINT_END = re.compile(
    r"(하지 않는다|않는다|바꾸지 않는다|건드리지 않는다|그대로 둔다|그대로 유지한다|제외한다)\.?$"
)


# AXcellent 쪽 화면을 고치라는 문장 (11/25 SPEC-30 R1·R3: 회사 레포에는 이 화면이 없어 개발이 손님
# 결제 실패 화면에 붙였다). 회사 사이트 코드가 아니라 요구사항·완료 기준에서 뺀다
_AX_SCREEN = re.compile(r"장애 상세 화면|원인 후보|운영자가 (직접 )?원인을")


def about_ax_screen(item: str) -> bool:
    return bool(_AX_SCREEN.search(item))


def is_constraint(item: str) -> bool:
    """요구사항 문장이 만들 것 없는 제약·금지면 True (완료 기준으로 옮긴다)."""
    return bool(_CONSTRAINT_END.search(" ".join(item.split())))


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

        def ok(x: str) -> bool:
            return not _from_evidence(x, title, evidence) and not about_ax_screen(x)

        keep = [x for x in got.requirements if ok(x)]
        done_keep = [x for x in got.done_criteria if ok(x)]
        # 제약·금지 요구사항은 작업이 되지 않게 완료 기준 쪽으로 (만들 것이 하나도 없으면 그대로)
        rules = [x for x in keep if is_constraint(x)]
        if rules and len(rules) < len(keep):
            keep = [x for x in keep if x not in rules]
            done_keep = [*done_keep, *rules]
        reqs, done = _clean(keep), _clean(done_keep)
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
