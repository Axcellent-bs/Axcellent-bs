"""data 에이전트 skill 입출력. 이슈 #36 요청(data), #33 함께 해결.

- `impact_analysis` (DEP-DATA-02): 장애 영향 숫자의 원천은 data 한 곳이다 (agent-common 8장).
  건수·금액은 SQL로 계산하고 고객은 가명 ID만 담는다.
- `anomaly_detection` (DEP-DATA-01): infra `verify_recovery`의 `business_metrics_ok` 근거.
- `fraud_score` (DEP-DATA-03): 판매자 사기 점수.
- `kpi_summary` (DSH-04 지원): 사건별 KPI 요약.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from ax_contracts.schemas.common import AxModel, UtcDatetime

# ---------- impact_analysis ----------


class ImpactAnalysisInput(AxModel):
    window_from: UtcDatetime
    window_to: UtcDatetime
    scope: Literal["payment", "order"] | None = None
    category: str | None = None


class CategoryImpact(AxModel):
    category: str
    failed_order_count: int = Field(ge=0)
    damage_amount: int = Field(ge=0)  # 정수 원


class ImpactAnalysisOutput(AxModel):
    failed_order_count: int = Field(ge=0)
    affected_customer_count: int = Field(ge=0)
    affected_customer_ids: list[str] = Field(default_factory=list)  # 가명 ID만
    damage_amount: int = Field(ge=0)  # 정수 원
    by_category: list[CategoryImpact] = Field(default_factory=list)
    summary: str

    @model_validator(mode="after")
    def _customer_count_matches_ids(self) -> Self:
        if self.affected_customer_count != len(self.affected_customer_ids):
            raise ValueError("affected_customer_count must equal len(affected_customer_ids)")
        return self


# ---------- anomaly_detection ----------

BusinessMetric = Literal["payment_fail_rate", "order_fail_count", "order_count", "inquiry_count"]


class AnomalyDetectionInput(AxModel):
    window_from: UtcDatetime
    window_to: UtcDatetime
    metrics: list[BusinessMetric] = Field(default_factory=list)  # 비면 전부


class MetricVerdict(AxModel):
    metric: BusinessMetric
    observed: float
    baseline: float
    ratio: float | None = None  # 기준값이 0이면 None
    is_anomaly: bool
    severity: Literal["low", "mid", "high"] | None = None
    # 판정 상태 (#55 이후). unknown이면 판정할 수 없었던 것이고 is_anomaly는 false다.
    # 그때 observed·baseline은 판정에 쓰지 않은 값(셀 수 있었던 값 또는 0)이다
    status: Literal["normal", "anomaly", "unknown"] | None = None
    direction: Literal["up", "down"] | None = None  # 이상이면 벗어난 방향
    note: str | None = None  # 판정 근거 한 줄 또는 확인할 수 없는 이유


class AnomalyDetectionOutput(AxModel):
    verdicts: list[MetricVerdict] = Field(default_factory=list)
    any_anomaly: bool
    summary: str
    # 구간 전체 상태 (#55 이후): 이상이 하나라도 있으면 anomaly,
    # 모든 지표를 판정할 수 없으면 unknown
    status: Literal["normal", "anomaly", "unknown"] | None = None
    unknown_metrics: list[BusinessMetric] = Field(default_factory=list)


# ---------- fraud_score ----------


class FraudScoreInput(AxModel):
    seller_id: str


class FraudReason(AxModel):
    rule_code: str
    value: float
    weight: float
    # 확인할 수 없는 규칙이면 "확인할 수 없음: <규칙> (이유)". 그때 value·weight는 0이고 점수에 없다
    note: str | None = None


class FraudScoreOutput(AxModel):
    seller_id: str
    score: int = Field(ge=0, le=100)
    level: Literal["low", "mid", "high"]
    reasons: list[FraudReason] = Field(default_factory=list)
    similar_card_ids: list[str] = Field(default_factory=list)
    summary: str
    # 데이터가 없어 확인하지 못한 규칙 코드. 점수는 확인한 규칙만으로 낸다
    unknown_rules: list[str] = Field(default_factory=list)
    # 점수가 불완전한지와 보수적 상한. data는 늘 채운다. 예전 응답(필드 없음)은 None = 알 수 없음
    # - complete: unknown_rules가 비어 있으면 true
    # - max_possible_score: score + 확인 못 한 규칙 가중치 합 (100 상한).
    #   빠진 규칙을 모두 위험으로 본 값
    # - max_possible_level: max_possible_score의 등급
    complete: bool | None = None
    max_possible_score: int | None = Field(default=None, ge=0, le=100)
    max_possible_level: Literal["low", "mid", "high"] | None = None

    @model_validator(mode="after")
    def _bounds(self) -> FraudScoreOutput:
        if self.complete is True and self.unknown_rules:
            raise ValueError("complete=true인데 unknown_rules가 있다")
        if self.complete is False and not self.unknown_rules:
            raise ValueError("complete=false인데 unknown_rules가 비어 있다")
        if self.max_possible_score is not None and self.max_possible_score < self.score:
            raise ValueError("max_possible_score는 score보다 작을 수 없다")
        return self


# ---------- kpi_summary ----------


class KpiSummaryInput(AxModel):
    period_from: UtcDatetime | None = None
    period_to: UtcDatetime | None = None
    context_ids: list[str] = Field(default_factory=list)


class IncidentKpi(AxModel):
    context_id: str
    detected_at: UtcDatetime
    recovered_at: UtcDatetime | None = None
    resolved_notice_at: UtcDatetime | None = None
    mttr_seconds: int | None = Field(default=None, ge=0)
    used_memory: bool


class HumanFeedback(AxModel):
    """운영 중 사람 반응 (공통 로그로 센다). 분모가 0이면 비율은 None(확인할 수 없음).

    - 받은 요청 반려율: 사람 할 일함에서 끝난 일 중 반려(`handoff_rejected`)
    - 승인 거절률: 사람이 결정한 C 등급 보류 중 거절(`review_rejected`)
    - 확인할 수 없음 비율: 에이전트 응답(`result_sent`) 중 `unavailable`·`service_disabled`
    """

    handoffs_closed: int = Field(default=0, ge=0)
    handoffs_rejected: int = Field(default=0, ge=0)
    handoff_reject_rate: float | None = Field(default=None, ge=0, le=1)
    reviews_decided: int = Field(default=0, ge=0)
    reviews_rejected: int = Field(default=0, ge=0)
    review_reject_rate: float | None = Field(default=None, ge=0, le=1)
    results_total: int = Field(default=0, ge=0)
    results_unknown: int = Field(default=0, ge=0)
    unknown_rate: float | None = Field(default=None, ge=0, le=1)


class KpiSummaryOutput(AxModel):
    incidents: list[IncidentKpi] = Field(default_factory=list)
    # 사건이 0건이면 셀 것이 없어 None (#40)
    auto_handled_rate: float | None = Field(default=None, ge=0, le=1)
    grade_counts: dict[str, int] = Field(default_factory=dict)
    blocked_counts: dict[str, int] = Field(default_factory=dict)
    escalation_count: int = Field(ge=0)
    # 운영 중 사람 반응 비율 (선택). 공통 로그가 하나도 없으면 None
    human_feedback: HumanFeedback | None = None


# ---------- accuracy_report ----------
#
# 정확도 평가(`domains/data/eval/run.py`) 결과를 화면에 준다. 최근 실행 결과(results/latest.json)가
# 있으면 그것을, 없으면 커밋된 기준(baseline.json)을 준다. 둘 다 없으면 source "none".


class AccuracyReportInput(AxModel):
    pass


class AccuracyMiss(AxModel):
    case_id: str
    area: str  # 이상 탐지·영향 분석·원인 추정·사기 점수·LLM 설명
    what: str


class AccuracyReportOutput(AxModel):
    source: Literal["latest", "baseline", "none"]
    generated_at: UtcDatetime | None = None
    case_count: int | None = Field(default=None, ge=0)
    metrics: dict[str, float | None] = Field(default_factory=dict)  # 값이 없으면 None
    misses: list[AccuracyMiss] = Field(default_factory=list)  # 최근 실행 결과에만 있다
    summary: str


# ---------- root_cause_hypotheses ----------
#
# 이상 구간 앞 30분 ~ 구간 끝의 사실(배포·공지·문의 급증·지난 사건)을 코드가 모으고,
# LLM은 모은 사실을 엮어 원인 후보를 고르고 설명만 한다. 숫자·시각은 사실에서만 나온다.
# 후보는 근거 사실 id가 반드시 있고, 사실 목록에 없는 id·숫자·시각을 쓴 후보는 버린다.

# anomaly: 입력으로 받은 이상 구간 자체.
# unavailable: 출처 서비스가 꺼졌거나 답하지 않아 확인할 수 없음
CauseFactKind = Literal[
    "anomaly", "deploy", "notice", "inquiry_surge", "past_incident", "unavailable"
]
CauseConfidence = Literal["high", "mid", "low"]


class RootCauseInput(AxModel):
    context_id: str  # 원인을 찾는 사건 (ctx_inc_)
    window_from: UtcDatetime  # 이상 구간 시작
    window_to: UtcDatetime  # 이상 구간 끝
    metric: BusinessMetric  # 이상이 난 지표
    component: str | None = None  # 알면 좁혀 찾는다 (예: payment)


class CauseFact(AxModel):
    fact_id: str  # 이 응답 안에서만 쓰는 id (f1, f2 …)
    kind: CauseFactKind
    at: UtcDatetime | None = None  # 사실의 시각. 확인할 수 없음이면 None
    source: str  # 어디서 받았는지 (예: planning.find_related_prs)
    ref: str | None = None  # 출처 쪽 id (pr:412, 공지 id, 카드 id)
    summary: str  # 코드가 쓴 한 줄


class CauseHypothesis(AxModel):
    explanation: str
    fact_ids: list[str] = Field(min_length=1)  # 근거 사실 id
    confidence: CauseConfidence


class RootCauseOutput(AxModel):
    hypotheses: list[CauseHypothesis] = Field(default_factory=list)  # 확신도 높은 순
    needs_confirmation: bool  # 확신도 high 후보가 없으면 true (사람이 원인을 확인)
    facts: list[CauseFact] = Field(default_factory=list)
    summary: str  # 사실이 하나도 없으면 "확인할 수 없음 …"

    @model_validator(mode="after")
    def _hypotheses_cite_facts(self) -> Self:
        known = {f.fact_id for f in self.facts}
        for hypothesis in self.hypotheses:
            missing = set(hypothesis.fact_ids) - known
            if missing:
                raise ValueError(f"hypothesis cites unknown facts: {sorted(missing)}")
        if not any(h.confidence == "high" for h in self.hypotheses) and not self.needs_confirmation:
            raise ValueError("needs_confirmation must be true without a high-confidence hypothesis")
        return self
