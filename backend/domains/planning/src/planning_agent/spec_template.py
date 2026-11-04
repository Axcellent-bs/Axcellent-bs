"""기획서 양식 (11/4 조사: PRD·요구사항 정의서 / 크리에이티브 브리프·마케팅 플랜).

- `plan` 기획서 (요청 → 개발): 문제 · 목표 · 범위 · 요구사항 · 완료 기준 · 고려사항
- `proposal` 기획 제안서 (기획 제안 → 마케팅·개발): 배경 · 목표 · 타깃 · 핵심 메시지 · 실행안 ·
  성공 지표 · 일정·예산 · 고려사항

칸은 기존 필드를 그대로 쓰고(problem·goal·요구 문단 R·완료 문단 C), 양식에만 있는 칸은
`Spec.extra[key]`에 둔다. 화면(frontend/src/lib/specTemplate.ts)과 이름·순서를 맞춘다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Source = Literal["problem", "goal", "req", "done", "extra"]


@dataclass(frozen=True)
class Section:
    key: str  # extra 칸이면 extra 키, 아니면 필드 이름
    label: str
    source: Source
    hint: str  # AI가 이 칸에 무엇을 쓰는지 (자동 채우기 프롬프트)


PLAN = (
    Section("problem", "문제", "problem", "지금 무엇이 문제인지, 누가 얼마나 겪는지 한두 문장"),
    Section("goal", "목표", "goal", "바뀐 뒤 무엇이 달라지는지 한 문장 (측정할 수 있으면 숫자)"),
    Section("scope", "범위", "extra", "이번에 하는 것과 하지 않는 것 (포함 · 제외)"),
    Section("req", "요구사항", "req", "개발자가 바로 작업할 수 있는 요구사항 1~3개"),
    Section("done", "완료 기준", "done", "다 됐는지 화면·동작으로 확인할 기준 1~3개"),
    Section("risks", "고려사항", "extra", "정책·제약·의존성·위험 (없으면 빈칸)"),
)

PROPOSAL = (
    Section(
        "problem", "배경", "problem", "왜 지금 이 기획인지: 데이터 인사이트·시장 상황·근거 수치"
    ),
    Section("goal", "목표", "goal", "이 캠페인으로 얻으려는 결과 한 문장"),
    Section("audience", "타깃", "extra", "누구에게 (연령대·관심사·상황)"),
    Section("message", "핵심 메시지", "extra", "타깃에게 남길 한 줄 메시지·컨셉"),
    Section("req", "실행안", "req", "채널·형식·콘텐츠 등 실행할 일 1~3개"),
    Section("done", "성공 지표", "done", "성과를 잴 KPI와 목표치 1~3개 (측정 방법 포함)"),
    Section("schedule", "일정·예산", "extra", "언제까지, 얼마로 (모르면 빈칸)"),
    Section("risks", "고려사항", "extra", "브랜드·저작권·실존 이름 주의 등 (없으면 빈칸)"),
)

TEMPLATES = {"plan": PLAN, "proposal": PROPOSAL}
EXTRA_KEYS = {s.key for t in TEMPLATES.values() for s in t if s.source == "extra"}


def template_of(origin: str | None) -> str:
    return "proposal" if origin == "proposal" else "plan"


def sections(name: str) -> tuple[Section, ...]:
    return TEMPLATES.get(name, PLAN)
