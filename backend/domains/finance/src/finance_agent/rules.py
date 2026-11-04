"""재무·세무 규칙과 근거 (`finance_rules.toml`).

판단 결과에는 `applied(...)`로 만든 적용 규칙을 함께 낸다. 값과 근거를 바꾸려면 설정 파일만 고친다.
`FINANCE_RULES_PATH`로 다른 파일을 쓸 수 있다(시험용). 형식이 틀리면 시작할 때 실패한다.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# 전문가 확인 대상. 브레이크·교훈·인사이트 같은 내부 기준은 재무 담당이 정한다
EXPERT_AREAS = frozenset({"revenue", "expense", "settlement", "vat", "income_tax"})


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Rule(_Strict):
    id: str
    area: str
    title: str
    statement: str
    basis: str
    review: Literal["expert", "pending"]


class CampaignRules(_Strict):
    good_roi: float
    bad_roi: float

    @model_validator(mode="after")
    def _ordered(self) -> CampaignRules:
        if self.bad_roi > self.good_roi:
            raise ValueError("bad_roi는 good_roi보다 클 수 없다")
        return self


class BrakeRules(_Strict):
    budget_ratio_of_last_month: float = Field(gt=0, le=1)
    monthly_budget_krw: int = Field(ge=0)
    min_viable_krw: int = Field(ge=0)
    loss_limit_krw: int = Field(ge=0)
    loss_streak_reject: int = Field(ge=1)
    cash_lookahead_weeks: int = Field(ge=1, le=13)


class RepeatRules(_Strict):
    window_days: int = Field(ge=1)
    min_cohort_buyers: int = Field(ge=1)


class LessonRules(_Strict):
    limit_factor: float = Field(gt=0, le=1)
    floor_factor: float = Field(gt=0, le=1)


class InsightRules(_Strict):
    trend_min_campaigns: int = Field(ge=2)
    overrun_window_days: int = Field(ge=1)
    overrun_min_count: int = Field(ge=1)
    tax_deadline_warn_days: int = Field(ge=0)


class Bracket(_Strict):
    upto_krw: int | None = None
    rate: float = Field(gt=0, lt=1)


class IncomeTaxRules(_Strict):
    brackets: list[Bracket]
    local_rate_of_tax: float = Field(ge=0, lt=1)

    @model_validator(mode="after")
    def _last_open(self) -> IncomeTaxRules:
        if not self.brackets or self.brackets[-1].upto_krw is not None:
            raise ValueError("마지막 구간은 상한이 없어야 한다")
        limits = [b.upto_krw for b in self.brackets[:-1]]
        if None in limits or limits != sorted(limits):  # type: ignore[type-var]
            raise ValueError("구간 상한은 오름차순이어야 한다")
        return self


class RuleBook(_Strict):
    version: int = Field(ge=1)
    updated: str
    campaign: CampaignRules
    brake: BrakeRules
    repeat: RepeatRules
    lessons: LessonRules
    insights: InsightRules
    income_tax: IncomeTaxRules
    expert_questions: list[str] = Field(default_factory=list)
    rules: list[Rule]

    @model_validator(mode="after")
    def _unique(self) -> RuleBook:
        ids = [r.id for r in self.rules]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 규칙 id가 두 번 있다")
        return self

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.id == rule_id:
                return r
        raise KeyError(f"모르는 규칙: {rule_id}")


@lru_cache(maxsize=1)
def load_rules() -> RuleBook:
    override = os.getenv("FINANCE_RULES_PATH", "").strip()
    if override:
        with open(override, "rb") as handle:
            return RuleBook.model_validate(tomllib.load(handle))
    text = resources.files("finance_agent").joinpath("finance_rules.toml").read_text("utf-8")
    return RuleBook.model_validate(tomllib.loads(text))


def applied(rule_id: str, detail: str | None = None) -> dict[str, Any]:
    """결과에 붙일 적용 규칙 한 줄. `detail`은 이번 판단의 숫자·이유다."""
    r = load_rules().rule(rule_id)
    return {
        "rule_id": r.id,
        "title": r.title,
        "statement": r.statement,
        "basis": r.basis,
        "review": r.review,
        "detail": detail,
        "rules_version": load_rules().version,
    }


def review_checklist_md() -> str:
    """세무사·회계사에게 함께 전달할 확인 목록. 규칙 파일에서 만들어 항상 지금 규칙과 같다."""
    book = load_rules()
    lines = [
        "# 재무·세무 규칙 전문가 확인 목록",
        "",
        f"규칙 버전 {book.version} ({book.updated}). AI가 공개 자료로 적은 근거입니다.",
        "전문가 확인 전이고, 이 묶음의 숫자는 신고서가 아니라 보조 자료입니다.",
        "",
        "## 먼저 답이 필요한 질문",
        "",
        *[f"{n}. {q}" for n, q in enumerate(book.expert_questions, start=1)],
        "",
        "## 규칙별 확인",
        "",
        "| 규칙 | 내용 | 적어 둔 근거 | 확인 |",
        "|---|---|---|---|",
    ]
    for r in book.rules:
        if r.review == "pending" and r.area in EXPERT_AREAS:
            lines.append(f"| {r.id} {r.title} | {r.statement} | {r.basis} | [ ] |")
    return "\n".join(lines) + "\n"


def applied_many(*rule_ids: str) -> list[dict[str, Any]]:
    return [applied(rule_id) for rule_id in rule_ids]
