"""data 에이전트 skill 입출력. 이슈 #36 요청(data), #33 함께 해결.

- `impact_analysis` (DEP-DATA-02): 장애 영향 숫자의 원천은 data 한 곳이다 (agent-common 8장).
  건수·금액은 SQL로 계산하고 고객은 가명 ID만 담는다.
- `anomaly_detection` (DEP-DATA-01): infra `verify_recovery`의 `business_metrics_ok` 근거.
- `fraud_score` (DEP-DATA-03): 판매자 사기 점수.
- `kpi_summary` (DSH-04 지원): 사건별 KPI 요약.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime
from ax_contracts.schemas.planning import BrandExposure, PromoStyle, ReceiveInsightInput

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
    damage_amount: int = Field(ge=0)  # 정수 원 (결제 실패 주문 금액 합, 이름은 호환을 위해 그대로)
    # 결제 실패(미결제) 금액 (11/13). 고객 돈이 나가지 않은 받지 못한 매출이다. 지금은
    # `damage_amount`와 같은 값이고, 실제 손실·환불할 돈이 아니라는 뜻을 이름으로 드러낸다.
    # 예전 응답에는 없다
    unpaid_amount: int | None = Field(default=None, ge=0)
    by_category: list[CategoryImpact] = Field(default_factory=list)
    summary: str
    # 평소보다 더 실패한 건수: 구간 장애 쪽 실패 − 지난 7일 같은 시각 ±1시간의 평소 실패
    # (구간 길이만큼, 반올림, 0 미만이면 0). 지난 7일 자료가 없으면 None. 예전 응답에는 없다
    excess_failed_order_count: int | None = Field(default=None, ge=0)
    # 데이터분석이 이 요청을 처리하기 시작한 시각. 부른 쪽이 대기(요청~시작)와
    # 처리(시작~응답) 시간을 나눠 남기는 데 쓴다(재무 영향 분석 로그). 예전 응답에는 없다
    started_at: UtcDatetime | None = None
    # 장애 구간에 시간 초과로 남은 결제(확인·환불 필요). 실패 주문·미결제와 따로 센다.
    # 모르면 None. 예전 응답에는 없다
    timeout_count: int | None = Field(default=None, ge=0)
    timeout_amount: int | None = Field(default=None, ge=0)

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
    # 판정 방식 (2026-11-11 추가). `window`(기본): 5분 칸마다 지난 7일 같은 시간대와 비교.
    # `recovery`: 복구 확인용. window_from(조치가 끝난 시각)부터 window_to까지를 한 구간으로 세어
    # 결제가 충분하고 장애 쪽 실패가 평소 수준이면 normal, 결제가 모자라면 unknown이다
    mode: Literal["window", "recovery"] = "window"


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
    # 회사 지표 카탈로그의 지표 id·한국어 이름 (캣북스는 metric과 같은 id). 예전 응답은 None
    metric_id: str | None = None
    metric_label: str | None = None


class MetricIdVerdict(AxModel):
    """회사 지표 카탈로그 id로 쓴 판정 (캣북스 4개 밖의 지표도 실을 수 있다).

    칸의 뜻은 `MetricVerdict`와 같고, 지표를 `BusinessMetric` 대신 `metric_id`(카탈로그
    `CatalogMetric.id`)로 적는다.
    """

    metric_id: str
    metric_label: str | None = None  # 카탈로그 한국어 이름
    observed: float
    baseline: float
    ratio: float | None = None
    is_anomaly: bool
    severity: Literal["low", "mid", "high"] | None = None
    status: Literal["normal", "anomaly", "unknown"] | None = None
    direction: Literal["up", "down"] | None = None
    note: str | None = None


class AnomalyDetectionOutput(AxModel):
    """이상 탐지 결과.

    지표는 두 목록으로 싣는다. `verdicts`·`unknown_metrics`는 캣북스 지표 4개(`BusinessMetric`)만
    담는 예전 목록이고, `metric_verdicts`·`unknown_metric_ids`는 회사 카탈로그 id로 모든 지표를
    담는다. 캣북스 지표는 두 목록에 다 들어가고(예전 소비자 호환), 다른 회사 지표는 새 목록에만
    들어간다.
    """

    verdicts: list[MetricVerdict] = Field(default_factory=list)
    any_anomaly: bool
    summary: str
    # 구간 전체 상태 (#55 이후): 이상이 하나라도 있으면 anomaly,
    # 모든 지표를 판정할 수 없으면 unknown
    status: Literal["normal", "anomaly", "unknown"] | None = None
    unknown_metrics: list[BusinessMetric] = Field(default_factory=list)
    metric_verdicts: list[MetricIdVerdict] = Field(default_factory=list)  # 모든 지표 (카탈로그 id)
    unknown_metric_ids: list[str] = Field(default_factory=list)  # 판정할 수 없었던 지표 id


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
    # 후보를 고른 방법: `llm`(LLM이 사실로 고름) · `rule`(시간 순서 규칙. 규칙만으로는 high를 주지
    # 않는다) · 후보가 없으면 None. 예전 결과는 없다
    method: Literal["llm", "rule"] | None = None

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


# ------------------------------------------------ activity_report (부서별 AI 업무 리포트)
# 숫자는 전부 data 코드가 센다. 공통 로그는 읽기만 한다.
# 사람이 직접 한 일(로그에 없는 일)은 세지 않는다.
# 비율은 분모가 3 미만이면 `rate`가 None이고 `note`가 "표본 부족"이다.
ActivityPeriod = Literal["day", "week", "month"]


class ActivityReportInput(AxModel):
    period: ActivityPeriod = "day"
    start: dt.date | None = None  # 한국 시간 날짜. 비우면 직전 기간(어제·지난주 월요일·지난달)
    services: list[str] = Field(default_factory=list)  # 서비스 id. 비우면 전체


class ActivityRate(AxModel):
    count: int = Field(ge=0)
    total: int = Field(ge=0)  # 분모
    rate: float | None = Field(default=None, ge=0, le=1)  # 분모 3 미만이면 None
    note: str | None = None  # "표본 부족" 등


class CompanyFigure(AxModel):
    """회사 숫자 하나. 값을 받지 못하면 None이고 이유는 `note`와 `unknown_sections`에 있다."""

    value: float | None = None
    previous_period: float | None = None
    source: str  # 어디서 받았는지 (예: finance.closing_report)
    note: str | None = None  # 기준 차이 등 (예: 재무 마감은 UTC 날짜 기준)
    # 이 값의 기준 시각 (재무 응답 시각 등). 예전 응답·모르는 값은 None
    collected_at: UtcDatetime | None = None


class ActivityCompany(AxModel):
    gmv: CompanyFigure  # 총거래액 (원)
    commission_revenue: CompanyFigure  # 판매 수수료 매출 (원)
    order_count: CompanyFigure  # 주문 수
    payment_success_rate: CompanyFigure  # 결제 성공률 (0~1)
    incident_count: CompanyFigure  # 사건 수


class SkillCount(AxModel):
    skill: str
    label: str  # 한국어 표시명
    count: int = Field(ge=0)


class ActivityRequests(AxModel):
    total: int = Field(ge=0)  # 이 부서 AI가 응답한 요청 수 (`result_sent`)
    top_skills: list[SkillCount] = Field(default_factory=list)  # 상위 5개


class ActivityOutcomes(AxModel):
    """요청 결과 6가지. 분모는 모두 `requests.total`이다."""

    auto_completed: ActivityRate  # 자동 완료
    human_approved: ActivityRate  # 사람 확인 후 실행 (C 보류 → 승인)
    held: ActivityRate  # 보류 (사람 판단·입력 대기, 승인 대기)
    rejected: ActivityRate  # 거절 (요청 거부·안전 관문 거부·승인 거절)
    unknown: ActivityRate  # 확인할 수 없음 (unavailable·service_disabled·교차검증 결과 없음)
    failed: ActivityRate  # 실패


class ActivityHandoffs(AxModel):
    """이 부서 일이 서비스가 꺼져 사람 할 일함으로 간 것."""

    handed: int = Field(ge=0)  # 넘긴 건수
    done: int = Field(ge=0)  # 사람이 처리
    rejected: int = Field(ge=0)  # 사람이 반려
    avg_close_minutes: float | None = Field(default=None, ge=0)  # 넘김 → 끝남 평균 (분)


class ActivityQuality(AxModel):
    fallback: ActivityRate  # 설명 문장 템플릿 대체 / 완료 응답
    fallback_reasons: dict[str, int] = Field(default_factory=dict)
    avg_response_ms: float | None = Field(default=None, ge=0)  # 받음 → 보냄 평균
    response_samples: int = Field(default=0, ge=0)


class ActivityIncidents(AxModel):
    count: int = Field(ge=0)  # 참여한 사건 수
    skills: list[SkillCount] = Field(default_factory=list)  # 사건에서 맡은 skill
    mttr_median_minutes: float | None = Field(default=None, ge=0)  # 그 사건들 MTTR 중앙값


class DepartmentActivity(AxModel):
    requests: ActivityRequests
    outcomes: ActivityOutcomes
    handoffs: ActivityHandoffs
    quality: ActivityQuality
    incidents: ActivityIncidents


class DepartmentReport(DepartmentActivity):
    department: str  # 부서 id (services.py department)
    label: str  # 한국어 표시명
    owners: list[str] = Field(default_factory=list)  # 서비스 카드 owner
    services: list[str] = Field(default_factory=list)
    enabled: bool  # 이 부서 서비스가 하나라도 켜져 있는지
    previous_period: DepartmentActivity


class CollaborationEdge(AxModel):
    from_department: str
    from_label: str
    to_department: str
    to_label: str
    count: int = Field(ge=1)  # A2A 요청 수 (`request_received`)


class UnusedHandoff(AxModel):
    """AI를 쓰지 않는(켜지 않은) 부서로 넘긴 일. 건수와 업무 상위 3개만 (예측 문구·숫자 없음)."""

    department: str
    label: str
    count: int = Field(ge=1)
    top_skills: list[SkillCount] = Field(default_factory=list, max_length=3)


class ActivityHighlight(AxModel):
    key: str  # 예: departments.support.requests.total
    label: str  # 한국어 (예: 고객지원 처리한 요청)
    current: float
    previous: float  # 3 이상만 고른다
    change_percent: float  # (current − previous) / previous × 100, 소수 1자리


class UnknownSection(AxModel):
    section: str
    reason: str


class ActivityReportOutput(AxModel):
    period: ActivityPeriod
    start: dt.date  # 한국 시간 날짜 (포함)
    end: dt.date  # 한국 시간 날짜 (제외)
    window_from: UtcDatetime
    window_to: UtcDatetime
    previous_from: UtcDatetime
    previous_to: UtcDatetime
    company: ActivityCompany
    departments: list[DepartmentReport] = Field(default_factory=list)
    collaboration: list[CollaborationEdge] = Field(default_factory=list)
    highlights: list[ActivityHighlight] = Field(default_factory=list, max_length=3)
    summary: str
    summary_source: Literal["llm", "template", "none"]  # none: 특이사항 없음(LLM 호출 안 함)
    unknown_sections: list[UnknownSection] = Field(default_factory=list)
    # 일간 저장(data DATA_REPORT_DB)을 쓴 값인지. 예전 응답은 None
    # 언제 만든 값인지 (쓴 저장값 중 가장 이른 것, 저장값이 없으면 계산 시각)
    snapshot_at: UtcDatetime | None = None
    stored_days: int | None = Field(default=None, ge=0)  # 저장된 값을 쓴 날 수 (이번 + 직전 기간)
    live_days: int | None = Field(default=None, ge=0)  # 바로 계산한 날 수 (이번 + 직전 기간)
    snapshot_note: str | None = None  # "저장된 날 N일 / 바로 계산 M일"
    # 센 범위: 켠(AI를 쓰는) 서비스 id. 저장된 날은 그날 켠 서비스. 예전 응답은 None
    enabled_services: list[str] | None = None
    unused_handoffs: list[UnusedHandoff] = Field(
        default_factory=list
    )  # AI를 쓰지 않는 부서로 넘긴 일


# --------------------------------------------------------------- 지표 카탈로그 (회사별 설정 형식)
# data 엔진이 회사마다 다른 것(지표 이름·나쁜 방향·장애 쪽 코드·단위·카테고리 이름)을 이 형식의
# 파일에서 읽는다. 캣북스는 data `catalogs/catbooks.toml`. 값은 회사 설정이고 숫자를 만들지 않는다.
class CatalogMetric(AxModel):
    id: str  # 커넥터가 주는 지표 이름 (예: payment_fail_rate)
    label: str  # 한국어 표시명 (설명 문장·리포트)
    bad_direction: Literal["up", "down"]  # 어느 쪽으로 움직이면 이상인지
    unit: str  # 단위 (예: 건, 비율)
    # 장애 쪽 실패 코드 (있으면 확률 기준도 본다)
    fault_codes: list[str] = Field(default_factory=list)
    aggregate: Literal["sum", "mean"] = "sum"  # 5분 구간으로 묶는 방법
    denominator: str | None = None  # 비율 지표의 분모 지표 (예: 결제 시도 수)
    denominator_label: str | None = None  # 분모를 부르는 말 (예: 결제). 판정 안내 문장에 쓴다
    scale_with_volume: bool = False  # 거래량이 평소보다 많으면 기준을 그만큼 늘려 볼지


class CatalogImpact(AxModel):
    unit_label: str  # 영향의 단위 (예: 주문, 출고)
    customer_label: str  # 영향받은 사람 (예: 고객, 거래처)
    money_field: str  # 피해 금액으로 더하는 칸
    money_unit: str = "원"
    # 영향 금액을 부르는 말 (11/13). 캣북스는 "결제 실패(미결제) 금액"(받지 못한 매출, 실제 손실이
    # 아님). 없으면 예전 문구 "피해 금액"
    money_label: str = "피해 금액"


class AttributeDef(AxModel):
    """고객 속성 하나 (예: 연령대). 어떤 속성이 있는지는 회사 설정이 정한다(코드에 쓰지 않는다)."""

    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1)
    values: dict[str, str] = Field(min_length=1)  # 값 code → 화면 이름 (순서 = 화면 순서)


class ExternalFilter(AxModel):
    """속성 값 하나를 바깥 출처 조건으로 바꾸는 표 (예: 20대 → 정보나루 from_age 20·to_age 29)."""

    source_id: str = Field(min_length=1)  # 커넥터 이름 (data4library·naver_shopping 등)
    attribute_key: str
    value_code: str
    params: dict[str, Any] = Field(default_factory=dict)


class GenreExternal(AxModel):
    """장르 하나를 바깥 출처 조건으로 바꾸는 표 (예: 소설 → 정보나루 kdc 8)."""

    source_id: str = Field(min_length=1)
    genre_code: str
    params: dict[str, Any] = Field(default_factory=dict)


class PopulationSource(AxModel):
    """시장 커버 비율에 쓰는 인구 통계표. 여기 적힌 속성 조합만 계산한다."""

    attribute_keys: list[str] = Field(min_length=1)
    source_id: str = Field(min_length=1)  # 예: kosis
    # 표 위치와 값 대응 (예: org_id·tbl_id·itm_id, 속성 값 → 표의 분류 코드). 모양은 커넥터가 안다
    table_ref: dict[str, Any] = Field(default_factory=dict)
    base_year: int | None = None


class EventKind(AxModel):
    """이벤트 종류 (예: 쿠폰). 전환율은 지난 캠페인이 없을 때 쓰는 가정값(0~1)."""

    code: str = Field(min_length=1)
    label: str = Field(min_length=1)
    discount_rate: float = Field(ge=0, le=1)
    conversion_low: float = Field(ge=0, le=1)
    conversion_high: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _low_le_high(self) -> Self:
        if self.conversion_low > self.conversion_high:
            raise ValueError(f"event_kinds {self.code}: conversion_low가 conversion_high보다 크다")
        return self


class QuickQuestion(AxModel):
    """화면의 자주 묻는 질문 칩: 이름 + 속성 조건."""

    label: str = Field(min_length=1)
    filters: dict[str, str] = Field(min_length=1)  # 속성 key → 값 code


class CatalogSales(AxModel):
    """매출 분석(`sales_analysis`) 회사 설정 (카탈로그 `[sales]` 구역).

    다른 회사는 같은 모양의 toml로 바꿔 끼운다. 값은 이름·기준이고 숫자를 만들지 않는다.
    고객·시장 분석(11/16)의 칸은 모두 기본값이 있는 선택 칸이다. 이름(장르·속성·이벤트 종류)은
    여기에만 두고 분석·화면 코드에 쓰지 않는다.
    """

    version: str = "1"  # 이 설정의 판 (근거에 남긴다)
    # 매출 정의 한 줄 (화면 위쪽에 그대로 보인다)
    revenue_definition: str = (
        "매출 = 결제 완료 주문의 상품 금액 − 쿠폰 할인 − 포인트 사용 − 기간 안 환불 (배송비 제외)"
    )
    categories: dict[str, str] = Field(default_factory=dict)  # category_code → 화면 이름
    # 사이트 변환기로 연결한 회사(11/26): 사이트 분류 값 → 화면 이름.
    # `categories`와 달리 닫힌 목록이 아니다 — 여기 없는 사이트 값은 그대로 보인다(#1841).
    # `categories`가 있으면 쓰지 않는다
    category_names: dict[str, str] = Field(default_factory=dict)
    source_labels: dict[str, str] = Field(default_factory=dict)  # 유입 src_source → 화면 이름
    medium_labels: dict[str, str] = Field(default_factory=dict)  # 유입 src_medium → 화면 이름
    min_product_orders: int = Field(default=5, ge=1)  # 상품 증감 비교의 최소 표본 (주문 수)
    max_staleness_minutes: int = Field(default=30, ge=1)  # 마지막 수집이 이보다 오래되면 숫자 안 냄
    # 기간 안 결제 완료 주문이 이보다 적으면 숫자 안 냄
    min_period_orders: int = Field(default=20, ge=1)
    # ---- 고객·시장 분석 (11/16, 모두 선택)
    genres: dict[str, str] = Field(default_factory=dict)  # genre_code → 화면 이름
    # genre_code → 시즌 판단용 대표 검색어 (작년 같은 달 검색 배수)
    genre_keywords: dict[str, list[str]] = Field(default_factory=dict)
    attributes: list[AttributeDef] = Field(default_factory=list)
    external_filters: list[ExternalFilter] = Field(default_factory=list)
    genre_external: list[GenreExternal] = Field(default_factory=list)
    population: list[PopulationSource] = Field(default_factory=list)
    event_kinds: list[EventKind] = Field(default_factory=list)
    quick_questions: list[QuickQuestion] = Field(default_factory=list)
    min_group_size: int = Field(default=5, ge=1)  # 한 묶음이 이보다 적으면 숫자를 숨긴다
    active_days: int = Field(default=365, ge=1)  # 대상 고객 = 최근 N일 결제 완료 주문 1회 이상
    # ---- 11/16 시안 맞춤 (모두 선택)
    item_noun: str = Field(default="상품", min_length=1)  # 화면에 쓰는 품목 이름 (예: 책·게임)
    # 이벤트 후보 거르기: 작년 같은 달 검색 배수가 이보다 낮거나 예상 주문(낮은 쪽)이 이보다
    # 적으면 제안에서 빼고 excluded에 이유와 함께 둔다
    min_season_multiple: float = Field(default=1.0, ge=0)
    min_orders_low: int = Field(default=5, ge=0)
    # ---- 데이터 분석 확장 (11/17, 모두 선택)
    # 가정: 단골이 이 날 수 넘게 안 사면 떠날 위험으로 본다 (retention at_risk)
    churn_days: int = Field(default=60, ge=1)
    repeat_min_orders: int = Field(default=2, ge=2)  # 단골 기준: 결제 완료 주문 이 수 이상
    value_horizon_days: int = Field(default=90, ge=1)  # 경로별 고객 가치를 보는 기간 (첫 구매부터)

    @model_validator(mode="after")
    def _references_exist(self) -> Self:
        """key·code 중복 금지, 없는 속성·값·장르를 가리키지 않기."""
        keys = [a.key for a in self.attributes]
        if len(keys) != len(set(keys)):
            raise ValueError("attributes: key가 겹친다")
        values = {a.key: a.values for a in self.attributes}
        codes = [e.code for e in self.event_kinds]
        if len(codes) != len(set(codes)):
            raise ValueError("event_kinds: code가 겹친다")

        def check_value(where: str, key: str, code: str) -> None:
            if key not in values:
                raise ValueError(f"{where}: 없는 속성 {key}")
            if code not in values[key]:
                raise ValueError(f"{where}: 속성 {key}에 없는 값 {code}")

        for genre in self.genre_keywords:
            if genre not in self.genres:
                raise ValueError(f"genre_keywords: 없는 장르 {genre}")
        for f in self.external_filters:
            check_value("external_filters", f.attribute_key, f.value_code)
        for g in self.genre_external:
            if g.genre_code not in self.genres:
                raise ValueError(f"genre_external: 없는 장르 {g.genre_code}")
        combos = [tuple(p.attribute_keys) for p in self.population]
        if len(combos) != len(set(combos)):
            raise ValueError("population: 같은 속성 조합이 겹친다")
        for p in self.population:
            if len(set(p.attribute_keys)) != len(p.attribute_keys):
                raise ValueError("population: 한 조합 안에 같은 속성이 겹친다")
            for key in p.attribute_keys:
                if key not in values:
                    raise ValueError(f"population: 없는 속성 {key}")
        labels = [q.label for q in self.quick_questions]
        if len(labels) != len(set(labels)):
            raise ValueError("quick_questions: label이 겹친다")
        for q in self.quick_questions:
            for key, code in q.filters.items():
                check_value("quick_questions", key, code)
        return self


class MetricCatalog(AxModel):
    id: str
    name: str  # 회사 표시명
    metrics: list[CatalogMetric] = Field(min_length=1)  # 감시하는 지표 (순서대로 판정)
    impact: CatalogImpact
    categories: dict[str, str] = Field(default_factory=dict)  # 카테고리 코드 → 한국어 이름
    # 사용자 쪽 실패 코드 → 한국어 이름 (장애와 무관해 영향에서 뺀다. 예: 카드 거절)
    user_codes: dict[str, str] = Field(default_factory=dict)
    volume_metric: str | None = None  # 거래량 지표 (빈 구간 판단·기준 보정). 없으면 쓰지 않는다
    volume_label: str | None = None  # 거래량을 부르는 말 (예: 주문·결제). 빈 구간 안내에 쓴다
    # 매출 지표 (campaign_effect 매출 증분). 커넥터가 주는 지표 이름. 없으면 매출은 확인할 수 없음
    revenue_metric: str | None = None
    # 지표·실패 이벤트·거래량을 읽는 커넥터 이름 (data `clients.shop_client.CONNECTORS`)
    connector: str = "shop-api"
    # 매출 분석 설정 (`[sales]`). 없으면 매출 분석은 "설정 없음"으로 숫자를 내지 않는다
    sales: CatalogSales | None = None

    @model_validator(mode="after")
    def _unique_metric_ids(self) -> Self:
        ids = [m.id for m in self.metrics]
        if len(ids) != len(set(ids)):
            raise ValueError("metric id가 겹친다")
        return self


# --------------------------------------------------------------- trend_scan (검색어 급상승)
# 외부 검색 트렌드(네이버 검색어 트렌드)에서 키워드 그룹별 급상승을 판정한다.
# 네이버 값은 검색 건수가 아니라 "그 요청 안의 최댓값 = 100"인 상대값이고, 요청이 다르면 배수가
# 달라진다. 그래서 기준선과 판정 구간은 한 요청으로 받은 값끼리만 비교하고, 출력도 건수가 아니라
# 기준선 대비 배수로 낸다.

TrendTimeUnit = Literal["date", "week", "month"]  # 네이버가 받는 단위 (가장 작은 단위 = 하루)
TrendStatus = Literal["rising", "normal", "unknown"]


class TrendKeywordGroup(AxModel):
    group_name: str = Field(min_length=1)  # 결과에서 이 그룹을 부르는 이름
    keywords: list[str] = Field(min_length=1, max_length=20)  # 같이 셀 검색어 (네이버 최대 20개)


class TrendScanInput(AxModel):
    keyword_groups: list[TrendKeywordGroup] = Field(min_length=1, max_length=5)  # 네이버 최대 5개
    start_date: dt.date  # 기준선 시작 (한국 날짜)
    end_date: dt.date  # 판정할 마지막 날 (한국 날짜, 네이버는 하루 늦게 채워진다)
    time_unit: TrendTimeUnit = "date"
    # 선택: 네이버 검색어 트렌드 연령·성별 필터. 없으면 전체(지금과 같음). ages는 네이버 연령 코드
    # 그대로 "1"~"11" (3=19~24세, 4=25~29, 5=30~34, 6=35~39). 사람 나이("20")를 넣지 않는다
    ages: list[str] | None = None
    gender: Literal["f", "m"] | None = None

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date가 start_date보다 앞이다")
        names = [g.group_name for g in self.keyword_groups]
        if len(names) != len(set(names)):
            raise ValueError("group_name이 겹친다")
        codes = {str(n) for n in range(1, 12)}
        if self.ages is not None and any(a not in codes for a in self.ages):
            raise ValueError("ages는 네이버 연령 코드 1~11만")
        return self


class TrendEvidence(AxModel):
    """판정 근거 숫자. 모두 같은 요청 안의 상대값(최댓값 100)이다."""

    latest_period: dt.date | None = None  # 판정한 마지막 시점
    latest_value: float | None = Field(default=None, ge=0)  # 그 시점 값
    baseline_value: float | None = Field(default=None, ge=0)  # 기준선 (판정 구간 전 값의 중앙값)
    baseline_points: int = Field(default=0, ge=0)  # 기준선에 쓴 시점 수
    spread: float | None = Field(default=None, ge=0)  # 평소 흔들림 (log 값의 중앙 절대 편차)
    score: float | None = None  # 평소 흔들림의 몇 배만큼 벗어났는지 (강건 z)
    # 평소 범위(화면의 띠): 기준선 log 중앙값 ± σ(= 1.4826 × MAD, 하한 적용)를 되돌린 값.
    # 예전 응답은 없다
    band_low: float | None = Field(default=None, ge=0)
    band_high: float | None = Field(default=None, ge=0)
    # 11/26 추가: z가 가장 큰 구간(정점)의 시작·끝 날짜와 끝난 급상승 여부. status는 그대로
    # rising이고 faded면 결론·핵심 숫자·인사이트 근거에 쓰지 않는다 (정점 끝이 마지막 값 날보다
    # 설정 일수(기본 2일) 넘게 앞이고 마지막 값이 band_high 이하). 예전 응답은 없다
    peak_start: dt.date | None = None
    peak_end: dt.date | None = None
    faded: bool = False


class TrendPoint(AxModel):
    """날짜별 값 하나 (같은 요청 안 상대값, 최댓값 100). 네이버가 주지 않은 날은 빠진다(0 아님)."""

    day: dt.date
    value: float = Field(ge=0)


class TrendVerdict(AxModel):
    group_name: str
    status: TrendStatus
    ratio: float | None = Field(default=None, ge=0)  # 기준선 대비 배수 (판정 못 하면 None)
    evidence: TrendEvidence = Field(default_factory=TrendEvidence)
    unknown_reason: str | None = None  # status가 unknown이면 이유 (예: 자료 없음, 기준선 0)
    # 아래는 화면(그래프·설명)용으로 추가. 예전 응답에는 없다
    series: list[TrendPoint] = Field(default_factory=list)  # 이 그룹이 받은 날짜별 값 (기간 전체)
    window_from: dt.date | None = None  # 판정 구간 시작일 (그 앞은 기준선)
    summary: str | None = None  # 이 그룹 설명 한두 문장 (급상승만, 숫자는 근거에서 검증)
    # 아래는 예전 응답은 없다. 공유 주제 분류기 결과 (판정과 별개, 분류 못 하면 unknown + 이유)
    topic_category: TopicCategory | None = None
    topic_category_reason: str | None = None
    real_entity: bool | None = None  # 실존 인물·팀·리그·상표가 든 주제
    entity_names: list[str] = Field(default_factory=list)
    ad_material: bool | None = None  # 광고 재료로 쓸 수 있는지 (분류 못 하면 None)
    ad_material_reason: str | None = None

    @model_validator(mode="after")
    def _reason_when_unknown(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status != "unknown" and self.ratio is None:
            raise ValueError("판정했으면 ratio가 있어야 한다")
        return self


class TrendScanOutput(AxModel):
    start_date: dt.date
    end_date: dt.date
    time_unit: TrendTimeUnit
    source: str  # 자료 출처 (예: naver_datalab)
    value_kind: Literal["relative"] = "relative"  # 검색 건수가 아니라 요청 안 상대값
    results: list[TrendVerdict] = Field(default_factory=list)  # 입력 그룹 순서


class TrendScanRequest(AxModel):
    """trend_scan skill 입력. 비우면 회사 기본 키워드 그룹·어제 날짜로 본다.

    그룹마다 따로 요청한다(큰 키워드에 작은 키워드가 눌리지 않게). 기간은 정의로 정해진다:
    판정 구간 = `end_date`까지 7일, 기준선 = 그 앞 56일.
    """

    # 없으면 회사 설정(config/tenants/<회사>/trends.toml)의 기본 그룹. 그룹마다 네이버 호출 1번
    keyword_groups: list[TrendKeywordGroup] | None = Field(
        default=None, min_length=1, max_length=20
    )
    end_date: dt.date | None = None  # 판정할 마지막 날 (한국 날짜). 없으면 어제

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [g.group_name for g in self.keyword_groups or []]
        if len(names) != len(set(names)):
            raise ValueError("group_name이 겹친다")
        return self


# --------------------------------------------------------------- trend_candidates (YouTube 후보)
# YouTube 인기 영상 스냅샷의 태그에서 후보 키워드를 코드 규칙으로 뽑고, 후보마다 trend_scan과 같은
# 판정 함수로 네이버 급상승 여부를 붙인다. 스냅샷이 없거나 오래되면 숫자 없이 unknown + 이유다.


class TrendCandidatesRequest(AxModel):
    """trend_candidates skill 입력.

    비우면 회사 설정(trends.toml `[candidates]`)과 어제 날짜로 본다.
    """

    # 후보 수 상한. 없으면 회사 설정. 후보마다 네이버 호출 1번
    max_candidates: int | None = Field(default=None, ge=1, le=20)
    end_date: dt.date | None = None  # 네이버 판정할 마지막 날 (한국 날짜). 없으면 어제


# 회사와의 연결: direct = 연결 상품 있음, domain = 도메인 프로필·회사 키워드, none = 둘 다 아님,
# format = 값 추가 (형식 빌리기: 상품 직접 연결은 없지만 광고 재료 가능하고, 그 주제의 잘 되는 숏츠
# 형식이 있으며, 예시 아이디어에서 회사 상품과 이어진 유행. 참고용이 아님)
CompanyRelevance = Literal["direct", "domain", "none", "format"]


# 영상을 광고·홍보 재료로 쓸 때 확인할 것 (v2 화면 칸, 코드가 분류 결과로 정함):
# real_person = 실존 인물(초상권), trademark = 팀·리그·상표·작품 이름(상표권), music = 음원(저작권)
CautionType = Literal["real_person", "trademark", "music"]


class VideoLink(AxModel):
    """근거 영상 하나의 주소와 썸네일. YouTube 응답에 이미 있던 값만 (더 부르지 않음)."""

    video_id: str = Field(min_length=1)
    video_url: str | None = None  # https://www.youtube.com/watch?v=<id> (숏츠는 /shorts/<id>)
    thumbnail_url: str | None = None  # YouTube 응답 snippet.thumbnails 값 (없으면 None)
    # 아래는 예전 응답은 없다 (v2 화면 칸). 광고 재료로 쓸 수 있는지: 확인할 것이 없고 분류를
    # 알 때만 true, 확인할 것이 있으면 false, 분류를 모르면 None + caution_reason
    ad_usable: bool | None = None
    caution_types: list[CautionType] = Field(default_factory=list)
    caution_reason: str | None = None
    # 아래는 예전 응답은 없다. 저장된 응답의 제목과 조회수 (없으면 None)
    title: str | None = None
    views: int | None = Field(default=None, ge=0)


class YouTubeTagEvidence(AxModel):
    """후보 키워드의 YouTube 근거 (스냅샷 한 시각의 인기 영상 목록 안에서 센 값)."""

    video_count: int = Field(ge=1)  # 이 태그(정리한 뒤)가 붙은 인기 영상 수
    snapshot_at: UtcDatetime  # 스냅샷 수집 시각
    video_ids: list[str] = Field(default_factory=list)  # 대표 영상 id (인기 순위 순, 몇 개만)
    # 아래는 예전 응답은 없다. video_ids의 주소·썸네일 (같은 순서)
    video_links: list[VideoLink] = Field(default_factory=list)
    # 이 후보로 센 태그 (정리한 값, 스냅샷에 실제로 나온 것. 별칭이 묶였으면 여러 개).
    # 예전 응답은 없다
    matched_tags: list[str] = Field(default_factory=list)
    # 이 후보로 센 제목 구 (YouTube 제목에서 뽑은 2~3어절). 예전 응답은 없다
    matched_phrases: list[str] = Field(default_factory=list)


# 후보가 어디서 나왔는지 (화면 표시: youtube_tag = YouTube 태그, trending_search = 인기 검색어)
CandidateOrigin = Literal["youtube_tag", "youtube_title", "trending_search"]


class TrendingSearchEvidence(AxModel):
    """인기 검색어 근거 (공개 피드 스냅샷 한 시각, 피드에 있는 값 그대로)."""

    rank: int = Field(ge=1)  # 피드 안 순서
    traffic: str | None = None  # 피드의 트래픽 표시값 (예: "500+", 검색 건수가 아님). 없으면 None
    snapshot_at: UtcDatetime  # 스냅샷 수집 시각
    source: str = "google_trends_rss"  # 피드 이름
    keyword: str | None = None  # 피드에 나온 검색어 그대로 (정리 전)


class TrendCandidate(AxModel):
    keyword: str = Field(min_length=1)  # 정리한 태그 (앞뒤 공백·# 제거, 영문 소문자)
    youtube: YouTubeTagEvidence
    # 네이버 검색어 급상승 판정 (trend_scan과 같은 함수의 결과 그대로).
    # 네이버를 못 읽으면 이 후보만 status unknown + 이유
    naver: TrendVerdict
    # 대표 이름(`keyword`)에 묶인 별칭 (회사 설정 별칭 표). 네이버 그룹에 대표 이름과 함께 키워드로
    # 넣었다. 별칭이 없으면 빈 목록. 예전 응답은 없다
    aliases: list[str] = Field(default_factory=list)
    # 아래는 이름별 판정 (별칭이 있을 때만, 예전 응답은 없다). 묶음 그룹으로만 보면 작은 이름의
    # 급상승이 큰 이름에 묻혀서 대표 이름·별칭을 하나씩 따로도 판정한다
    name_verdicts: list[TrendVerdict] = Field(
        default_factory=list
    )  # 이름 하나씩 판정 (group_name = 그 이름)
    rising_names: list[str] = Field(default_factory=list)  # 따로 판정해 급상승인 이름
    # `naver`가 어디서 왔는지: group = 묶음 그룹 판정,
    # name = 묶음은 급상승이 아닌데 이름 하나가 급상승 (그 이름들 중 z가 가장 큰 판정을 올림)
    naver_from: Literal["group", "name"] = "group"
    # 아래는 출처 추가 (예전 응답은 없다). 이 후보가 나온 곳 (YouTube 태그는 늘, 인기 검색어에도
    # 있으면 trending_search가 붙고 `trending`에 그 근거)
    origins: list[CandidateOrigin] = Field(default_factory=list)
    trending: TrendingSearchEvidence | None = None
    # 데이터로 자동 묶은 다른 이름 (`aliases`에도 들어 있다). 예전 응답은 없다
    auto_grouped: list[str] = Field(default_factory=list)


class AutoExcludedTerm(AxModel):
    """쌓인 스냅샷 전체에서 늘 붙는 말로 보고 후보에서 자동으로 뺀 이름 (문서 빈도 기준)."""

    name: str
    source: Literal["youtube_tag", "youtube_title"]  # 태그에서 / 제목 구에서
    snapshot_videos: int = Field(ge=0)  # 이번 스냅샷에서 이 이름이 나온 영상 수 (빠지지 않았다면)
    videos: int = Field(ge=0)  # 쌓인 인기 영상 중 이 이름이 나온 영상 수
    total_videos: int = Field(ge=0)
    doc_ratio: float = Field(ge=0, le=1)  # videos ÷ total_videos
    categories: int = Field(ge=0)  # 이 이름이 나온 영상의 카테고리 수
    days: int = Field(ge=0)  # 이 이름이 나온 한국 날짜 수
    total_days: int = Field(ge=0)  # 기록이 있는 한국 날짜 수
    reason: str  # 코드가 숫자로 만든 한 줄


class NamePairEvidence(AxModel):
    """후보 이름 두 개를 같은 대상으로 볼지의 근거 (자동 묶음 또는 묶음 제안)."""

    names: list[str] = Field(min_length=2, max_length=2)
    correlation: float = Field(ge=-1, le=1)  # 두 이름 네이버 일별 값(로그)의 같은 날 피어슨 상관
    days: int = Field(ge=0)  # 상관에 쓴 날 수 (둘 다 값이 있는 날)
    shared_videos: int = Field(ge=0)  # 쌓인 인기 영상 중 두 이름이 함께 나온 영상 수
    applied: bool  # true = 기준을 넘어 자동으로 묶음, false = 제안만 (자동 적용 안 함)


class TrendingCandidate(AxModel):
    """인기 검색어에서만 나온 후보 (YouTube 근거 없음). 네이버 판정은 YouTube 후보와 같은 함수."""

    keyword: str = Field(min_length=1)  # 정리한 검색어 (별칭 표가 있으면 대표 이름)
    trending: TrendingSearchEvidence
    naver: TrendVerdict
    aliases: list[str] = Field(default_factory=list)
    name_verdicts: list[TrendVerdict] = Field(default_factory=list)
    rising_names: list[str] = Field(default_factory=list)
    naver_from: Literal["group", "name"] = "group"
    origins: list[CandidateOrigin] = Field(default_factory=lambda: ["trending_search"])


class TrendCandidatesOutput(AxModel):
    status: Literal["known", "unknown"]  # unknown = 스냅샷이 없거나 오래됨 (후보를 뽑지 않음)
    unknown_reason: str | None = None
    snapshot_at: UtcDatetime | None = None  # 쓴 스냅샷 시각 (없으면 None)
    snapshot_videos: int = Field(default=0, ge=0)  # 그 스냅샷의 인기 영상 수
    start_date: dt.date | None = None  # 네이버 요청 기간 (기준선 시작)
    end_date: dt.date | None = None  # 네이버 판정 마지막 날
    candidates: list[TrendCandidate] = Field(default_factory=list)  # 나온 영상 수 많은 순
    naver_calls: int = Field(default=0, ge=0)  # 네이버 검색어 트렌드를 부른 수 (실패 포함)
    summary: str  # 한 문장 (코드가 숫자로 만든 고정 문장)
    # 아래는 인기 검색어 출처 (예전 응답은 없다). YouTube 후보와 이름이 겹치면 그 후보에 붙고
    # (`TrendCandidate.trending`), 겹치지 않는 것만 여기 (피드 순위 순)
    trending_candidates: list[TrendingCandidate] = Field(default_factory=list)
    trending_status: Literal["known", "unknown"] = "unknown"  # 인기 검색어 스냅샷을 썼는지
    trending_reason: str | None = None  # unknown이면 이유 (스냅샷 없음·오래됨)
    trending_snapshot_at: UtcDatetime | None = None  # 쓴 인기 검색어 스냅샷 시각
    # 자동 제외 (예전 응답은 없다): 이번 스냅샷에서 빠진 늘 붙는 말과 근거, 무엇으로 쟀는지 한 줄
    auto_excluded: list[AutoExcludedTerm] = Field(default_factory=list)
    exclusion_basis: str | None = None
    # 이름 자동 묶기 (예전 응답은 없다): 자동으로 묶은 쌍, 제안만 하는 쌍 (상관 큰 순)
    auto_groups: list[NamePairEvidence] = Field(default_factory=list)
    group_suggestions: list[NamePairEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "unknown" and self.candidates:
            raise ValueError("unknown이면 후보가 없어야 한다")
        return self


# --------------------------------------------------------------- trend_history (월별 흐름)
# 키워드 그룹별 네이버 월 단위 값(timeUnit=month)과 "작년 같은 달 대비" 배수만 돌려준다.
# 급상승·계절 판정은 하지 않는다(평가가 없어서). 값은 그 그룹 요청 안 상대값(최댓값 = 100)이다.


class TrendHistoryRequest(AxModel):
    """trend_history skill 입력. 비우면 회사 기본 키워드 그룹·설정 개월 수·지난달까지."""

    # 없으면 회사 설정(trends.toml)의 기본 그룹. 그룹마다 네이버 호출 1번
    keyword_groups: list[TrendKeywordGroup] | None = Field(
        default=None, min_length=1, max_length=20
    )
    months: int | None = Field(default=None, ge=13, le=60)  # 볼 개월 수. 없으면 회사 설정(기본 24)
    end_month: dt.date | None = None  # 마지막 달(그 달의 아무 날). 없으면 지난달(다 채워진 달)

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        names = [g.group_name for g in self.keyword_groups or []]
        if len(names) != len(set(names)):
            raise ValueError("group_name이 겹친다")
        return self


class TrendMonthPoint(AxModel):
    month: dt.date  # 그 달 1일
    value: float = Field(ge=0)  # 그 그룹 요청 안 상대값 (최댓값 100)
    last_year_value: float | None = Field(default=None, ge=0)  # 작년 같은 달 값 (없으면 None)
    # 작년 같은 달 대비 배수 = 값 ÷ 작년 같은 달 값. 작년 값이 없거나 0이면 None (0으로 채우지 않음)
    vs_last_year: float | None = Field(default=None, ge=0)


class TrendHistoryGroup(AxModel):
    group_name: str
    keywords: list[str] = Field(default_factory=list)
    status: Literal["known", "unknown"]
    unknown_reason: str | None = None  # 자료 없음·네이버를 읽지 못함 등
    points: list[TrendMonthPoint] = Field(
        default_factory=list
    )  # 달 순서. 네이버가 주지 않은 달은 빠짐
    latest_month: dt.date | None = None  # 마지막으로 값이 있는 달
    latest_vs_last_year: float | None = Field(default=None, ge=0)  # 그 달의 작년 같은 달 대비

    @model_validator(mode="after")
    def _reason_when_unknown(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        return self


class TrendHistoryOutput(AxModel):
    start_month: dt.date  # 첫 달 1일
    end_month: dt.date  # 마지막 달 1일
    months: int = Field(ge=1)
    time_unit: Literal["month"] = "month"
    source: str  # 자료 출처 (naver_datalab)
    value_kind: Literal["relative"] = "relative"  # 검색 건수가 아니라 그룹 요청 안 상대값
    groups: list[TrendHistoryGroup] = Field(default_factory=list)  # 입력 그룹 순서
    naver_calls: int = Field(default=0, ge=0)  # 네이버 요청 수 (그룹마다 1번, 실패 포함)
    summary: str  # 한 문장 (코드가 숫자로 만든 고정 문장)


# ---------------------------------------------------------- youtube_velocity (YouTube 지금 속도)
# 최근 두 스냅샷 사이 영상별 시간당 조회 증가(지금 속도)와 그 전 구간 대비 빨라졌는지(가속).
# 판정하지 않는다(평소 속도 기준이 아직 없음). 숫자 칸이 빠진 영상은 빈 값, 지워진 영상은 뺀다.


class YouTubeVelocityRequest(AxModel):
    """youtube_velocity skill 입력. 비우면 회사 설정(trends.toml `[velocity] top`) 개수."""

    top: int | None = Field(default=None, ge=1, le=50)  # 지금 속도 상위 몇 개. 없으면 회사 설정


class YouTubeVideoVelocity(AxModel):
    video_id: str
    channel_id: str
    published_at: UtcDatetime
    popular_rank: int | None = Field(
        default=None, ge=1
    )  # 최근 스냅샷의 인기 순위 (인기 목록에 없으면 None)
    tags: list[str] = Field(default_factory=list)  # 저장된 태그 그대로
    views: int | None = Field(default=None, ge=0)  # 최근 스냅샷 조회수
    likes: int | None = Field(default=None, ge=0)  # 최근 스냅샷 좋아요 (숨김이면 None)
    comments: int | None = Field(default=None, ge=0)  # 최근 스냅샷 댓글 (막음이면 None)
    views_per_hour: float | None = None  # 지금 속도: 최근 두 스냅샷 사이 시간당 조회 증가
    prev_views_per_hour: float | None = None  # 그 전 구간(셋째·둘째 스냅샷 사이) 시간당 조회 증가
    speed_change: float | None = (
        None  # 지금 속도 − 그 전 속도 (시간당 조회). 그 전 구간이 없으면 None
    )
    # 아래는 예전 응답은 없다. 영상 주소와 썸네일 (저장된 YouTube 응답 값만)
    video_url: str | None = None
    thumbnail_url: str | None = None
    faster: bool | None = None  # 그 전 구간보다 빨라졌는지 (그 전 구간이 없으면 None)
    # 아래는 화면 표시용 (예전 응답은 없다). 스냅샷에 저장된 값이 없으면 None
    title: str | None = None  # 영상 제목
    channel_title: str | None = None  # 채널 이름
    duration_seconds: int | None = Field(default=None, ge=0)  # 영상 길이(초)


class YouTubeVelocityOutput(AxModel):
    status: Literal["known", "unknown"]  # unknown = 스냅샷이 2개 미만 등
    unknown_reason: str | None = None
    latest_at: UtcDatetime | None = None  # 최근 스냅샷 시각
    previous_at: UtcDatetime | None = None  # 그 앞 스냅샷 시각 (지금 속도 구간 시작)
    earlier_at: UtcDatetime | None = None  # 셋째 스냅샷 시각 (그 전 구간 시작, 없으면 None)
    compared_videos: int = Field(default=0, ge=0)  # 두 스냅샷 모두에 있어 속도를 잰 영상 수
    missing_views: int = Field(default=0, ge=0)  # 조회수가 빠져 속도를 못 잰 영상 수
    videos: list[YouTubeVideoVelocity] = Field(default_factory=list)  # 지금 속도 큰 순, 상위 N개
    summary: str  # 한 문장 (코드가 숫자로 만든 고정 문장)

    @model_validator(mode="after")
    def _reason_when_unknown(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        return self


# --------------------------------------------------------------- content_recommend (영상 소재 추천)
# trend_scan(회사 기본 키워드) + trend_candidates(YouTube 인기 태그) 판정에서 급상승한 것만 골라
# 코드 규칙 점수로 순위를 매기고, 소재마다 LLM이 영상 아이디어 문장·형식을 쓴다(숫자 검증).
# 급상승이 없으면 빈 목록 + 이유다(억지로 채우지 않는다). 콘텐츠 루프 2단계용.

ContentFormat = Literal["숏폼", "롱폼"]
ContentSource = Literal["naver", "youtube"]  # 검색 급상승, YouTube 인기 태그
RecommendOrigin = Literal["keyword_group", "youtube_tag", "youtube_title", "trending_search"]
# 밈 형식 (대표 영상 제목으로 LLM이 목록 안에서만 고름, 목록 밖 답은 기타). unknown = 분류하지 못함
MemeFormat = Literal["챌린지", "패러디·밈", "리액션", "하이라이트", "정보·리뷰", "기타", "unknown"]
# 소재 주제 분류 (LLM이 이 목록 안에서만 고른다. 목록 밖 답은 기타, 분류하지 못하면 unknown + 이유)
TopicCategory = Literal[
    "뉴스·사건",
    "정치·사회",
    "기부·재난",
    "인물",
    "게임",
    "연예·스포츠",
    "생활·취미",
    "교육·책",
    "기타",
    "unknown",
    "IT",  # 값 추가 (IT·기기·서비스)
    "K-pop·음악",  # 값 추가 (음악·아이돌·커버·안무, 연예·스포츠에서 나눔)
    "유머",  # 값 추가 (예능·코미디·상황극 클립, 연예·스포츠·기타에서 나눔)
    "드라마",  # 값 추가 (드라마 클립·리뷰)
    "교양",  # 값 추가 (다큐·강연·지식·역사)
]
# 추천 묶음: product_linked = 회사 상품과 연결된 소재, general = 일반 화제
RecommendGroup = Literal["product_linked", "general"]
# 임베딩 유사도를 재지 못한 까닭: 설정 없음 / 설정 오류(키 없음 등) / 요청 실패
SimilarityFailure = Literal["not_configured", "config_error", "request_failed"]


class ContentRecommendRequest(AxModel):
    """content_recommend skill 입력. 비우면 회사 설정(trends.toml `[recommend]`)과 어제 날짜."""

    max_items: int | None = Field(default=None, ge=1, le=10)  # 추천 수 상한. 없으면 회사 설정
    end_date: dt.date | None = None  # 네이버 판정할 마지막 날 (한국 날짜). 없으면 어제


class ContentScore(AxModel):
    """순위 점수와 그 근거 (점수 = z 가중치 × z + 두 소스면 가산점, 가중치는 회사 설정)."""

    score: float
    z: float  # 변화 정도 (trend_scan 판정의 강건 z)
    ratio: float = Field(ge=0)  # 기준선 대비 배수
    two_sources: bool  # 검색 급상승 + YouTube 인기 태그 근거가 둘 다 있음
    youtube_videos: int | None = Field(default=None, ge=1)  # YouTube 인기 영상 수 (두 소스일 때)


class LinkedProduct(AxModel):
    """소재와 연결한 회사 실제 상품 하나 (회사 상품 목록에 있는 값 그대로)와 연결 근거."""

    product_id: str
    name: str
    category_code: str | None = None
    category_name: str | None = None
    # name = 상품 이름 글자 일치, category = 카테고리 이름 일치, embedding = 임베딩 유사도만
    match: Literal["name", "category", "embedding"]
    similarity: float | None = None  # 임베딩 코사인 (재지 못했으면 None)
    reason: str  # 코드가 만든 한 줄 (예: 이름 일치: 파이썬)
    # 임베딩으로 이었을 때 1등 유사도 − 2등 유사도 (예전 응답·글자 일치는 None)
    margin: float | None = None


class ContentRecommendation(AxModel):
    rank: int = Field(ge=1)
    keyword: str = Field(
        min_length=1
    )  # 소재 이름 (기본 키워드 그룹 이름 또는 YouTube 후보 대표 이름)
    aliases: list[str] = Field(default_factory=list)  # 같이 본 이름 (네이버 그룹의 다른 키워드)
    sources: list[ContentSource]
    score: ContentScore
    # 회사 관련성 (표시만, 순위에서 빼지 않음): related = 회사 키워드와 겹침, low = 안 겹침
    company_relevance: Literal["related", "low"]
    company_match: list[str] = Field(default_factory=list)  # 겹친 회사 키워드
    idea: str  # 영상 아이디어 1~2문장 (LLM, 숫자 검증. 실패하면 고정 문장)
    format: ContentFormat  # 형식 제안
    fallback: bool  # 아이디어 문장을 고정 문장으로 바꿨는지
    # llm_unavailable·llm_error·format_error·number_invalid·llm_daily_cap(하루 상한)
    fallback_reason: str | None = None
    # 소재가 나온 곳 (예전 응답은 없다): keyword_group = 회사 기본 키워드 그룹, 그 밖은 후보 출처
    origins: list[RecommendOrigin] = Field(default_factory=list)
    # 아래는 상품 연결 (예전 응답은 없다). 연결할 회사 실제 상품 (최대 N개, 회사 설정)
    products: list[LinkedProduct] = Field(default_factory=list)
    # 회사 관련성을 무엇으로 정했는지: products = 상품 연결, manual_keywords = 수동 회사 키워드
    # (덮어쓰기용), both = 둘 다, none = 둘 다 없음 (low)
    relevance_from: Literal["products", "manual_keywords", "both", "none"] | None = None
    # 아래는 밈 형식 (예전 응답은 없다). YouTube 근거 영상이 없거나 분류하지 못하면 unknown + 이유
    meme_format: MemeFormat | None = None
    meme_reason: str | None = None
    meme_titles: int = Field(default=0, ge=0)  # 분류에 쓴 대표 영상 제목 수 (최대 5)
    # 아래는 적합성 (예전 응답은 없다). 주제 분류와 그 이유(목록 밖 답·분류 못 함일 때)
    topic_category: TopicCategory | None = None
    topic_category_reason: str | None = None
    # 추천 묶음: 회사 상품과 연결됐으면 product_linked, 아니면 general
    group: RecommendGroup | None = None
    # 실존 인물·팀·리그·상표가 소재에 들어 있는지(주제 분류와 같은 LLM 호출, 별도 칸)와 그 이름.
    # 분류하지 못했으면 None
    real_entity: bool | None = None
    entity_names: list[str] = Field(default_factory=list)


class ReferenceTopic(AxModel):
    """급상승했지만 회사 설정의 "추천 안 함" 분류라 추천하지 않은 소재 (참고용)."""

    keyword: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    origins: list[RecommendOrigin] = Field(default_factory=list)
    ratio: float = Field(ge=0)  # 기준선 대비 배수 (판정 그대로)
    z: float  # 변화 정도
    two_sources: bool
    topic_category: TopicCategory
    topic_category_reason: str | None = None  # 목록 밖 답 등
    reason: str  # 추천하지 않은 이유 (코드가 만든 한 줄, 예: 추천 안 함 분류: 뉴스·사건)


class ContentRecommendOutput(AxModel):
    start_date: dt.date | None = None  # 네이버 요청 기간 (기준선 시작)
    end_date: dt.date | None = None  # 네이버 판정 마지막 날
    items: list[ContentRecommendation] = Field(default_factory=list)  # 순위 순
    empty_reason: str | None = None  # 추천이 없을 때 이유 (예: 이번 기간 급상승한 소재가 없음)
    naver_calls: int = Field(default=0, ge=0)  # 이번 실행에서 네이버를 실제로 부른 수 (캐시 제외)
    naver_failed: list[str] = Field(default_factory=list)  # 네이버를 읽지 못한 그룹·후보
    youtube_status: Literal["known", "unknown"] = "unknown"  # YouTube 후보를 썼는지
    youtube_reason: str | None = None  # unknown이면 이유 (예: 스냅샷이 오래됨)
    snapshot_at: UtcDatetime | None = None  # 쓴 YouTube 스냅샷 시각
    summary: str  # 한 문장 (코드가 숫자로 만든 고정 문장)
    # 아래는 상품 연결 (예전 응답은 없다): 회사 상품 목록을 읽었는지, 임베딩 유사도를 쟀는지
    catalog_status: Literal["known", "unknown"] = "unknown"
    catalog_reason: str | None = None
    catalog_products: int = Field(default=0, ge=0)  # 읽은 판매 중 상품 수
    similarity_status: Literal["known", "unknown"] = "unknown"
    similarity_reason: str | None = None
    # 아래는 적합성 (예전 응답은 없다). 유사도를 재지 못한 까닭 (잰 경우·소재·상품이 없으면 None)
    similarity_failure: SimilarityFailure | None = None
    # 급상승했지만 "추천 안 함" 분류라 뺀 소재 (참고용, 순위 순)
    reference_items: list[ReferenceTopic] = Field(default_factory=list)
    excluded_categories: list[str] = Field(default_factory=list)  # 이번에 쓴 "추천 안 함" 분류

    @model_validator(mode="after")
    def _reason_when_empty(self) -> Self:
        if not self.items and not self.empty_reason:
            raise ValueError("추천이 없으면 empty_reason이 있어야 한다")
        if [i.rank for i in self.items] != list(range(1, len(self.items) + 1)):
            raise ValueError("rank는 1부터 차례로")
        return self


# --------------------------------------------------------------- trend_popular (지금 인기)
# 급상승 판정과 별개인 "지금 인기" 주제 순위. 점수는 코드(영상 수·조회 속도·인기 검색어 순위 몫의
# 가중합). 출처 하나를 못 읽으면 그 몫만 None(unknown) + 이유이고 0으로 채우지 않는다.
# 네이버를 부르지 않는다.


class TrendPopularRequest(AxModel):
    max_items: int | None = Field(default=None, ge=1, le=50)  # 없으면 회사 설정 [popular] top


class PopularScore(AxModel):
    score: float = 0.0  # 아는 몫만 가중합 (가중치는 출력 weights)
    video_share: float | None = None  # 인기 영상 수 몫 (YouTube를 못 읽었으면 None)
    velocity_share: float | None = None  # 지금 조회 속도 몫 (못 쟀으면 None)
    trending_share: float | None = None  # 인기 검색어 순위 몫 (인기 검색어를 못 읽었으면 None)


class PopularTopic(AxModel):
    rank: int = Field(ge=1)
    keyword: str = Field(min_length=1)  # 정리한 대표 이름
    aliases: list[str] = Field(default_factory=list)  # 같이 센 이름 (태그·별칭·자동 묶음)
    origins: list[CandidateOrigin] = Field(default_factory=list)
    score: PopularScore = Field(default_factory=PopularScore)
    youtube_videos: int | None = Field(default=None, ge=0)  # 이 주제가 나온 인기 영상 수
    video_ids: list[str] = Field(default_factory=list)  # 인기 순위 순 (최대 5)
    views_per_hour: float | None = None  # 그 영상들의 지금 시간당 조회 증가 합 (못 쟀으면 None)
    velocity_videos: int = Field(default=0, ge=0)  # 속도를 잰 영상 수
    velocity_reason: str | None = None  # 속도를 못 쟀으면 이유
    trending_rank: int | None = Field(default=None, ge=1)  # 이번에 본 인기 검색어 중 순위
    trending_traffic: str | None = None  # 피드의 트래픽 표시값 (예: "500+", 검색 건수 아님)
    topic_category: TopicCategory | None = None  # 주제 분류 (unknown = 분류하지 못함)
    topic_category_reason: str | None = None
    # 회사 "추천 안 함" 분류가 아니면 true, 그 분류면 false, 분류 unknown이면 None
    brand_safe: bool | None = None
    # 오늘 이미 낸 네이버 판정이 급상승이면 true, 평범이면 false, 오늘 판정이 없으면 None
    rising_now: bool | None = None
    rising_ratio: float | None = Field(default=None, ge=0)  # 그 판정의 배수
    # 실존 인물·팀·리그·상표가 주제에 들어 있는지와 그 이름 (분류하지 못했으면 None)
    real_entity: bool | None = None
    entity_names: list[str] = Field(default_factory=list)
    category_rank: int | None = Field(default=None, ge=1)  # 화면 카테고리 안 순위 (1~5)
    # 주제 이름 자체가 실존 사람 이름인지 (분류기, 분류하지 못했으면 None)
    is_person: bool | None = None
    # 아래는 예전 응답은 없다. video_ids 순서의 영상 (제목·채널·조회수는 저장된 값, 모르면 None)
    videos: list[PopularVideo] = Field(default_factory=list)
    videos_reason: str | None = None  # 제목을 못 붙인 영상이 있으면 이유
    # 아래는 예전 응답은 없다. "왜 뜨나" 근거 (숫자는 코드, 모르면 None + 이유)
    news: list[TrendingNewsLink] = Field(default_factory=list)  # 인기 검색어 RSS의 관련 뉴스
    news_reason: str | None = None  # 뉴스가 없으면 이유 (예: 인기 검색어에 없는 주제)
    rising_started: dt.date | None = None  # 네이버 판정 구간에서 평소 범위를 처음 넘은 날
    rising_reason: str | None = None  # 네이버 근거가 없으면 이유
    # "이런 근거가 있어요" 한 줄. llm = 숫자 검증 통과, fixed = 검증 실패·LLM 없음이라 고정 문장
    why_line: str | None = None
    why_status: Literal["llm", "fixed", "unknown"] | None = None
    why_reason: str | None = None  # fixed·unknown이면 이유


class TrendingNewsLink(AxModel):
    """인기 검색어 RSS가 함께 주는 관련 뉴스 하나 (피드 값 그대로)."""

    title: str = Field(min_length=1)
    source: str | None = None
    url: str | None = None


class PopularVideo(AxModel):
    """카테고리의 최근 조회수 높은 영상 하나 (지금 인기 주제가 모자랄 때 채움, 지금 인기 아님)."""

    video_id: str = Field(min_length=1)
    title: str | None = None
    channel_title: str | None = None
    views: int | None = Field(default=None, ge=0)  # 받은 시각의 누적 조회수
    published_at: UtcDatetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    # 아래는 예전 응답은 없다. 칸을 정한 분류(공유 분류기)와 YouTube가 준 카테고리 번호(참고)
    topic_category: TopicCategory | None = None
    youtube_category_id: str | None = None
    # 분류기 칸과 YouTube 카테고리가 가리키는 칸이 다르면 true (분류기를 따른다)
    category_mismatch: bool | None = None
    source: Literal["chart", "search"] | None = None  # 한국 인기 목록 / 카테고리 검색 보충
    # 아래는 예전 응답은 없다. 영상 근거 (코드 계산, 모르면 None, 이유는 evidence_reason)
    hours_since_published: float | None = Field(default=None, ge=0)  # 올린 뒤 지난 시간
    views_per_hour: float | None = Field(default=None, ge=0)  # 지금 시간당 조회 (두 스냅샷 차)
    channel_subscribers: int | None = Field(default=None, ge=0)  # channels.list, 숨김이면 None
    views_per_subscriber: float | None = Field(default=None, ge=0)  # 조회수 ÷ 구독자
    like_rate: float | None = Field(default=None, ge=0)  # 좋아요 ÷ 조회수
    comment_rate: float | None = Field(default=None, ge=0)  # 댓글 ÷ 조회수
    is_short: bool | None = None  # 길이가 숏츠 기준([shorts] max_seconds) 이하
    shorts_format: str | None = None  # 숏츠 형식 (shorts_stats 분류가 저장돼 있을 때만)
    evidence_reason: str | None = None  # 못 잰 칸과 이유
    # 아래는 예전 응답은 없다. 영상 주소와 썸네일 (YouTube 응답 값만)
    video_url: str | None = None
    thumbnail_url: str | None = None
    # 아래는 예전 응답은 없다 (v2 화면 칸). VideoLink와 같은 뜻
    ad_usable: bool | None = None
    caution_types: list[CautionType] = Field(default_factory=list)
    caution_reason: str | None = None


class PopularCategory(AxModel):
    """화면용 카테고리 하나의 "지금 인기" 1~N위 (회사 설정 [popular] categories)."""

    category: TopicCategory
    items: list[PopularTopic] = Field(default_factory=list)  # 점수 순, 최대 per_category개
    # N개를 못 채웠으면 이유 (예: 이 카테고리로 분류된 주제가 3개뿐)
    shortage_reason: str | None = None
    # 아래는 예전 응답은 없다. 주제가 모자랄 때 그 카테고리의 최근 조회수 높은 영상 (지금 인기 아님)
    top_videos: list[PopularVideo] = Field(default_factory=list)
    top_videos_reason: str | None = None  # 고른 기준, 또는 못 받은 이유


class TrendPopularOutput(AxModel):
    items: list[PopularTopic] = Field(default_factory=list)  # 점수 순
    empty_reason: str | None = None  # 주제가 없을 때 이유
    youtube_status: Literal["known", "unknown"] = "unknown"
    youtube_reason: str | None = None
    snapshot_at: UtcDatetime | None = None  # 쓴 YouTube 인기 스냅샷 시각
    velocity_status: Literal["known", "unknown"] = "unknown"
    velocity_reason: str | None = None
    velocity_from: UtcDatetime | None = None  # 조회 속도 구간 (최근 두 스냅샷)
    velocity_to: UtcDatetime | None = None
    trending_status: Literal["known", "unknown"] = "unknown"
    trending_reason: str | None = None
    trending_at: UtcDatetime | None = None  # 쓴 인기 검색어 스냅샷 시각
    weights: dict[str, float] = Field(default_factory=dict)  # video·velocity·trending 가중치
    excluded_categories: list[str] = Field(default_factory=list)  # brand_safe에 쓴 추천 안 함 분류
    summary: str = ""  # 코드가 만든 한 문장
    # 아래는 카테고리별 보기 (예전 응답은 없다). 화면 카테고리마다 1~N위.
    # 화면 카테고리가 아닌 주제는
    # 응답(items 포함)에서 뺀다
    by_category: list[PopularCategory] = Field(default_factory=list)
    display_categories: list[str] = Field(default_factory=list)  # 회사 설정의 화면 카테고리 순서
    score_method: str | None = None  # 점수 방식 (예: rank = 출처별 순위 몫)


# --------------------------------------------------------------- promo_concepts (홍보 영상 기획안)
# 재료(회사 상품·안전한 지금 인기 트렌드·카탈로그 카테고리 시즌·회사 이벤트)는 코드가 모으고,
# LLM은 정해진 스키마로 기획안 문장만 쓴다. 카탈로그에 없는 상품·재료 밖 트렌드·재료에 없는 숫자는
# 코드가 버린다(이유 기록).

PromoFormat = Literal["15초 숏폼", "30초 숏폼", "롱폼"]
# 지금 인기 · 시즌(작년 같은 달) · 회사 이벤트
PromoTrendKind = Literal["popular", "season", "event"]


class PromoConceptsRequest(AxModel):
    regenerate: bool = False  # true면 오늘 만든 기획안이 있어도 새로 만든다 (기본: 하루 캐시)


class PromoProduct(AxModel):
    """기획안에 나오는 회사 상품 (회사 상품 목록 값 그대로)."""

    product_id: str
    name: str
    category_name: str | None = None
    price: int | None = Field(default=None, ge=0)  # 판매가 (원)


class PromoTrend(AxModel):
    """재료 트렌드 하나 (코드가 고른 값)."""

    name: str = Field(min_length=1)
    kind: PromoTrendKind
    topic_category: TopicCategory | None = None  # popular만 (주제 분류)
    ip_risk: bool = False  # 실존 인물·팀·리그·상표가 든 트렌드 (그 이름이 제목·콘티에 있으면 경고)
    entity_names: list[str] = Field(default_factory=list)  # 그 실존 이름들 (경고 기준)
    detail: str = ""  # 코드가 만든 한 줄 (예: 지금 인기 3위, 작년 12월 검색이 평균의 1.6배)


class PromoConcept(AxModel):
    concept_id: str
    title: str
    concept: str = ""  # 컨셉 한 줄
    borrowed_trend: str  # 빌려 온 트렌드 (재료 트렌드 이름 그대로)
    trend_kind: PromoTrendKind | None = None
    products: list[PromoProduct] = Field(default_factory=list)  # 등장 상품
    format: PromoFormat | None = None
    storyboard: list[str] = Field(default_factory=list)  # 콘티 3컷
    cautions: list[str] = Field(default_factory=list)  # 주의사항 (LLM)
    warnings: list[str] = Field(default_factory=list)  # 코드가 단 경고 (상표·초상권 확인 필요 등)
    # 아래는 숏츠 광고 기획안 초안 (예전 응답은 없다)
    duration_seconds: int | None = Field(default=None, ge=1)  # 숏츠 길이(초)
    shorts_format: str | None = None  # 빌려 온 인기 숏츠 형식 (shorts_stats 형식 이름)
    shorts_format_avg_views: float | None = None  # 그 형식의 평균 조회수 (서버 값)
    # 아래는 예전 응답은 없다. 그 형식의 중앙값 조회수(대표값)와 숏츠 통계 기준 시각
    shorts_format_median_views: float | None = None
    shorts_format_at: UtcDatetime | None = None
    fit_reason: str | None = None  # 이 트렌드·형식이 이 상품과 맞는 이유 한 줄 (LLM)
    # 아래는 예전 응답은 없다. 타깃 연령대 (trend_age 결과, 코드). 모르면 None + 이유
    target_age: str | None = None  # 빌린 트렌드에서 평소 대비 가장 크게 오른 연령대
    target_age_ratio: float | None = Field(default=None, ge=0)  # 그 연령대 평소 대비 배수
    target_age_status: TrendStatus | None = None  # 그 연령대 판정 (rising·normal)
    target_age_reason: str | None = None  # 타깃 미정이면 이유
    # 아래는 예전 응답은 없다. 빌린 트렌드가 회사 상품·도메인과 연결이 없으면 참고용 (코드)
    reference_only: bool = False
    reference_reason: str | None = None
    # 아래는 예전 응답은 없다. concept_id는 내용 해시(회사·트렌드·정렬한 상품 id·숏츠 형식)라
    # 같은 재료면 날·실행이 달라도 같다. 바뀌기 전의 무작위 id는 legacy_concept_id로만 준다
    legacy_concept_id: str | None = None
    # 빌린 트렌드가 지금 인기 주제면, 브리프가 같은 주제에 주는 insight_id (stable_insight_id 규칙)
    source_insight_id: str | None = None
    # 아래는 예전 응답은 없다 (11/18). 기획이 ReceiveInsightInput으로 옮기는 영상 카테고리·형식.
    # shorts_stats 숏츠 형식 이름 (예: 챌린지). 재무·기획 content_category로 그대로 옮긴다
    content_category: str | None = Field(default=None, min_length=1, max_length=40)
    # finance ContentFormat과 같은 값. 데이터는 유튜브 숏츠만 보므로 shorts만 채운다
    content_format: Literal["shorts", "reels"] | None = None


class PromoShortsFormat(AxModel):
    """기획안 재료로 쓴 인기 숏츠 형식 (shorts_stats 값 그대로)."""

    name: str
    avg_views: float = Field(ge=0)  # 보조 칸 (평균)
    count: int = Field(default=0, ge=0)
    # 아래는 예전 응답은 없다. 대표값 = 중앙값, 숏츠 통계 기준 시각
    median_views: float | None = Field(default=None, ge=0)
    snapshot_at: UtcDatetime | None = None


class PromoSeasonCheck(AxModel):
    """시즌을 확인한 키워드 하나 (카탈로그 카테고리 이름 또는 상품 핵심어)."""

    keyword: str
    source: Literal["category", "product"] = "product"
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None  # unknown이면 이유
    same_month: dt.date | None = None  # 작년 같은 달 (1일)
    same_month_ratio: float | None = None  # 그 달 값 ÷ 기간 평균
    next_month: dt.date | None = None  # 작년 다음 달 (1일)
    next_month_ratio: float | None = None


class PromoSkippedTrend(AxModel):
    """재료 기준에 못 미쳐 뺀 지금 인기 주제 (코드가 만든 이유)."""

    name: str
    reason: str


class DroppedConcept(AxModel):
    """검증에서 버린 기획안 (제목과 이유만, 내용은 남기지 않는다)."""

    title: str
    reason: str


class PromoConceptsOutput(AxModel):
    concepts: list[PromoConcept] = Field(default_factory=list)
    dropped: list[DroppedConcept] = Field(default_factory=list)
    empty_reason: str | None = None  # 기획안이 없을 때 이유 (LLM 실패·재료 없음 등)
    trends: list[PromoTrend] = Field(default_factory=list)  # 쓴 재료 트렌드
    products: int = Field(default=0, ge=0)  # 재료 상품 수
    catalog_status: Literal["known", "unknown"] = "unknown"
    catalog_reason: str | None = None
    popular_status: Literal["known", "unknown"] = "unknown"  # 안전한 지금 인기 트렌드를 썼는지
    popular_reason: str | None = None
    season_status: Literal["known", "unknown"] = "unknown"  # 카테고리 월별 흐름을 읽었는지
    season_reason: str | None = None
    naver_calls: int = Field(default=0, ge=0)  # 이번 실행에서 네이버를 실제로 부른 수 (캐시 제외)
    cached: bool = False  # 오늘 만든 기획안을 그대로 줬는지
    # 아래는 숏츠 광고 기획안 초안 (예전 응답은 없다)
    shorts_formats: list[PromoShortsFormat] = Field(default_factory=list)  # 재료 숏츠 형식
    shorts_status: Literal["known", "unknown"] = "unknown"
    shorts_reason: str | None = None
    season_checks: list[PromoSeasonCheck] = Field(default_factory=list)  # 확인한 시즌 키워드
    # 아래는 재료 거르기 (예전 응답은 없다). 기준(출처 수·인기 영상 수)·사람 이름으로 뺀 주제와 이유
    skipped_trends: list[PromoSkippedTrend] = Field(default_factory=list)
    # 초안 중 지금 인기 트렌드를 쓴 것이 없을 때 그 이유 (LLM 한 줄 또는 코드)
    popular_trend_note: str | None = None
    run_id: str | None = None  # 기획안 기록 id
    generated_at: UtcDatetime | None = None
    summary: str = ""  # 코드가 만든 한 문장


# --------------------------------------------------------------- shorts_stats (숏츠 통계)
# YouTube 숏츠(길이 기준, API에 숏츠 표시가 없음)를 카테고리별·형식별로 센다. 숫자는 코드, 분류는
# LLM이
# 정해진 목록 안에서만. 표본이 적은 묶음은 unknown + 표본 부족.

ShortsFormat = Literal[
    "챌린지",
    "상황극·스케치",
    "리액션",
    "정보·팁",
    "비포애프터",
    "브이로그",
    "밈 패러디",
    "기타",
    "unknown",
]


class ShortsStatsRequest(AxModel):
    pass


class ShortsVideo(AxModel):
    video_id: str
    title: str | None = None
    channel_title: str | None = None
    views: int | None = Field(default=None, ge=0)
    # 시간당 조회 증가 (두 번 본 숏츠만, 예전 응답은 없다)
    views_per_hour: float | None = None
    # 아래는 예전 응답은 없다. known = 잼, measuring = 다시 본 간격이 기준 미만이라 측정 중,
    # pending = 한 번만 봐서 다음 조회 대기
    velocity_status: Literal["known", "measuring", "pending"] | None = None
    # 아래는 예전 응답은 없다. 숏츠 주소와 썸네일 (YouTube 응답 값만)
    video_url: str | None = None
    thumbnail_url: str | None = None
    # 아래는 예전 응답은 없다 (1단계 숏츠 카드). 주제 분류(저장된 분류기 결과, 모르면 "unknown")
    category: str | None = None
    # 채널 구독자 수와 조회 ÷ 구독자 (channels.list, 저장 재사용). 숨김·못 받음이면 None + 이유
    channel_subscribers: int | None = Field(default=None, ge=0)
    views_per_subscriber: float | None = Field(default=None, ge=0)
    subscriber_reason: str | None = None
    # 광고 재료로 쓸 수 있는지·확인할 것 (VideoLink와 같은 규칙)
    ad_usable: bool | None = None
    caution_types: list[CautionType] = Field(default_factory=list)
    caution_reason: str | None = None


class ShortsFormatCount(AxModel):
    """그룹 안의 숏츠 형식 하나와 그 수."""

    name: str  # 숏츠 형식
    count: int = Field(default=0, ge=0)


class ShortsGroup(AxModel):
    """카테고리 또는 형식 하나의 숏츠 통계."""

    name: str  # 주제 분류 값 또는 숏츠 형식
    status: Literal["known", "unknown"] = "known"
    reason: str | None = None  # unknown이면 이유 (예: 표본 부족 3개, 기준 5개)
    count: int = Field(default=0, ge=0)
    avg_views: float | None = None
    avg_views_per_hour: float | None = None  # 시간당 조회 증가를 잰 숏츠만의 평균
    velocity_videos: int = Field(default=0, ge=0)  # 시간당 조회 증가를 잰 숏츠 수
    top: list[ShortsVideo] = Field(default_factory=list)  # 조회수 상위 N개
    # 아래는 예전 응답은 없다. 한 번만 본 숏츠 수 (다음 조회 대기)와 그 이유
    velocity_pending: int = Field(default=0, ge=0)
    velocity_reason: str | None = None
    # 아래는 예전 응답은 없다. 대표값 = 중앙값 (평균 avg_views는 보조 칸)
    median_views: float | None = None
    median_views_per_hour: float | None = None  # 시간당 조회 증가를 잰 숏츠만의 중앙값
    velocity_measuring: int = Field(default=0, ge=0)  # 다시 본 간격이 기준 미만이라 측정 중인 수
    # 아래는 예전 응답은 없다. 카테고리 그룹만: 그 카테고리 숏츠의 형식별 수
    # (많은 순, 분류 못 한 형식 제외)
    formats: list[ShortsFormatCount] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 카테고리 그룹만: 화면 카드용 숏츠 (조회수 순, 설정 개수까지).
    # 이미 모은 숏츠 안에서만 고르고(더 검색하지 않음), 표본 부족 칸도 준다. 통계 칸과 무관
    videos: list[ShortsVideo] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 형식 그룹만: 이 형식 숏츠 중 게시된 지(YouTube snippet.publishedAt)
    # `ShortsStatsOutput.recent_days`일(= 숏츠 검색 기간 search_days) 안인 수 (반짝 1개인지 보려고).
    # 게시 시각을 모르는 숏츠는 세지 않고 그 수를 recent_unknown에 둔다
    recent_count: int | None = Field(default=None, ge=0)
    recent_unknown: int | None = Field(default=None, ge=0)
    # 아래는 예전 응답은 없다. 형식 그룹만: 하위 주제 숏츠를 뺀 이 형식 숏츠의 시간당 조회 중앙값과
    # 그 표본 수 ("숏츠 전체 대비" 배수의 분자, 분모 ShortsStatsOutput.median_views_per_hour와 같은
    # 모집단). 표본이 [shorts] min_sample 미만이면 None + promo_reason.
    # recent_count·recent_unknown도 같은 모집단으로 센다
    promo_median_views_per_hour: float | None = None
    promo_velocity_videos: int = Field(default=0, ge=0)
    promo_reason: str | None = None


class ShortsSearch(AxModel):
    """표본 넓히기 검색 한 번 (YouTube 카테고리 하나)."""

    youtube_category_id: str
    status: Literal["known", "unknown"]
    reason: str | None = None  # 할당량 초과·실패 이유
    found: int = Field(default=0, ge=0)  # 검색에서 받은 영상 수
    shorts: int = Field(default=0, ge=0)  # 그중 길이 기준 이하 숏츠 수


class ShortsStatsOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    snapshot_at: UtcDatetime | None = None  # 쓴 YouTube 인기 스냅샷 시각
    max_seconds: int = Field(default=60, ge=1)  # 숏츠 길이 기준(초)
    shorts: int = Field(default=0, ge=0)  # 숏츠 표본 수 (인기 + 검색, 중복 없음)
    popular_shorts: int = Field(default=0, ge=0)
    searched_shorts: int = Field(default=0, ge=0)
    unknown_length: int = Field(default=0, ge=0)  # 길이를 몰라 뺀 인기 영상 수
    by_category: list[ShortsGroup] = Field(default_factory=list)
    by_format: list[ShortsGroup] = Field(default_factory=list)
    search: list[ShortsSearch] = Field(default_factory=list)
    # known = 이번에 검색함, cached = 오늘 이미 검색해 저장한 결과, unknown = 검색하지 않음(이유)
    search_status: Literal["known", "unknown", "cached"] = "unknown"
    search_reason: str | None = None
    quota_used: int = Field(default=0, ge=0)  # 이번 실행에서 쓴 YouTube 할당량 단위
    velocity_note: str | None = None  # 평균 시간당 조회 증가를 잰 범위
    # 아래는 예전 응답은 없다. 검색 숏츠 다시 조회(videos.list): 이번에 다시 본 수와 못 본 이유
    rechecked: int = Field(default=0, ge=0)
    recheck_reason: str | None = None
    # 아래는 예전 응답은 없다. 표본 언어 (예: ko = 한국 숏츠만)와 그 기준으로 뺀 숏츠 수
    language: str | None = None
    foreign_excluded: int = Field(default=0, ge=0)
    # 아래는 예전 응답은 없다. 전체 숏츠 조회수 중앙값과 그 표본 수 (카테고리 배수의 분모)
    median_views: float | None = None
    median_views_count: int = Field(default=0, ge=0)
    limitation: str = ""  # 한계 (이미 뜬 영상끼리의 비교 등)
    summary: str = ""
    # 아래는 예전 응답은 없다. 하위 주제 검색(domain_subtopics)으로 이미 모은 숏츠 중
    # 표본에 더한 수 (같은 영상은 한 번만, 추가 검색 없음)
    subtopic_shorts: int = Field(default=0, ge=0)
    # 아래는 예전 응답은 없다. 전체 숏츠 시간당 조회 증가 중앙값과 그 표본 수
    # (형식별 "숏츠 전체 대비" 배수의 분모), 형식 그룹 recent_count를 센 기간
    # (게시 후 일, = 숏츠 검색 search_days).
    # 하위 주제 숏츠(subtopic_shorts)는 넣지 않는다 (형식 분자 promo_median_views_per_hour와 같은
    # 모집단). 표본이 [shorts] min_sample 미만이면 중앙값은 None이고 이유는
    # median_views_per_hour_reason
    median_views_per_hour: float | None = None
    median_views_per_hour_count: int = Field(default=0, ge=0)
    recent_days: int | None = Field(default=None, ge=1)
    median_views_per_hour_reason: str | None = None


# --------------------------------------------------------------- video_explorer (영상 탐색)
# 카테고리 × 기간(올린 시각 기준 1일·3일·7일) × 길이(숏 = 60초 이하, 롱 = 그 초과)마다 영상 목록.
# 영상 풀(저장된 인기 차트·추적·숏츠 표본·검색 결과에서 올린 지 7일 이내)에서 먼저 찾고, 칸이
# 최소 수보다 적으면 검색으로 보충한다(하루 상한). 수집기가 미리 계산하고 화면 요청은 읽기만.
# 정렬은 코드. 없는 값(좋아요 숨김 등)은 None + 이유.

ExplorerPeriod = Literal["1d", "3d", "7d"]
ExplorerLength = Literal["short", "long"]
ExplorerSort = Literal["views", "views_per_subscriber", "likes"]


class VideoExplorerRequest(AxModel):
    category: TopicCategory | None = None  # 없으면 설정의 카테고리 전부
    period: ExplorerPeriod = "7d"
    length: ExplorerLength = "short"
    sort: ExplorerSort = "views"


class ExplorerVideo(AxModel):
    video_id: str = Field(min_length=1)
    title: str | None = None
    channel_title: str | None = None
    channel_id: str | None = None
    thumbnail_url: str | None = None
    video_url: str | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    published_at: UtcDatetime | None = None  # 올린 시각 (publishedAt)
    views: int | None = Field(default=None, ge=0)
    views_reason: str | None = None
    likes: int | None = Field(default=None, ge=0)
    likes_reason: str | None = None  # 좋아요 숨김 등
    channel_subscribers: int | None = Field(default=None, ge=0)
    views_per_subscriber: float | None = Field(default=None, ge=0)  # 구독자 대비 배수
    subscriber_reason: str | None = None
    category: TopicCategory | None = None
    ad_usable: bool | None = None
    caution_types: list[CautionType] = Field(default_factory=list)
    caution_reason: str | None = None
    # 어디서 왔는지: chart = 인기 차트, tracked = 추적, shorts = 숏츠 표본, subtopic = 하위 주제
    # 검색, search = 이 skill의 보충 검색
    source: str | None = None
    at: UtcDatetime | None = None  # 조회수·좋아요를 읽은 시각
    # 아래는 예전 응답은 없다. 칸이 최소 수보다 적어 기간 밖(올린 지 7일 넘은) 숏츠 표본으로 채운
    # 영상이면 true (화면 표시 "7일 이전")
    before_period: bool = False


class ExplorerCell(AxModel):
    """카테고리 하나 × 기간 하나 × 길이 하나의 영상 목록."""

    category: str
    period: ExplorerPeriod
    length: ExplorerLength
    count: int = Field(default=0, ge=0)
    shortage_reason: str | None = None  # 최소 수보다 적으면 이유 (예: 이 기간 한국 영상 N개)
    videos: list[ExplorerVideo] = Field(default_factory=list)


class ExplorerSearch(AxModel):
    """보충 검색 하나 (카테고리 × 길이 × 구간)."""

    category: str
    length: ExplorerLength
    window: Literal["7d", "1d", "3d"]  # 3d: 값 추가 (3일 칸 보충 검색)
    status: Literal["known", "unknown", "skipped"]
    query: str | None = None  # 검색어 (그 카테고리 지금 인기 주제 이름, 교육·책은 도메인 씨앗)
    found: int = Field(default=0, ge=0)  # 길이·한국 영상 기준을 통과한 수
    reason: str | None = None  # unknown·skipped면 이유 (검색 상한·할당량 초과 등)
    day: dt.date | None = None  # 검색한 한국 날짜


class VideoExplorerOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    category: TopicCategory | None = None  # 요청 값 그대로
    period: ExplorerPeriod = "7d"
    length: ExplorerLength = "short"
    sort: ExplorerSort = "views"
    categories: list[str] = Field(default_factory=list)  # 설정의 카테고리 (화면 순서)
    min_per_cell: int = Field(default=10, ge=0)
    cells: list[ExplorerCell] = Field(default_factory=list)  # 요청 조건의 칸 (정렬된 영상)
    computed_at: UtcDatetime | None = None  # 수집기가 미리 계산한 시각
    searches: list[ExplorerSearch] = Field(default_factory=list)  # 오늘 보충 검색
    searches_today: int = Field(default=0, ge=0)
    search_cap: int = Field(default=0, ge=0)  # 하루 검색 상한 (설정)
    quota_used_today: int = Field(default=0, ge=0)  # 이 skill이 오늘 쓴 YouTube 할당량 (단위)
    limits: list[str] = Field(default_factory=list)
    summary: str = ""


# --------------------------------------------------------------- campaign_effect (캠페인 효과)
# 캠페인 기간 주문·매출을 지난 7일 같은 시간대 평균과 비교한 증분과, 캠페인 쿠폰 누적 사용률.
# 항목마다 "앎(값·근거)" 또는 "모름 + 이유"다. 모르면 0으로 채우지 않는다.

CampaignDirection = Literal["up", "none", "down"]  # 늘었음·같음·줄었음
CampaignStatus = Literal["known", "unknown"]


class CampaignEffectInput(AxModel):
    campaign_id: str | None = None  # 없으면 쿠폰은 확인할 수 없음 (주문·매출은 본다)
    window_from: UtcDatetime  # 캠페인 시작
    window_to: UtcDatetime  # 캠페인 끝
    # 아래는 선택. 기획이 결과를 기록할 때 연결하려고 보내면 결과에 그대로 돌려준다 (저장하지 않음)
    insight_id: str | None = None  # trend_insight_brief의 insight_id
    proposal_id: str | None = None  # 기획 쪽 제안 id
    # 아래는 예전 요청은 없다 (#646). 관련 상품 (상품 id 또는 이름, 최대 10개). 있으면 결과에
    # 상품 기준(`products_effect`)을 따로 낸다. 이름은 카탈로그 이름과 정규화(NFKC, 소문자, 공백
    # 모두 뺌)한 값이 정확히 같을 때만 맞추고, 못 맞춘 값은 `unmatched_products`로 돌려준다
    products: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.window_to <= self.window_from:
            raise ValueError("window_to가 window_from보다 뒤여야 한다")
        return self


class CampaignChange(AxModel):
    """주문 수 또는 매출의 캠페인 기간 변화."""

    status: CampaignStatus
    direction: CampaignDirection | None = None
    actual: float | None = None  # 캠페인 기간 합계
    expected: float | None = None  # 기대 = 지난 7일 같은 시간대 합계의 평균
    excess: float | None = None  # 증분 = 실제 − 기대
    ratio: float | None = Field(default=None, ge=0)  # 실제 ÷ 기대
    p_value: float | None = Field(default=None, ge=0, le=1)  # 방향 판정에 쓴 포아송 꼬리확률
    baseline_days: int = Field(default=0, ge=0)  # 기대에 쓴 지난 구간 수
    unknown_reason: str | None = None
    # 기준선 정의 (예: "지난 7일 같은 시간대", "시작 전 14일 같은 요일·시간대"). 예전 응답은 없다
    baseline_rule: str | None = None
    # 기준선에서 뺀 지난 구간과 이유 (빈 구간·기록 공백). 뺀 게 없으면 None. 예전 응답은 없다
    baseline_note: str | None = None

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "known" and (self.direction is None or self.excess is None):
            raise ValueError("known이면 direction과 excess가 있어야 한다")
        return self


class CampaignCoupons(AxModel):
    """캠페인 쿠폰 누적 사용률 (조회 시점 기준, 캠페인 뒤 사용분도 섞인다)."""

    status: CampaignStatus
    coupon_count: int | None = Field(default=None, ge=0)  # 쿠폰 수 (사람마다 한 장씩 발급된 쿠폰)
    issued_units: int | None = Field(default=None, ge=0)  # 발급 장수 합
    used_units: int | None = Field(default=None, ge=0)  # 사용 장수 합 (조회 시점)
    use_rate: float | None = Field(default=None, ge=0, le=1)  # 사용 ÷ 발급
    note: str | None = None  # 고정 설명 문장 (코드)
    unknown_reason: str | None = None

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "known" and self.use_rate is None:
            raise ValueError("known이면 use_rate가 있어야 한다")
        return self


class CampaignSalesTotals(AxModel):
    """한 구간의 판매액 (결제 완료 주문, 주문 접수 시각 기준, 원 단위 정수)."""

    orders: int = Field(ge=0)  # 결제 완료 주문 수
    gross_krw: int = Field(ge=0)  # 주문 금액 합 (상품가 × 수량, 할인 전)
    discount_krw: int = Field(ge=0)  # 쿠폰 할인 합
    paid_krw: int = Field(ge=0)  # 결제 금액 합 (성공한 결제)
    refund_krw: int = Field(ge=0)  # 환불 합 (조회 시점)


class CampaignFact(AxModel):
    """근거 숫자 하나 (출처·기간·표본)."""

    label: str
    value: float
    unit: str  # KRW, 건
    source: str = "shop-api"
    period: str  # "<from>~<to>" (UTC ISO)
    sample: int | None = Field(default=None, ge=0)  # 표본 (주문 수·기준선 날 수)
    # 아래는 예전 응답은 없다. 같은 응답 안에서 이 숫자를 가리키는 id, 근거 주소
    fact_id: str | None = None
    link: str | None = None


class DataFact(AxModel):
    """응답에 나오는 숫자 하나 (출처·기간·표본). 숫자는 코드가 낸다.

    응답 안의 숫자는 모두 facts에 있고, 문장(한 줄·요약)은 facts에 있는 숫자만 쓴다.
    """

    fact_id: str = Field(min_length=1)  # 같은 응답 안에서 이 숫자를 가리키는 id
    label: str
    value: float
    unit: str  # 배, 개, 회, 원(KRW), 건, 일, 위, 비율 등
    source: str  # youtube, google_trending, naver_search_trend, trend_age, shorts_stats, code 등
    link: str | None = None  # 근거 주소 (영상·뉴스 등)
    period: str | None = None  # "<from>~<to>" 또는 스냅샷 시각
    sample: int | None = Field(default=None, ge=0)  # 표본 (영상 수·날 수·주문 수)
    about: str | None = None  # 어느 항목의 숫자인지 (insight_id·opportunity_id)


class CampaignSales(AxModel):
    """캠페인 판매액 (증분). 모르면 unknown + 이유, 0으로 채우지 않는다."""

    status: CampaignStatus
    unknown_reason: str | None = None
    basis: str = "결제 완료 주문, 주문 접수 시각 기준, 원 단위 정수"
    period: CampaignSalesTotals | None = None  # 캠페인 구간
    baseline: CampaignSalesTotals | None = None  # 기준선 날들의 평균 (원 단위 반올림)
    baseline_rule: str | None = None  # 주문 수와 같은 기준선 정의
    baseline_days: int = Field(default=0, ge=0)
    baseline_note: str | None = None  # 기준선에서 뺀 날과 이유
    incremental_gross_krw: int | None = None  # 증분 판매액 = 구간 − 기준선 (주문 금액, 할인 전)
    incremental_paid_krw: int | None = None  # 증분 결제 금액
    # 기준선 날들의 범위로 낸 증분의 하한·상한 (구간 − 가장 많은 날, 구간 − 가장 적은 날)
    incremental_low_krw: int | None = None
    incremental_high_krw: int | None = None
    # 방향: 증분 범위가 0을 넘으면 up, 0 아래면 down, 걸치거나 기준선 날이 1개면 uncertain
    direction: Literal["up", "down", "uncertain"] | None = None
    direction_reason: str | None = None
    orders_direction: CampaignDirection | None = None  # 주문 수 판정(포아송)과 함께 본다
    # 쿠폰 귀속: 캠페인 쿠폰을 쓴 구간 주문의 판매액 합 (모르면 None + 이유)
    direct_attributed_krw: int | None = None
    direct_orders: int | None = Field(default=None, ge=0)
    direct_reason: str | None = None

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "known" and self.incremental_gross_krw is None:
            raise ValueError("known이면 incremental_gross_krw가 있어야 한다")
        return self


class CampaignProductMeasure(AxModel):
    """상품 기준 한 칸(주문 수·판매 수량·판매액)의 기간값·기준선·증분·범위·방향 (#646).

    판매액(`campaign_effect` sales)과 같은 규칙: 기준선 = 주문 수 판정이 쓴 지난 구간들의 평균,
    범위 = 구간 − 가장 많은 날 ~ 구간 − 가장 적은 날, 방향은 범위가 0을 넘으면 up, 0 아래면 down,
    걸치거나 기준선 날이 1개면 uncertain.
    """

    period: float = Field(ge=0)  # 캠페인 구간 값
    baseline: float | None = Field(default=None, ge=0)  # 기준선 날들의 평균 (모르면 None)
    incremental: float | None = None  # 증분 = 구간 − 기준선
    incremental_low: float | None = None
    incremental_high: float | None = None
    direction: Literal["up", "down", "uncertain"] | None = None
    direction_reason: str | None = None


class CampaignMatchedProduct(AxModel):
    product_id: str = Field(min_length=1)
    name: str | None = None  # 카탈로그 이름
    asked: str  # 요청에 온 값 그대로 (id 또는 이름)


class CampaignProductsEffect(AxModel):
    """관련 상품 기준 결과 (#646). 맞은 상품이 든 결제 완료 주문만 센다.

    가게 전체(`orders`·`sales`, 참고용)·쿠폰 귀속(`coupons`·`sales.direct_attributed_krw`)과 따로다.
    같은 주문이 여러 칸에 겹칠 수 있어 합산하지 않는다. 모르면 unknown + 이유, 0으로 채우지 않는다.
    """

    status: CampaignStatus
    unknown_reason: str | None = None
    basis: str = (
        "결제 완료 주문 중 맞은 상품의 품목만, 주문 접수 시각 기준, 판매액은 단가 × 수량(할인 전)"
    )
    matched: list[CampaignMatchedProduct] = Field(default_factory=list)
    unmatched_products: list[str] = Field(default_factory=list)  # 못 맞춘 요청 값 (추측하지 않음)
    orders: CampaignProductMeasure | None = None  # 맞은 상품이 든 주문 수
    units: CampaignProductMeasure | None = None  # 맞은 상품 판매 수량
    gross_krw: CampaignProductMeasure | None = None  # 맞은 상품 판매액 (원)
    baseline_rule: str | None = None  # 주문 수 판정과 같은 기준선 정의
    baseline_days: int = Field(default=0, ge=0)
    baseline_note: str | None = None  # 기준선에서 뺀 날과 이유

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "known" and (
            not self.matched or self.orders is None or self.units is None or self.gross_krw is None
        ):
            raise ValueError("known이면 맞은 상품과 주문 수·수량·판매액이 있어야 한다")
        return self


class CampaignEffectOutput(AxModel):
    campaign_id: str | None = None
    window_from: UtcDatetime
    window_to: UtcDatetime
    orders: CampaignChange
    revenue: CampaignChange
    coupons: CampaignCoupons
    summary: str  # 한두 문장 (LLM, 숫자 검증 실패면 고정 문장)
    # 아래는 예전 응답은 없다. 요청에 있던 연결 id 그대로 (데이터는 저장하지 않는다)
    insight_id: str | None = None
    proposal_id: str | None = None
    # 아래는 예전 응답은 없다. 판매액 (shop-api 주문 기록에서 코드가 셈)과 근거 숫자 목록
    sales: CampaignSales | None = None
    facts: list[CampaignFact] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 증분 주문 수 (= 주문 판정의 실제 − 기대, 정수 반올림. 모르면 None),
    # 응답 모양 버전과 만든 시각
    incremental_orders: int | None = None
    schema_version: str = ""
    generated_at: UtcDatetime | None = None
    # 아래는 예전 응답은 없다 (#646). 요청에 products가 있을 때만: 관련 상품 기준 결과
    products_effect: CampaignProductsEffect | None = None
    # 아래는 예전 응답은 없다 (#648). 유입경로 기준 (요청에 campaign_id가 있을 때만)과
    # 유입 + 관련 상품 (products도 있을 때만). 직접 귀속이라 기준선과 비교하지 않는다
    attribution_effect: CampaignAttribution | None = None
    attribution_and_products: CampaignAttributionProducts | None = None


class CampaignSourceRow(AxModel):
    """유입 출처 하나 (`src_source`, 없으면 "(출처 없음)")의 이 캠페인 주문."""

    source: str = Field(min_length=1)
    mediums: list[str] = Field(default_factory=list)  # 이 출처로 온 주문의 매체 (`src_medium`)
    orders: int = Field(ge=0)
    gross_krw: int = Field(ge=0)  # 판매액 (단가 × 수량, 할인 전)


class CampaignAttribution(AxModel):
    """이 캠페인 링크(`src_campaign` = campaign_id)로 들어온 결제 완료 주문 (#648).

    유입·상품(#646)·쿠폰 귀속·가게 전체는 같은 주문이 겹칠 수 있어 합산하지 않는다.
    구간에 유입 표시가 있는 주문이 하나도 없으면 unknown + "유입 기록 없음" (0으로 채우지 않음).
    """

    status: CampaignStatus
    unknown_reason: str | None = None
    basis: str = (
        "결제 완료 주문 중 유입 표시 campaign_id가 같은 것, 주문 접수 시각 기준,"
        " 판매액은 단가 × 수량(할인 전). 직접 귀속이라 기준선과 비교하지 않는다"
    )
    campaign_id: str | None = None
    orders: int | None = Field(default=None, ge=0)
    gross_krw: int | None = Field(default=None, ge=0)
    by_source: list[CampaignSourceRow] = Field(default_factory=list)  # 주문 많은 순
    window_orders: int | None = Field(default=None, ge=0)  # 구간 결제 완료 주문 (전부)
    tagged_orders: int | None = Field(default=None, ge=0)  # 그중 유입 표시가 있는 것 (아무 캠페인)
    tagged_share: float | None = Field(default=None, ge=0, le=1)  # tagged_orders ÷ window_orders
    note: str | None = None  # 한계 (예: 꼬리표 없이 들어온 주문은 세지 않음)

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "known" and (self.orders is None or self.gross_krw is None):
            raise ValueError("known이면 orders·gross_krw가 있어야 한다")
        return self


class CampaignAttributionProducts(AxModel):
    """이 캠페인으로 들어와 관련 상품(#646에서 맞은 상품)을 산 결제 완료 주문 (그 상품 품목만)."""

    status: CampaignStatus
    unknown_reason: str | None = None
    matched: list[str] = Field(default_factory=list)  # 맞은 상품 id
    orders: int | None = Field(default=None, ge=0)
    units: int | None = Field(default=None, ge=0)
    gross_krw: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "known" and (self.orders is None or self.gross_krw is None):
            raise ValueError("known이면 orders·gross_krw가 있어야 한다")
        return self


# ---------------------------------------------------------------- 연령대별 상승 (trend_age)

AGE_NOTE = (
    "네이버 값은 요청마다 최댓값이 100인 상대값이라 연령대끼리 검색량을 비교하지 않는다."
    " 각 연령대 안에서 평소 대비 배수만 본다"
)


class TrendAgeRequest(AxModel):
    """비우면 코드가 대상을 고른다(급상승·지금 인기 상위·시즌 키워드, 수는 회사 설정)."""

    keywords: list[str] = Field(default_factory=list, max_length=30)
    max_topics: int | None = Field(default=None, ge=1, le=30)


class AgeBandTrend(AxModel):
    """주제 하나의 연령대 하나: 그 연령대만 조회한 값으로 낸 판정 (trend_scan과 같은 함수)."""

    band: str = Field(min_length=1)  # 회사 설정 이름 (예: 20대)
    ages: list[str] = Field(default_factory=list)  # 네이버 연령 코드
    status: TrendStatus
    ratio: float | None = Field(default=None, ge=0)  # 그 연령대 평소 대비 배수
    latest_value: float | None = Field(default=None, ge=0)
    baseline_value: float | None = Field(default=None, ge=0)
    reason: str | None = None  # unknown이면 이유


class AgeTopic(AxModel):
    keyword: str = Field(min_length=1)
    keywords: list[str] = Field(default_factory=list)  # 네이버에 함께 보낸 이름
    sources: list[Literal["popular", "rising", "season", "request"]] = Field(default_factory=list)
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None  # unknown이면 이유 (예: 하루 호출 상한)
    bands: list[AgeBandTrend] = Field(default_factory=list)
    # 평소 대비 배수가 가장 큰 연령대 (급상승 판정 연령대가 있으면 그중). 모르면 None
    top_band: str | None = None
    top_ratio: float | None = Field(default=None, ge=0)


class TrendAgeOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    start_date: dt.date | None = None
    end_date: dt.date | None = None
    bands: list[str] = Field(default_factory=list)  # 설정 순서
    items: list[AgeTopic] = Field(default_factory=list)
    calls_used: int = Field(default=0, ge=0)  # 오늘 이 skill이 쓴 네이버 호출 수
    call_cap: int = Field(default=0, ge=0)  # 하루 상한
    cached: bool = False
    note: str = AGE_NOTE
    generated_at: UtcDatetime | None = None
    summary: str = ""
    # 아래는 예전 응답은 없다. 한계 (상한에 닿았을 때 어느 skill이 얼마나 썼는지 등)
    # 와 skill별 오늘 호출 수
    limits: list[str] = Field(default_factory=list)
    calls_by_caller: dict[str, int] = Field(default_factory=dict)


# ------------------------------------------------ 인사이트 브리프 (trend_insight_brief)


class TrendInsightBriefRequest(AxModel):
    max_insights: int | None = Field(default=None, ge=3, le=7)  # 비우면 회사 설정
    regenerate: bool = False  # 하루 캐시를 쓰지 않고 새로 묶는다
    # 아래는 예전 요청은 없다 (v5). read = 판 읽기(version 없으면 최신),
    # regenerate_start = 다시 추천 시작(백그라운드, 바로 started/blocked),
    # status = 진행 상태만 (화면이 2초마다 읽음)
    action: Literal["read", "regenerate_start", "status"] = "read"
    version: int | None = Field(default=None, ge=1)  # 오늘 판 번호 (없으면 최신)
    requested_by: str | None = Field(default=None, max_length=40)  # 화면 계정 표시 이름 (기록용)


class CustomerReaction(AxModel):
    """연결 상품 하나의 고객 반응 (고객지원 book_issue_stats 결과 그대로, 데이터는 세지 않는다)."""

    product_id: str = Field(min_length=1)
    product_name: str
    period: Literal["day", "week", "month"] = "month"
    claim_count: int = Field(ge=0)  # 이 상품이 든 클레임 수
    defect_count: int = Field(ge=0)  # 사유가 불량·파손·오배송·누락인 클레임 수
    sold_qty: int = Field(ge=0)  # 같은 기간 판매 수량
    # 클레임 수량 ÷ 같은 기간 판매 수량. 접수 기간 기준이라 1을 넘을 수 있다 (그때 note)
    claim_rate: float | None = Field(default=None, ge=0)
    note: str | None = None


class InsightEvidence(AxModel):
    """인사이트 하나의 데이터 근거 (모두 기존 skill 결과 그대로, 없으면 None)."""

    youtube_videos: int | None = Field(default=None, ge=0)  # 이 주제가 나온 인기 영상 수
    youtube_views_per_hour: float | None = Field(default=None, ge=0)  # 그 영상들 시간당 조회 합
    youtube_views_per_subscriber: float | None = Field(default=None, ge=0)  # 구독자 대비 최대
    youtube_at: UtcDatetime | None = None  # YouTube 스냅샷 시각
    google_rank: int | None = Field(default=None, ge=1)  # 인기 검색어 순위
    google_news: list[TrendingNewsLink] = Field(default_factory=list)
    google_at: UtcDatetime | None = None  # 인기 검색어 스냅샷 시각
    naver_ratio: float | None = Field(default=None, ge=0)  # 네이버 평소 대비 배수
    naver_status: TrendStatus | None = None
    naver_rising_started: dt.date | None = None
    naver_until: dt.date | None = None  # 네이버 판정 마지막 날
    links: list[str] = Field(default_factory=list)  # 근거 영상·뉴스 주소
    missing: list[str] = Field(default_factory=list)  # 없는 출처와 이유
    # 아래는 예전 응답은 없다. 시즌 근거 (작년 같은 달·다음 달 ÷ 기간 평균 중 큰 값)
    season_keyword: str | None = None
    season_ratio: float | None = Field(default=None, ge=0)
    season_month: dt.date | None = None
    # 아래는 예전 응답은 없다. 근거 영상의 주소·썸네일 (links의 영상과 같은 것)
    video_links: list[VideoLink] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 연결 상품의 고객 반응 (고객지원 book_issue_stats 값 그대로).
    # 판매 5권 미만·못 읽은 상품은 넣지 않고 이유를 missing에 적는다
    customer_reaction: list[CustomerReaction] = Field(default_factory=list)


class InsightProduct(AxModel):
    """임베딩 기준(product_link)을 통과한 회사 상품."""

    product_id: str
    name: str
    category_name: str | None = None
    similarity: float | None = None
    reason: str  # 코드가 만든 연결 근거 한 줄
    season: str | None = None  # 이 상품 쪽 시즌 근거 (기획안 시즌 확인, 없으면 None)


EvidenceHighlightSource = Literal["naver_search", "naver_shopping", "youtube", "google"]


class EvidenceHighlight(AxModel):
    """근거 요약 하나 (코드가 facts 중 센 것을 고름, LLM 안 씀).

    평소 값이 없는 근거(Google 순위·숏츠 조회수 등)는 baseline_value·ratio가 None이고 값만 있다.
    """

    source: EvidenceHighlightSource
    label: str  # 무엇을 잰 값인지 (facts의 label)
    value: float  # 지금 값
    unit: str
    baseline_value: float | None = None  # 평소 값 (기준선)
    ratio: float | None = Field(default=None, ge=0)  # 지금 ÷ 평소
    period: str | None = None  # 기간 (예: 지난 8주 기준, 스냅샷 시각)
    sample: int | None = Field(default=None, ge=0)  # 표본 (날 수·영상 수)
    sentence: str  # 템플릿 문장 하나 (예: 네이버 검색이 평소의 1.42배예요 (지난 8주 기준))
    fact_id: str | None = None  # 고른 숫자의 facts id
    # 아래는 예전 응답은 없다. 표본이 기준(설정)보다 적은 근거 (뒤로 보냄, 화면은 막대 없이 "참고")
    # 와 그 표시 문구 (예: "표본 6개 · 참고")
    low_sample: bool = False
    sample_note: str | None = None


class BorrowFormat(AxModel):
    """형식 빌리기 인사이트의 빌릴 숏츠 형식 (숏츠 통계 값 그대로)."""

    name: str = Field(min_length=1)  # 숏츠 형식 (예: 챌린지)
    median_views: float | None = Field(default=None, ge=0)  # 그 형식 조회 중앙값
    samples: int | None = Field(default=None, ge=0)  # 그 형식 숏츠 수
    source: Literal["topic", "category", "all"] | None = None  # 어느 단계 숏츠 통계에서 골랐는지


class TopicFormatExample(AxModel):
    """이 주제 숏츠 중 고른 형식인 영상 하나 (제목·조회수는 저장된 값 그대로, 모르면 None)."""

    title: str | None = None
    views: int | None = Field(default=None, ge=0)


class TrendInsight(AxModel):
    insight_id: str = Field(min_length=1)  # 날짜 + 주제 기반 (같은 날 같은 주제면 같은 id)
    rank: int = Field(ge=1)
    topic: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    line: str  # 한 줄 인사이트 (근거 숫자만, 검증 실패면 고정 문장)
    line_status: Literal["llm", "fixed"] = "fixed"
    line_reason: str | None = None
    category: TopicCategory | None = None
    ad_material: bool | None = None  # 광고 재료로 쓸 수 있는지
    ad_material_reason: str | None = None
    reference_only: bool = False  # 광고 재료가 아니라 맨 아래 참고용
    real_entity: bool | None = None  # 실존 인물·팀·리그·상표가 든 주제
    entity_names: list[str] = Field(default_factory=list)
    evidence: InsightEvidence = Field(default_factory=InsightEvidence)
    target_age: str | None = None  # 평소 대비 가장 크게 오른 연령대 (trend_age)
    target_age_ratio: float | None = Field(default=None, ge=0)
    target_age_status: TrendStatus | None = None
    target_age_reason: str | None = None  # 미정이면 이유
    products: list[InsightProduct] = Field(default_factory=list)
    products_reason: str | None = None  # 없으면 이유
    shorts_format: str | None = None  # 이 주제 숏츠 영상에서 가장 많은 형식
    shorts_format_samples: int | None = Field(default=None, ge=0)
    shorts_format_reason: str | None = None
    # 아래는 예전 응답은 없다. 숏츠 통계와 같은 시점의 그 형식 중앙값 조회수와 기준 시각
    # (그 형식 전체 숏츠 중앙값 (주제와 무관), 같은 형식이면 모든 주제가 같은 값)
    shorts_format_median_views: float | None = Field(default=None, ge=0)
    shorts_format_at: UtcDatetime | None = None
    # 아래는 예전 응답은 없다. 이 주제 자기 숏츠 중 고른 형식인 영상의 조회 중앙값 (source=topic일
    # 때만, 표본이 모자라면 None). 기준 시각 = 이 주제 인기 스냅샷 시각. 예시 = 조회 큰 순 최대 3개
    shorts_format_topic_median_views: float | None = Field(default=None, ge=0)
    shorts_format_topic_at: UtcDatetime | None = None
    shorts_format_topic_examples: list[TopicFormatExample] = Field(default_factory=list)
    shorts_format_topic_reason: str | None = None  # 값이 없을 때 이유
    # 아래는 예전 응답은 없다. 형식을 어느 단계에서 골랐는지: topic = 이 주제 근거 숏츠,
    # category = 이 주제 카테고리의 숏츠 통계, all = 전체 숏츠 통계.
    # 그 단계의 표본: count = 이 형식인 숏츠 수, total = 형식을 분류한 숏츠 수
    shorts_format_source: Literal["topic", "category", "all"] | None = None
    shorts_format_source_count: int | None = Field(default=None, ge=0)
    shorts_format_source_total: int | None = Field(default=None, ge=0)
    limits: list[str] = Field(default_factory=list)  # 한계 (평가셋 없음, 상대값 등)
    example_ideas: list[str] = Field(default_factory=list)  # 이 주제를 쓴 예시 아이디어 (참고용)
    score: float = 0.0  # 순서를 정한 코드 점수 (가중치는 회사 설정)
    # 아래는 예전 응답은 없다. 분류기가 아직 분류하지 못한 주제 (순위에 넣지 않고 참고용)
    classification_pending: bool = False
    # 아래는 예전 응답은 없다. 회사 관련 인사이트 칸 (시즌 키워드·상품 핵심어 중 오른 것, 별도 확보)
    company_related: bool = False
    company_reason: str | None = None  # 왜 회사 관련으로 골랐는지 (코드 문장)
    # 아래는 예전 응답은 없다. 바뀌기 바로 전 방식의 id와 이 인사이트의 숫자.
    # v3 첫 응답은 날짜 + 주제 id. insight_id에서 카테고리를 뺀 뒤에는 테넌트 + 주제 + 카테고리 id
    legacy_insight_id: str | None = None
    fact_ids: list[str] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 바뀌기 전 id 전부 (최근 것부터: 카테고리 포함 id, 날짜 + 주제 id)
    legacy_insight_ids: list[str] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 연령대별 평소 대비 배수 전체 (trend_age 결과 그대로, 설정 순서).
    # 연령대를 보지 않은 주제는 빈 목록이고 이유는 target_age_reason
    age_bands: list[AgeBandTrend] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 회사와의 연결 (코드): direct = 연결 상품 있음, domain = 도메인
    # 프로필 씨앗·하위 주제·회사 관련 키워드에서 나옴, none = 둘 다 아님 (none이면 reference_only)
    company_relevance: CompanyRelevance | None = None
    company_relevance_reason: str | None = None
    # 아래는 예전 응답은 없다 (v2 화면 칸). 이 주제 숏츠 적합도와 목표 참고값 (기회 카드와 같은
    # 계산). 숏츠 표본이 5개 미만이면 shorts_fit·target_views_reference는 None이고
    # shorts_fit_status = unknown + shorts_fit_reason
    shorts_fit: ShortsFit | None = None
    shorts_fit_status: Literal["known", "unknown"] | None = None
    shorts_fit_reason: str | None = None
    target_views_reference: float | None = Field(default=None, ge=0)
    target_reference_basis: str | None = None
    # 아래는 예전 응답은 없다 (v2 화면 칸). 평소 대비 가장 크게 오른 연령대와 그 배수
    # (age_bands 중 known만, 모르면 None + top_age_band_reason). 연령대끼리 양은 비교하지 않는다
    top_age_band: str | None = None
    top_age_band_ratio: float | None = Field(default=None, ge=0)
    top_age_band_reason: str | None = None
    # 아래는 예전 응답은 없다. 근거 요약 (평소 대비 배수가 큰 순, 최대 3개)과
    # 근거 숏츠 (썸네일·제목·조회수, 최대 3개)
    evidence_highlights: list[EvidenceHighlight] = Field(default_factory=list, max_length=3)
    evidence_shorts: list[VideoLink] = Field(default_factory=list, max_length=3)
    # 아래는 예전 응답은 없다. 형식 빌리기(company_relevance = format) 칸: 빌릴 형식, 이어 붙일 회사
    # 상품(예시 아이디어에서 이어진 것), 주의(상표·인물·음원: 이름·로고는 쓰지 말고 형식만)
    borrow_format: BorrowFormat | None = None
    borrow_products: list[InsightProduct] = Field(default_factory=list)
    cautions: list[CautionType] = Field(default_factory=list)
    caution_note: str | None = None
    # 아래는 예전 응답은 없다 (브리프 v4). 같은 insight_id로 한 캠페인의 성과 결과
    # (campaign_result), 최근 3개까지 최신순. 없으면 []. 인사이트 판정·순서는 바꾸지 않는다
    # (응답할 때 붙임)
    past_results: list[InsightPastResult] = Field(default_factory=list, max_length=3)
    # 아래는 예전 응답은 없다 (브리프 v6). 인사이트 종류: trend = 트렌드 주제(기존),
    # promo = 실제로 오르는 숏츠 형식만 빌린 캣북스 홍보 영상 아이디어 (topic은 형식 이름)
    kind: Literal["trend", "promo"] = "trend"
    promo_format: str | None = None  # promo: 빌리는 숏츠 형식
    promo_idea: str | None = None  # promo: 캣북스 홍보 아이디어 한 줄 (60자 이내)
    promo_idea_fallback: bool = False  # promo: LLM 대신 고정 틀 문장이면 true
    # promo 근거: 숏츠 전체 대비 배수 = 이 형식 1시간 조회 중앙값 ÷ 전체 숏츠 1시간 조회 중앙값
    # (같은 형식의 지난 기간 대비 상승률이 아님), 최근 N일 안에 게시된 같은 형식 숏츠 수와
    # 게시 시각을 몰라 세지 않은 수
    promo_views_per_hour_ratio: float | None = Field(default=None, ge=0)
    promo_recent_count: int | None = Field(default=None, ge=0)
    promo_recent_days: int | None = Field(default=None, ge=1)
    promo_recent_unknown: int | None = Field(default=None, ge=0)
    # promo: 분자·분모에서 뺀 하위 주제 숏츠 수 (같은 모집단으로 맞추려고)
    promo_subtopic_excluded: int | None = Field(default=None, ge=0)
    # 기획에 넘기는 기준을 통과한 근거 한 줄 (기준을 보는 인사이트만)
    gate_reason: str | None = None
    # 아래는 예전 응답은 없다 (11/18). 기획이 ReceiveInsightInput으로 옮기는 영상 카테고리·형식.
    # shorts_stats 숏츠 형식 이름 (예: 챌린지). 재무·기획 content_category로 그대로 옮긴다.
    # shorts_format_source == "topic"(이 주제 영상에서 고른 형식)일 때만 채운다
    content_category: str | None = Field(default=None, min_length=1, max_length=40)
    # finance ContentFormat과 같은 값. 데이터는 유튜브 숏츠만 보므로 shorts만 채운다
    content_format: Literal["shorts", "reels"] | None = None


class BriefGate(AxModel):
    """기획에 넘기는 기준 값 (브리프 v6, 설정·환경 변수 그대로)."""

    vs_all_ratio: float  # 숏츠 전체 대비 배수 이상이면 통과
    # 또는 네이버 검색 배수 이상 그리고 숏츠 전체 대비 naver_min_format_ratio 이상
    naver_ratio: float
    naver_min_format_ratio: float
    recent_min: int  # 그리고 최근 N일 게시된 같은 형식 숏츠 수 이상
    max_ratio: float  # 이보다 큰 배수는 근거로 치지 않음


class HeldBack(AxModel):
    """근거가 모자라 기획에 넘기지 않은 주제·형식과 이유 (브리프 v6)."""

    topic: str
    reason: str


# ------------------------------------------------ 기회 가설 카드 (trend_insight_brief 선택 칸)
# 수집 계획(source_plan)대로 모은 데이터(사용 가능·승인된 출처만)로 하위 주제마다 근거를 묶는다.
# 숫자는 모두 코드, 한 줄만 LLM(근거 숫자만, 숫자 검증). 근거마다 출처·기간·표본.

SourceDataKind = Literal["search_interest", "consumption", "viewing", "survey"]
EvidenceStatus = Literal["known", "unknown", "key_required", "not_approved"]


class OpportunityEvidence(AxModel):
    """근거 하나. 모르면 None + 이유 (0으로 채우지 않는다)."""

    source_id: str  # 출처 목록(data_sources)의 id
    kind: SourceDataKind
    status: EvidenceStatus
    label: str  # 무엇을 잰 값인지 (예: 네이버 검색 평소 대비 배수)
    value: float | None = None
    unit: str | None = None  # 배, 회 등
    period_start: dt.date | None = None
    period_end: dt.date | None = None
    samples: int | None = Field(default=None, ge=0)  # 표본 수 (영상 수·기간 점 수 등)
    reason: str | None = None  # 모르면 이유 (예: 키 등록 후 가능)


class ShortsFit(AxModel):
    """숏츠 적합도: 이 하위 주제 분류의 숏츠 조회 중앙값 ÷ 전체 숏츠 중앙값 (코드, 설정 기준)."""

    level: Literal["high", "uncertain", "low"]
    category: str | None = None  # 숏츠 통계에서 본 분류
    median_views: float | None = Field(default=None, ge=0)
    overall_median_views: float | None = Field(default=None, ge=0)
    ratio: float | None = Field(default=None, ge=0)
    samples: int = Field(default=0, ge=0)
    snapshot_at: UtcDatetime | None = None
    reason: str  # 이 등급인 이유 (기준값 포함, 코드 문장)


class OpportunityFormat(AxModel):
    name: str  # 숏츠 형식
    count: int = Field(default=0, ge=0)  # 이 분류 숏츠 중 이 형식 수
    median_views: float | None = Field(default=None, ge=0)  # 전체 숏츠 통계의 그 형식 중앙값


class DomainOpportunity(AxModel):
    opportunity_id: str = Field(min_length=1)  # 날짜 + 하위 주제 해시
    subtopic: str = Field(min_length=1)  # 하위 주제 (도메인 프로필 씨앗 키워드)
    subtopic_evidence: str | None = None  # 그 씨앗 키워드의 카탈로그·소개 근거 문장
    # 아래는 예전 응답은 없다. 하위 주제 넓히기(domain_subtopics)에서 온 카드의 축과 씨앗 키워드
    axis: str | None = None
    seed_keyword: str | None = None
    line: str  # 가설 한 줄 (LLM, 근거 숫자만. 검증 실패면 고정 문장)
    line_status: Literal["llm", "fixed"] = "fixed"
    line_reason: str | None = None
    consumption: list[OpportunityEvidence] = Field(default_factory=list)  # 실제 소비 근거
    search_interest: list[OpportunityEvidence] = Field(default_factory=list)  # 검색 관심
    age_bands: list[AgeBandTrend] = Field(default_factory=list)
    age_reason: str | None = None  # 연령대가 없으면 이유
    shorts_fit: ShortsFit | None = None
    formats: list[OpportunityFormat] = Field(default_factory=list)  # 잘 되는 형식 (많은 순)
    # 목표 참고값: 이 분류 숏츠 조회 중앙값 (없으면 전체 중앙값). 기획이 목표를 정할 때 참고만
    target_views_reference: float | None = Field(default=None, ge=0)
    target_reference_basis: str | None = None
    products: list[InsightProduct] = Field(default_factory=list)
    products_reason: str | None = None
    limits: list[str] = Field(default_factory=list)
    planning_decides: list[str] = Field(default_factory=list)  # 기획이 정할 것 (코드 고정 문장)
    score: float = 0.0  # 순서를 정한 코드 점수
    # 아래는 예전 응답은 없다. 이 카드의 숫자 (브리프 facts의 fact_id)
    fact_ids: list[str] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 근거 숏츠의 주소·썸네일
    video_links: list[VideoLink] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 회사와의 연결: 카드는 도메인 프로필에서 나오므로 늘 domain 이상
    company_relevance: CompanyRelevance | None = None
    # 아래는 예전 응답은 없다 (v2 화면 칸). TrendInsight와 같은 뜻 (age_bands에서 고름)
    top_age_band: str | None = None
    top_age_band_ratio: float | None = Field(default=None, ge=0)
    top_age_band_reason: str | None = None
    # 아래는 예전 응답은 없다. TrendInsight와 같은 뜻 (근거 요약·근거 숏츠, 각각 최대 3개)
    evidence_highlights: list[EvidenceHighlight] = Field(default_factory=list, max_length=3)
    evidence_shorts: list[VideoLink] = Field(default_factory=list, max_length=3)


class TrendInsightBriefOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    day: dt.date | None = None  # 한국 날짜
    rules_version: str = ""
    insights: list[TrendInsight] = Field(default_factory=list)
    cached: bool = False
    generated_at: UtcDatetime | None = None
    sources: list[str] = Field(default_factory=list)  # 묶은 skill 결과
    limits: list[str] = Field(default_factory=list)  # 브리프 전체 한계
    summary: str = ""
    # 아래는 예전 응답은 없다. 기회 가설 카드 (도메인 프로필 → 수집 계획대로 모은 근거)
    opportunities: list[DomainOpportunity] = Field(default_factory=list)
    opportunities_reason: str | None = None  # 비었으면 이유
    plan_id: str | None = None  # 쓴 수집 계획 id
    # 아래는 예전 응답은 없다. 응답에 나오는 숫자 목록(인사이트·카드는 fact_ids로 가리킴),
    # 응답 모양 버전, 이 브리프를 묶은 시점 id (insight_id에 날짜가 없어 시점은 여기와 generated_at)
    facts: list[DataFact] = Field(default_factory=list)
    schema_version: str = ""
    snapshot_id: str | None = None
    # 아래는 예전 응답은 없다. 지금 data LLM 상태 (오류 원인·중단). 저장분을 줄 때도 응답 시점 값
    llm_status: LlmStatus | None = None
    # 아래는 예전 응답은 없다 (v5, 11/9). 같은 날 판(자동·다시 추천)과 다시 추천 상태.
    # 문장은 서버 값
    version: int | None = Field(default=None, ge=1)  # 이 응답의 판 번호 (그날·규칙 버전 안에서)
    latest_version: int | None = Field(default=None, ge=1)  # 오늘 최신 판 번호
    version_label: str | None = None  # "00:27 자동" · "11:16 다시 추천"
    created_at: UtcDatetime | None = None  # 이 판을 저장한 시각
    trigger: BriefTrigger | None = None
    requested_by: str | None = None  # 다시 추천을 누른 사람 (표시용, 인증에 쓰지 않음)
    detect_as_of: UtcDatetime | None = None  # 이 판이 쓴 감지 데이터 시각
    versions: list[BriefVersionInfo] = Field(default_factory=list)  # 오늘 판 목록 (오래된 순)
    regen_used: int = Field(default=0, ge=0)  # 오늘 다시 추천 횟수 (자동 판·막힘·실패는 안 셈)
    regen_limit: int = Field(default=3, ge=0)
    regen_blocked: BriefRegenBlocked | None = None  # 지금 누르면 막히는 이유 (없으면 None)
    regen_job: BriefRegenJob | None = None  # 오늘 마지막 다시 추천 작업 (만드는 중·끝·실패)
    building: BriefBuilding | None = None  # 지금 누가 만드는 중이면 (자동 포함)
    diff_from_previous: BriefDiff | None = None  # 바로 앞 판 대비 주제 변화
    estimated_llm_pct: float | None = Field(default=None, ge=0)  # 다시 추천 한 번에 드는 LLM %
    estimated_llm_basis: str | None = None  # 어떻게 셌는지 (최근 다시 추천 중앙값 / "추정")
    estimated: bool = False  # 다시 추천 기록이 없어 첫 자동 브리프 토큰으로 추정했으면 true
    llm_pct_now: float | None = Field(default=None, ge=0)  # 지금 LLM 오늘 사용 %
    # 지금 있는 최신 감지 데이터 시각 (다시 추천이 쓸 것, 확인 상자 제목)
    detect_latest_at: UtcDatetime | None = None
    # 아래는 예전 응답은 없다 (v6). 기준(트렌드 근거)을 넘지 못해 기획에 넘기지 않은 것
    held_back: list[HeldBack] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 이 판에 쓴 기획에 넘기는 기준 값
    # (화면 칩이 지표마다 기준을 넘었는지 본다)
    gate: BriefGate | None = None


# auto_retry(11/19 추가): 그날 자동 판이 LLM 상한·실패로 덜 만들어졌고 예산이 풀렸을 때 그날 한 번만
# 자동으로 다시 만든 판 (화면 표시 "자동(다시)")
BriefTrigger = Literal["auto", "manual", "auto_retry"]
BriefRegenCode = Literal["daily_limit", "cooldown", "llm_budget", "in_progress", "no_new_data"]


class BriefVersionInfo(AxModel):
    version: int = Field(ge=1)
    label: str  # "00:27 자동" · "11:16 다시 추천"
    created_at: UtcDatetime
    trigger: BriefTrigger
    requested_by: str | None = None
    detect_as_of: UtcDatetime | None = None


class BriefRegenBlocked(AxModel):
    code: BriefRegenCode
    text: str  # 화면 칩 문장 (서버 값 그대로)


class BriefRegenJob(AxModel):
    job_id: str
    state: Literal["running", "done", "failed"]
    requested_by: str | None = None
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    version: int | None = Field(default=None, ge=1)  # 끝나면 새 판 번호
    tokens: int | None = Field(default=None, ge=0)  # 이 다시 추천에 든 LLM 토큰
    reason: str | None = None  # 실패 이유


class BriefBuilding(AxModel):
    who: str  # 누른 사람 또는 "자동"
    trigger: BriefTrigger
    started_at: UtcDatetime
    text: str  # "만드는 중… (시작 HH:MM)"


class BriefDiff(AxModel):
    from_version: int = Field(ge=1)
    from_label: str
    new_topics: list[str] = Field(default_factory=list)
    gone_topics: list[str] = Field(default_factory=list)
    new_count: int = Field(default=0, ge=0)
    gone_count: int = Field(default=0, ge=0)


# ------------------------------------------------ 도메인 프로필 (domain_profile)
# 입력: 회사 소개(테넌트 설정) + 회사 카탈로그(이름·카테고리·설명).
# LLM은 정해진 스키마·목록 안에서만.
# 씨앗 키워드는 카탈로그·소개에 근거가 있는 것만 (근거 문장 칸, 코드가 원문에 있는지 확인).


class DomainProfileRequest(AxModel):
    regenerate: bool = False  # 저장된 프로필을 쓰지 않고 다시 만든다


class ProductGroup(AxModel):
    name: str = Field(min_length=1)
    category_codes: list[str] = Field(default_factory=list)  # 카탈로그 카테고리 코드
    product_count: int = Field(default=0, ge=0)  # 코드가 센 상품 수


class SeedKeyword(AxModel):
    keyword: str = Field(min_length=1)
    evidence: str = Field(min_length=1)  # 근거 문장 (소개·상품 이름·카테고리·설명 원문 일부)
    evidence_from: Literal["about", "product", "category", "description", "override"]
    product_ids: list[str] = Field(default_factory=list)  # 근거가 된 상품


class DroppedKeyword(AxModel):
    keyword: str
    reason: str


class MismatchProduct(AxModel):
    product_id: str
    name: str


class ProfileEvidence(AxModel):
    """프로필 칸 하나의 근거 문장 (소개·상품 이름·카테고리 이름·설명 원문 일부, 코드가 대조)."""

    field: Literal["industry", "product_group", "customer_axis", "question"]
    value: str  # 그 칸의 값 (업종 이름·상품군 이름·고객 축·질문)
    evidence: str
    evidence_from: Literal["about", "product", "category", "description"]


class IntroMismatch(AxModel):
    """소개와 카탈로그가 어긋남: 회사 소개에 없는 상품군이 카탈로그에 있다 (경고만, 빼지 않음)."""

    group: str  # 상품군 이름
    category_codes: list[str] = Field(default_factory=list)
    products: list[MismatchProduct] = Field(default_factory=list)  # 그 상품군의 카탈로그 상품
    reason: str  # 코드 문장


class DomainProfileOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    tenant: str | None = None  # 회사 이름 (테넌트 설정)
    industry: str | None = None  # 업종 (정해진 업종 태그 중 하나)
    industry_tags: list[str] = Field(default_factory=list)  # 출처 목록 업종 태그와 맞추는 값
    product_groups: list[ProductGroup] = Field(default_factory=list)
    customer_axes: list[str] = Field(default_factory=list)  # 고객 축 (정해진 목록 중)
    # 아래는 예전 응답은 없다. 하위 주제를 나눌 축 이름
    # (고객 축에 맞춰 LLM이 정함, 사람이 나열하지 않음)
    subtopic_axes: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)  # 분석 질문
    seed_keywords: list[SeedKeyword] = Field(default_factory=list)
    dropped_keywords: list[DroppedKeyword] = Field(default_factory=list)  # 근거가 없어 버린 것
    # 아래는 예전 응답은 없다. 소개와 카탈로그가 어긋남 경고 (소개에 없는 상품군이 카탈로그에 있음)
    intro_mismatch: list[IntroMismatch] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 경고(intro_mismatch)로 씨앗에서 뺀 키워드 (카탈로그는 그대로,
    # 사람 덮어쓰기 seed_keywords에 넣으면 다시 쓴다)
    mismatch_skipped: list[DroppedKeyword] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 업종·상품군·고객 축·분석 질문의 근거 문장 (원문 대조를 통과한 것만)
    evidence: list[ProfileEvidence] = Field(default_factory=list)
    overridden: list[str] = Field(default_factory=list)  # 사람이 덮어쓴 칸 이름
    catalog_hash: str = ""  # 카탈로그·소개 해시 (바뀌면 다시 만든다)
    products_seen: int = Field(default=0, ge=0)
    cached: bool = False
    generated_at: UtcDatetime | None = None
    limits: list[str] = Field(default_factory=list)


# ------------------------------------------------ 데이터 출처 목록 (data_sources)
# 출처마다 능력을 코드에 적고, 수집 계획(LLM)이 이 목록 안에서만 고른다. 업종 태그로만 구분한다.

SourceStatus = Literal["available", "key_required"]


class DataSourcesRequest(AxModel):
    # 아래는 예전 요청은 없다. true면 확인이 필요한 출처를 저장된 결과와 상관없이
    # 지금 1번 다시 부른다 (키·이용 신청을 바꾼 뒤 사람이 누르는 "다시 확인")
    recheck: bool = False


class DataSource(AxModel):
    source_id: str = Field(min_length=1)
    name: str
    kinds: list[SourceDataKind] = Field(default_factory=list)  # 주는 데이터 종류
    industries: list[str] = Field(default_factory=list)  # 맞는 업종 태그 ("all" = 전체)
    age: bool = False  # 연령대별로 받을 수 있는지
    gender: bool = False
    categories: Literal["api", "fixed", "none"] = "none"  # 카테고리 목록을 어디서 받는지
    period: str = ""  # 자료 주기 (예: 일간, 연 1회)
    quota_daily: int | None = Field(default=None, ge=0)  # 하루 한도 (단위는 quota_unit)
    quota_unit: str = "calls"
    quota_note: str = ""  # 한도 설명 (공식 문서 기준)
    key_envs: list[str] = Field(default_factory=list)  # 필요한 키 환경변수 이름 (값은 넣지 않음)
    status: SourceStatus
    status_reason: str | None = None
    docs_url: str = ""  # 공식 문서
    approved: bool = False  # 사람이 수집을 승인했는지 (설정 파일 승인 플래그, 기존 출처는 True)


class CollectorStatus(AxModel):
    """스냅샷 수집기 하나의 상태 (YouTube·인기 검색어). 잠금 파일과 저장된 스냅샷 시각으로 본다."""

    collector_id: str = Field(min_length=1)  # youtube_collect, trending_collect
    name: str  # 화면 이름
    running: bool  # 잠금이 잡혀 있고 적힌 pid가 살아 있음
    pid: int | None = None
    started_at: UtcDatetime | None = None  # 지금 수집기가 켜진 시각
    last_collected_at: UtcDatetime | None = None  # 마지막으로 저장된 스냅샷 시각 (없으면 None)
    max_age_hours: float = Field(gt=0)  # 이보다 오래되면 수집 멈춤 (설정)
    stalled: bool  # 돌고 있지 않거나 마지막 수집이 max_age_hours보다 오래됨
    warning: str | None = None  # stalled면 "수집 멈춤: ..." 한 줄
    reason: str  # 잠금 상태 한 줄 (사람이 읽음)


class FreshTrackStatus(AxModel):
    """새 숏츠 추적 (같은 형식 비교 기준용): 오늘 쓴 YouTube 단위 / 하루 상한, 추적 중 수."""

    units_today: int = Field(default=0, ge=0)
    unit_cap: int = Field(default=2500, gt=0)
    tracking: int = Field(default=0, ge=0)  # 지금 추적 중 (게시 8일 안)
    formats: list[str] = Field(default_factory=list)  # 대상 형식 (최대 5개)
    find_stopped: bool = False  # 상한에 닿아 오늘 찾기를 멈춤 (추적은 계속)
    line: str = ""  # "새 숏츠 추적: 오늘 N유닛 / 2,500, 추적 중 N개"


class YouTubeChartFailure(AxModel):
    """영상 탐색 카테고리 인기 차트(videos.list chart=mostPopular)를 받지 못한 YouTube 카테고리.

    차트가 다시 되면 목록에서 빠진다. 그 칸은 같은 카테고리 검색(최근 3일, 하루 검색 상한 안에서
    카테고리마다 하루 1번)으로 대신 채운다.
    """

    category_id: str = Field(min_length=1)  # YouTube 카테고리 id (예: 27)
    categories: list[str] = Field(default_factory=list)  # 이 id를 쓰는 화면 카테고리
    code: str  # 실패 코드 (예: UNAVAILABLE)
    reason: str  # 실패 이유 (커넥터 문장)
    failed_at: UtcDatetime  # 마지막으로 실패한 시각
    # 대신 검색: searched = 검색함, cap = 하루 검색 상한, failed = 검색도 실패, not_run = 안 함
    fallback: Literal["searched", "cap", "failed", "not_run"] = "not_run"
    fallback_found: int | None = Field(default=None, ge=0)  # 검색으로 찾은 영상 수
    fallback_reason: str | None = None  # 검색하지 못했으면 이유
    fallback_day: dt.date | None = None  # 대신 검색 상태의 날 (한국 날짜)


class InstagramTokenExpiry(AxModel):
    """인스타 토큰 만료 (11/20 추가). 토큰 값은 담지 않는다.

    basis checked = 공식 debug_token으로 하루 한 번 읽은 만료 시각, estimated = 키 저장소에 토큰을
    넣은 시각 + 60일(추정), unknown = 둘 다 없음."""

    basis: Literal["checked", "estimated", "unknown"] = "unknown"
    expires_at: UtcDatetime | None = None  # None이면 모름 (checked인데 None이면 만료 없음)
    never_expires: bool = False  # debug_token expires_at 0
    days_left: int | None = None  # 한국 날짜 기준 남은 날 (지났으면 음수)
    checked_at: UtcDatetime | None = None  # debug_token을 읽은 시각 (checked만)
    line: str = ""  # 화면·대화 문장: "만료까지 N일(확인)" · 14일 이하면 경고 덧붙임
    warning: bool = False  # 14일 이하
    reason: str | None = None  # 확인 실패·추정 이유


class DataSourcesOutput(AxModel):
    sources: list[DataSource] = Field(default_factory=list)
    checked_at: UtcDatetime | None = None
    summary: str = ""
    # 아래는 예전 응답은 없다. 스냅샷 수집기 상태 (출처 현황에 같이 보인다)
    collectors: list[CollectorStatus] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. data 부서 LLM 오늘 사용량과 하루 상한
    llm_usage: LlmDailyUsage | None = None
    # 아래는 예전 응답은 없다 (#648). 아직 붙이지 않고 기다리는 출처 (수집 계획 후보가 아님)
    pending_sources: list[PendingSource] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. YouTube 오늘 사용량(하나의 장부)과 하루 상한
    youtube_usage: YouTubeDailyUsage | None = None
    # 아래는 예전 응답은 없다. 카테고리 인기 차트를 받지 못한 YouTube 카테고리 (영상 탐색)
    youtube_chart_failures: list[YouTubeChartFailure] = Field(default_factory=list)
    # 아래는 예전 응답은 없다. 새 숏츠 추적 상태 (수집기 상태 화면에 같이 보인다)
    fresh_track: FreshTrackStatus | None = None
    # 아래는 예전 응답은 없다 (11/20). 인스타 토큰 만료 (데이터 출처 화면 인스타 칸)
    instagram_token: InstagramTokenExpiry | None = None


class YouTubeCallerUsage(AxModel):
    """YouTube를 부른 곳 하나의 오늘 사용량 (수집기·skill)."""

    caller: str
    units: int = Field(ge=0)  # 할당량 단위 (search 100, videos·channels·categories 1)
    searches: int = Field(ge=0)  # search.list 호출 수


class YouTubeDailyUsage(AxModel):
    """YouTube Data API 오늘(한국 날짜) 사용량. 모든 YouTube 호출이 같은 장부에 적는다.

    하루 상한에 닿으면 검색(search.list, 100단위)을 멈춘다. 1단위 호출은 계속한다.
    """

    day: dt.date
    units: int = Field(ge=0)
    unit_cap: int = Field(gt=0)
    searches: int = Field(ge=0)
    search_stopped: bool  # 상한에 닿아 지금은 검색하지 않음
    by_caller: list[YouTubeCallerUsage] = Field(default_factory=list)  # 많이 쓴 순
    note: str | None = None


class PendingSource(AxModel):
    """기다리는 출처 하나 (예: 광고 데이터 — 마케팅 #647 광고 부분 대기)."""

    name: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class LlmDailyUsage(AxModel):
    """data 부서 LLM 오늘(한국 날짜) 사용량과 하루 상한. 닿으면 LLM을 부르지 않는다."""

    day: dt.date
    calls: int = Field(ge=0)  # HTTP 요청 수 (재시도·실패 포함)
    call_cap: int = Field(gt=0)
    tokens: int = Field(ge=0)  # 입력 + 출력 토큰
    token_cap: int = Field(gt=0)
    reached: bool  # 상한에 닿아 지금은 LLM을 부르지 않음
    note: str | None = None  # reached면 "LLM 하루 상한: …"
    # 아래는 예전 응답은 없다. skill·작업별 오늘 사용량 (많은 순), 남겨 둔 몫 기준과 그 상태
    by_task: list[LlmTaskUsage] = Field(default_factory=list)
    reserve_ratio: float | None = Field(default=None, ge=0, le=1)  # 이 비율부터 브리프 몫만
    reserving: bool = False  # 지금 브리프·기회 카드 몫만 남기고 나머지는 다음 날로 미룸
    # 아래는 예전 응답은 없다. LLM 오류 원인과 호출 중단 상태
    status: LlmStatus | None = None


# LLM 상태: ok, key_error = 401·403 (그날 중단), provider_limit = 429 (1시간 뒤 다시, 3번 연속이면
# 그날 중단), our_cap = 우리 하루 상한 도달, request_error = 400 (같은 요청만 그날 중단, 값 추가)
LlmState = Literal["ok", "key_error", "provider_limit", "our_cap", "request_error"]


class LlmStatus(AxModel):
    """data LLM 오류 원인과 중단 상태. 키·주소 값은 담지 않는다 (키 이름·호스트만)."""

    state: LlmState = "ok"
    tone: Literal["none", "red", "yellow", "blue"] = "none"  # 화면 색 (빨강·노랑·파랑)
    title: str | None = None  # 예: "LLM 키 오류 (401·403)"
    detail: str | None = None  # 예: "쓰는 키: DATA_LLM_API_KEY · 오늘은 호출을 멈췄습니다. …"
    # "DATA_LLM_API_KEY" · "공통 LLM_API_KEY(폴백)" · "키 없음" (이름만, 값 아님)
    key_source: str = "키 없음"
    model: str | None = None  # 모델 이름 (설정 값)
    base_host: str | None = None  # base_url의 호스트만
    halted_until: UtcDatetime | None = None  # 이 시각까지 호출하지 않음
    strikes: int = Field(default=0, ge=0)  # 연속 429 수
    calls: int = Field(default=0, ge=0)  # 오늘 호출 (우리 하루 상한 셈)
    call_cap: int = Field(default=0, ge=0)
    tokens: int = Field(default=0, ge=0)
    token_cap: int = Field(default=0, ge=0)
    # 아래는 예전 응답은 없다. request_error(400)일 때: 막힌 요청(응답 형식 이름)과 공급자 오류
    # 메시지 앞 200자 (키·토큰 모양은 가림)
    request: str | None = None
    error_message: str | None = Field(default=None, max_length=200)
    # 여러 회사 6+ (11/23, 추가만): 쓰는 키 종류. company면 key_source가
    # "<회사> 회사 키(…끝4자리)"(값 아님). 예전 응답은 없다
    key_kind: Literal["company", "server", "none"] | None = None


class LlmTaskUsage(AxModel):
    """data LLM 오늘 사용량 한 줄 (skill × 작업)."""

    skill: str  # 부른 skill (미리 계산도 skill 이름)
    task: str  # 문장·분류·계획·프로필·예시 아이디어·임베딩 등
    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)


# ------------------------------------------------ 수집 계획 (source_plan)
# 도메인 프로필 + 출처 목록 → LLM이 목록 안에서만 고르고,
# 코드가 카테고리 id·키워드·할당량을 검증한다.


class SourcePlanRequest(AxModel):
    regenerate: bool = False


class PlanCategory(AxModel):
    category_id: str
    name: str | None = None


class PlanDropped(AxModel):
    value: str  # 버린 출처 id·카테고리 id·키워드
    reason: str
    # 아래는 예전 응답은 없다. 버린 값의 종류
    kind: Literal["source", "category", "keyword", "age"] | None = None


class NotPlannedSource(AxModel):
    """출처 목록에 있지만 계획에 들어가지 않은 출처와, 등록·승인하면 얻는 근거."""

    source_id: str
    name: str
    status: SourceStatus
    approved: bool = False
    reason: str  # 계획에 없는 이유 (코드 문장)
    unlocks: str | None = None  # 등록하면 얻는 근거


class SourcePlanItem(AxModel):
    source_id: str
    status: SourceStatus
    approved: bool = False
    reason: str  # 이 출처를 고른 이유 (LLM 문장)
    categories: list[PlanCategory] = Field(default_factory=list)  # 실제 목록에 있는 것만
    category_check: Literal["verified", "after_key", "not_applicable", "unknown"] = "not_applicable"
    category_check_reason: str | None = None
    keywords: list[str] = Field(default_factory=list)  # 씨앗 키워드 중에서만
    use_age: bool = False
    calls: int = Field(default=0, ge=0)  # 코드가 센 호출 수
    quota_units: int = Field(default=0, ge=0)  # 코드가 센 할당량 단위
    dropped: list[PlanDropped] = Field(default_factory=list)
    unlocks: str | None = None  # 키 필요 출처: 이 키가 있으면 더 쓸 수 있는 근거


class PlanQuota(AxModel):
    source_id: str
    units: int = Field(default=0, ge=0)
    cap: int = Field(default=0, ge=0)  # 계획이 쓸 수 있는 상한 (회사 설정)
    unit: str = "calls"


class SourcePlanOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    plan_id: str = ""  # 프로필·출처 목록·규칙 해시
    profile_hash: str = ""
    items: list[SourcePlanItem] = Field(default_factory=list)
    dropped_sources: list[PlanDropped] = Field(default_factory=list)  # 목록 밖·중복 출처
    quota: list[PlanQuota] = Field(default_factory=list)
    trimmed: list[str] = Field(default_factory=list)  # 할당량 상한으로 자른 것
    # 아래는 예전 응답은 없다. 하위 주제 넓히기 YouTube 숏츠 검색 (YouTube 할당량 합계에 들어감)
    subtopic_searches: int = Field(default=0, ge=0)
    subtopic_units: int = Field(default=0, ge=0)
    approval_note: str = ""  # 새 출처 수집은 사람 승인 후 (설정 파일 승인 플래그)
    cached: bool = False
    generated_at: UtcDatetime | None = None
    summary: str = ""
    # 아래는 예전 응답은 없다. 계획에 없는 출처와 등록하면 얻는 근거
    not_planned: list[NotPlannedSource] = Field(default_factory=list)


# ------------------------------------------------ 하위 주제 넓히기 (domain_subtopics)
# 씨앗 키워드로 YouTube 한국 숏츠를 검색해 결과 영상 태그·제목에서 함께 나오는 말을 모으고(코드),
# LLM이 프로필의 하위 주제 축으로 분류한다.


class DomainSubtopicsRequest(AxModel):
    regenerate: bool = False  # 오늘 묶은 결과를 쓰지 않고 다시 (검색은 일주일 캐시 그대로)


class DomainSubtopic(AxModel):
    name: str = Field(min_length=1)
    seed_keyword: str  # 이 하위 주제를 찾은 씨앗 키워드
    axis: str  # 프로필 하위 주제 축 중 하나 또는 "기타"
    videos: int = Field(default=0, ge=0)  # 함께 나온 숏츠 수 (코드)
    median_views: float | None = Field(default=None, ge=0)  # 그 숏츠 조회수 중앙값
    video_ids: list[str] = Field(default_factory=list)  # 근거 영상 (최대 5)
    # 아래는 예전 응답은 없다. video_ids의 주소·썸네일 (같은 순서)
    video_links: list[VideoLink] = Field(default_factory=list)
    origins: list[Literal["tag", "title"]] = Field(default_factory=list)
    merged: list[str] = Field(default_factory=list)  # 같은 대상으로 묶은 이름


class SubtopicSearch(AxModel):
    seed_keyword: str
    status: Literal["searched", "cached", "skipped", "failed"]
    searched_at: UtcDatetime | None = None
    shorts: int = Field(default=0, ge=0)  # 받은 숏츠 수
    reason: str | None = None


class DomainSubtopicsOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    axes: list[str] = Field(default_factory=list)  # 프로필 하위 주제 축 + 기타
    subtopics: list[DomainSubtopic] = Field(default_factory=list)
    searches: list[SubtopicSearch] = Field(default_factory=list)
    dropped_common: list[str] = Field(default_factory=list)  # 흔한 말로 뺀 이름 (문서 빈도)
    quota_used: int = Field(default=0, ge=0)  # 이번에 쓴 YouTube 단위
    cache_days: int = Field(default=7, ge=0)
    daily_searches: int = Field(default=5, ge=0)
    generated_at: UtcDatetime | None = None
    limits: list[str] = Field(default_factory=list)
    summary: str = ""


# ------------------------------------------------ video_performance (게시한 영상 성과)
# 게시한 영상을 추적 목록에 넣어 게시 후 1시간·1일·3일·7일 시점의 조회수·좋아요·댓글을 저장하고,
# 목표 참고값(인사이트 target_views_reference)·같은 카테고리·형식 숏츠 중앙값 대비 배수를 낸다.
# 숫자는 모두 코드, facts에. 아직 그 시점이 안 됐으면 unknown + "측정 중".
# 인스타그램·틱톡은 아직 읽는 길이 없어 unknown + "연결 대기"와 필요한 것.

VideoPlatform = Literal["youtube", "instagram", "tiktok"]
VideoCheckpointName = Literal["1h", "1d", "3d", "7d"]


class VideoPerformanceRequest(AxModel):
    """campaign_id 또는 video_url 중 하나는 있어야 한다. video_url이 있으면 등록(추적 목록)한다."""

    campaign_id: str | None = Field(default=None, min_length=1, max_length=120)
    video_url: str | None = Field(default=None, min_length=1, max_length=500)
    insight_id: str | None = Field(default=None, max_length=80)  # 이 영상을 낸 인사이트
    proposal_id: str | None = Field(default=None, max_length=80)
    test: bool = False  # 시험 영상: 진짜 캠페인과 섞지 않는다 (화면·브리프 집계에서 뺌)
    # 아래는 예전 요청은 없다 (v3). 등록된 영상 전부 (성과 탭). 새로 재지 않고 저장분만 읽는다
    list_all: bool = False
    # 아래는 예전 요청은 없다 (v4). 이 영상의 매출 연결: 프로모션 링크 꼬리표(유입 표시)와 쿠폰.
    # 둘 다 없으면 이 영상의 매출 효과는 "측정 불가: 프로모션 링크/쿠폰 없음"
    promo_id: str | None = Field(default=None, min_length=1, max_length=120)
    coupon_id: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def _one_of(self) -> VideoPerformanceRequest:
        if not self.campaign_id and not self.video_url and not self.list_all:
            raise ValueError("campaign_id 또는 video_url이 있어야 한다 (전부 보기는 list_all)")
        return self


class VideoCheckpoint(AxModel):
    """게시 후 한 시점의 값. 모르면 None + 이유 (0으로 채우지 않는다)."""

    checkpoint: VideoCheckpointName
    due_at: UtcDatetime | None = None  # 게시 시각 + 시점
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None  # 측정 중 · 놓침 · 연결 대기 등
    measured_at: UtcDatetime | None = None
    hours_after: float | None = Field(default=None, ge=0)  # 게시 후 실제로 잰 시간
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)  # 숨김이면 None
    comments: int | None = Field(default=None, ge=0)
    views_per_hour: float | None = Field(default=None, ge=0)  # 조회 ÷ 게시 후 시간
    like_rate: float | None = Field(default=None, ge=0)  # 좋아요 ÷ 조회
    comment_rate: float | None = Field(default=None, ge=0)  # 댓글 ÷ 조회
    # 조회 ÷ 목표 참고값. v2부터 목표 시점(출력의 target_checkpoint, 7d)에만 있고 다른 시점은 None
    target_ratio: float | None = Field(default=None, ge=0)
    peer_ratio: float | None = Field(default=None, ge=0)  # 조회 ÷ 같은 카테고리·형식 숏츠 중앙값
    fact_ids: list[str] = Field(default_factory=list)
    # 아래는 예전 응답은 없다 (v2). 화면 한 줄 (예: "1일 · 33,263회 · 같은 형식 대비 16.63배")
    line: str | None = None
    # 아래는 예전 응답은 없다 (v3). 같은 형식 숏츠의 게시 후 같은 시간대(이 시점 창) 조회 중앙값
    # (시간 맞춤). 표본이 5개 미만이면 배수 없이 baseline_reason ("기준 없음(표본 n)").
    # 위 peer_ratio(지금 조회수 중앙값 대비, 시간 안 맞춤)는 칸만 남기고 화면에서 쓰지 않는다
    baseline_basis: Literal["age_matched"] | None = None
    baseline_views: float | None = Field(default=None, ge=0)
    baseline_n: int | None = Field(default=None, ge=0)
    baseline_reason: str | None = None
    peer_age_ratio: float | None = Field(default=None, ge=0)  # 조회 ÷ baseline_views


class VideoPerformanceGroup(AxModel):
    """성과 모으기 표 한 줄 (인사이트·형식·카테고리별). 시험 영상은 뺀다.

    v2부터 목표 시점(7d) 값만 모은다 (performance_by_horizon의 7d 줄과 같음). 그 전에는 영상마다
    가장 늦게 잰 시점 값을 섞었다.
    """

    key: Literal["insight", "format", "category"]
    value: str
    videos: int = Field(ge=0)  # 등록된 영상 수
    measured: int = Field(ge=0)  # 목표 시점 목표 대비 배수가 있는 영상 수
    median_target_ratio: float | None = Field(default=None, ge=0)  # 목표 시점(7d) 값의 중앙값


class VideoPerformanceHorizonGroup(AxModel):
    """성과 모으기 한 줄: 묶음 하나의 시점 하나 (1d·3d·7d). 시험 영상은 뺀다.

    그 시점을 잰 영상만 넣는다. 측정 중·놓침·게시 시각 모름은 빼고 빠진 수로 센다.
    """

    key: Literal["insight", "format", "category"]
    value: str
    checkpoint: VideoCheckpointName
    videos: int = Field(ge=0)  # 묶음의 영상 수
    n: int = Field(ge=0)  # 집계에 들어간 영상 수 (그 시점을 잰 영상)
    excluded: int = Field(ge=0)  # 빠진 수 (videos − n)
    excluded_measuring: int = Field(default=0, ge=0)  # 그 시점이 아직 (측정 중)
    excluded_missed: int = Field(default=0, ge=0)  # 그 시점을 놓침·게시 시각 모름 등
    low_sample: bool = True  # n < 3
    median_views: float | None = Field(default=None, ge=0)
    median_peer_ratio: float | None = Field(default=None, ge=0)  # 기준값이 있는 영상만
    peer_n: int = Field(default=0, ge=0)  # 같은 숏츠 대비가 있는 영상 수
    median_target_ratio: float | None = Field(default=None, ge=0)  # 목표 시점(7d)에만
    target_n: int = Field(default=0, ge=0)  # 목표 대비가 있는 영상 수


# 판정 (v3): 7일 조회수 ÷ 목표. achieved = 1배 이상, missed = 미만, measuring = 7일이 아직,
# excluded = 시험 영상·목표 없음·7일 놓침·연결 대기 (집계에서 뺌)
VideoVerdict = Literal["achieved", "missed", "measuring", "excluded"]
# 신호 등급: 같은 형식 숏츠(시간 맞춤) 중앙값의 80% 이상 strong, 50~80% normal, 50% 미만 weak,
# 기준 표본 5개 미만·값 없음 = unknown (판단 불가)
SignalLevel = Literal["strong", "normal", "weak", "unknown"]


class VideoSignal(AxModel):
    """성과 요인 신호 하나 (코드 계산). 비교는 같은 형식 숏츠의 시간 맞춤 중앙값."""

    value: float | None = None  # 우리 영상 값 (first는 1시간 조회 ÷ 목표, 나머지는 비율)
    display: str | None = None  # 화면 값 ("49%", "6.0%", "0.80%", "+64%")
    compared: float | None = None  # 등급에 쓴 우리 값 (first는 1시간 조회수)
    baseline: float | None = None  # 같은 형식 숏츠 중앙값 (같은 시점 창)
    baseline_n: int = Field(default=0, ge=0)
    checkpoint: VideoCheckpointName | None = None  # 어느 시점 값인지
    level: SignalLevel = "unknown"
    reason: str | None = None  # unknown이면 이유
    # 아래는 예전 응답은 없다 (v5). 같은 형식 기준 표본이 5개 미만이라 판단 불가 = 기준 모으는 중
    # (reason "기준 모으는 중 (n/5)")
    collecting: bool = False


class BaselineProgress(AxModel):
    """같은 형식 비교 기준 모으는 중: 형식·시점마다 새 숏츠 추적 표본 수 (5개면 등급이 켜짐)."""

    shorts_format: str
    checkpoint: VideoCheckpointName
    n: int = Field(ge=0)
    need: int = Field(default=5, gt=0)


class VideoSignals(AxModel):
    first: VideoSignal = Field(default_factory=VideoSignal)  # 첫 1시간 속도
    like: VideoSignal = Field(default_factory=VideoSignal)  # 좋아요율 (마지막으로 잰 시점)
    cmt: VideoSignal = Field(default_factory=VideoSignal)  # 댓글율 (마지막으로 잰 시점)
    grow: VideoSignal = Field(default_factory=VideoSignal)  # 1일 이후 성장 = 3d ÷ 1d − 1


class VideoProjection(AxModel):
    """7일 예상치 (측정 중일 때만): 3일 조회 × 같은 형식 숏츠의 7일/3일 중앙값. 늘 예상치."""

    views: int | None = Field(default=None, ge=0)
    ratio: float | None = Field(default=None, ge=0)  # 예상 ÷ 목표
    is_estimate: bool = True
    multiplier: float | None = Field(default=None, ge=0)  # 같은 형식 7일/3일 중앙값
    baseline_n: int = Field(default=0, ge=0)
    reason: str | None = None  # 못 내면 이유


class VideoChip(AxModel):
    """인사이트 카드의 영상 칩."""

    video_id: str
    name: str
    verdict: VideoVerdict
    target_ratio: float | None = Field(default=None, ge=0)  # 7일 목표 대비 (측정 중이면 None)
    projected_ratio: float | None = Field(default=None, ge=0)  # 측정 중이면 예상치 (예상치 표기)


class VideoInsightCard(AxModel):
    """인사이트별로 보면 (인사이트 묶음만, 시험 영상 제외)."""

    insight_id: str
    topic: str | None = None
    finished: int = Field(default=0, ge=0)  # 7일 판정이 난 영상 (달성 + 미달)
    achieved: int = Field(default=0, ge=0)
    missed: int = Field(default=0, ge=0)
    measuring: int = Field(default=0, ge=0)
    line: str = ""  # "끝난 N개 중 M개 달성 · K개 측정 중"
    chips: list[VideoChip] = Field(default_factory=list)
    median_target_ratio: float | None = Field(default=None, ge=0)  # 끝난 영상 3개 이상일 때만
    notes: list[str] = Field(default_factory=list)  # 결론 이름·결과 갈림 문구


class VideoVerdictTally(AxModel):
    """요약 타일: 영상 수(시험 영상 제외)와 판정별 수."""

    videos: int = Field(default=0, ge=0)
    achieved: int = Field(default=0, ge=0)
    missed: int = Field(default=0, ge=0)
    measuring: int = Field(default=0, ge=0)
    excluded: int = Field(default=0, ge=0)


class VideoPerformanceOutput(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    platform: VideoPlatform | None = None
    video_id: str | None = None
    video_url: str | None = None
    campaign_id: str | None = None
    insight_id: str | None = None
    proposal_id: str | None = None
    test: bool = False
    title: str | None = None
    published_at: UtcDatetime | None = None
    tracking: bool = False  # 추적 목록에 있음 (시점마다 잰다)
    checkpoints: list[VideoCheckpoint] = Field(default_factory=list)
    target_views_reference: float | None = Field(default=None, ge=0)
    target_reference_basis: str | None = None  # 어느 인사이트의 목표 참고값인지
    category: str | None = None  # 비교에 쓴 카테고리 (인사이트에서)
    shorts_format: str | None = None  # 비교에 쓴 숏츠 형식 (인사이트에서)
    peer_median_views: float | None = Field(default=None, ge=0)
    peer_basis: str | None = None  # 어느 숏츠 통계 중앙값인지 (모르면 이유)
    needs: list[str] = Field(default_factory=list)  # 연결 대기면 필요한 것 (계정 토큰 등)
    facts: list[DataFact] = Field(default_factory=list)
    performance_table: list[VideoPerformanceGroup] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    summary: str = ""
    schema_version: str = ""
    # 아래는 예전 응답은 없다 (v2). 목표 참고값이 정의된 시점과 시점별 성과 모으기
    target_checkpoint: VideoCheckpointName | None = None
    performance_by_horizon: list[VideoPerformanceHorizonGroup] = Field(default_factory=list)
    # 아래는 예전 응답은 없다 (v3). 판정·성과 요인·개선 방향 (규칙 기반, LLM 안 씀)
    topic: str | None = None  # 인사이트 주제 (화면 "책추천 · 리뷰 · 목표 5,000회")
    baseline_basis: Literal["age_matched"] | None = None  # 같은 형식 비교 기준
    verdict: VideoVerdict | None = None
    verdict_reason: str | None = None
    signals: VideoSignals | None = None
    factors: list[str] = Field(default_factory=list)  # 성과 요인 문장 (숫자 포함)
    actions: list[str] = Field(default_factory=list)  # 개선 방향 문장
    projection: VideoProjection | None = None
    # list_all 응답: 영상마다 이 모양 (영상 칸만 채움), 요약 타일, 인사이트 카드
    videos: list[VideoPerformanceOutput] = Field(default_factory=list)
    tally: VideoVerdictTally | None = None
    insight_cards: list[VideoInsightCard] = Field(default_factory=list)
    # 아래는 예전 응답은 없다 (v4). 이 영상의 매출 연결 (등록 때 받은 값)과 매출 측정 가능 여부.
    # 프로모션 링크·쿠폰이 둘 다 없으면 sales_measurable False와 그 이유 (측정 불가)
    promo_id: str | None = None
    coupon_id: str | None = None
    sales_measurable: bool | None = None
    sales_reason: str | None = None
    # 아래는 예전 응답은 없다 (v5, list_all). 같은 형식 비교 기준 모으는 중 (대상 형식 × 시점)과
    # 모든 칸이 5개를 넘었는지 (넘으면 화면에서 이 칸을 숨김)
    baseline_progress: list[BaselineProgress] = Field(default_factory=list)
    baseline_ready: bool = False


# ------------------------------------------------ campaign_result (성과 결과 1건, campaign_id 기준)
# 영상 성과(video_performance)와 매출 효과(campaign_effect 계산 그대로)를 캠페인 하나로 묶는다.
# 매출 판정은 기존 증분(판매액)을 기준선 날들의 흔들림 범위와 비교한 것뿐이다 (계산은 바꾸지 않음).
# 네 칸(귀속·상품·쿠폰·전체 매장)은 같은 주문이 겹쳐 따로 계산·표시하고 더하지 않는다.
# 만들지 않는 것: 조회와 매출을 합친 점수, 수익·ROI(재무 몫), 캠페인 자동 중단.

# 매출 판정: more = 더 팔림 (범위 위), less = 덜 팔림 (범위 아래), no_effect = 효과 확인 안 됨
# (범위 안, 또는 표본이 적어 단정하지 않음)
CampaignSalesVerdictName = Literal["more", "less", "no_effect"]
CampaignResultView = Literal["planning", "marketing", "finance"]
CampaignResultSection = Literal["link", "videos", "sales", "advice", "alerts"]


class CampaignSalesVerdict(AxModel):
    """매출 효과 판정 (코드). 판매액 증분이 기준선 날들의 흔들림 범위 안·위·아래인지."""

    status: CampaignStatus = "unknown"
    verdict: CampaignSalesVerdictName | None = None
    # "더 팔림" · "덜 팔림" · "효과 확인 안 됨" · "효과 확인 안 됨(표본 적음)" · "측정 불가: …"
    label: str
    low_sample: bool = False
    low_sample_reason: str | None = None
    reason: str | None = None  # 판정 근거 한 줄 (범위·기준선 날 수)
    # 기존 계산 그대로 (마이너스도 그대로, 0으로 바꾸지 않음)
    incremental_gross_krw: int | None = None
    incremental_low_krw: int | None = None
    incremental_high_krw: int | None = None
    incremental_orders: int | None = None
    baseline_days: int = Field(default=0, ge=0)
    baseline_rule: str | None = None


CampaignSalesCellKey = Literal["attribution", "products", "coupons", "store"]


class CampaignSalesCell(AxModel):
    """매출 네 칸 중 하나 (따로 계산·표시, 더하지 않음). 값은 campaign_effect 그대로."""

    key: CampaignSalesCellKey
    label: str  # 귀속(쿠폰·링크로 온 주문) · 상품(캠페인 대상 책) · 쿠폰 · 전체 매장
    status: CampaignStatus = "unknown"
    reason: str | None = None  # unknown이면 이유
    line: str | None = None  # 화면 한 줄 (숫자는 아래 칸 그대로)
    orders: int | None = None  # 귀속: 링크로 들어온 주문 수
    gross_krw: int | None = None  # 귀속: 링크로 들어온 주문 판매액
    coupon_orders: int | None = None  # 귀속: 캠페인 쿠폰을 쓴 주문 수
    coupon_gross_krw: int | None = None  # 귀속: 캠페인 쿠폰을 쓴 주문 판매액
    incremental_gross_krw: int | None = None  # 상품·전체 매장: 증분 판매액 (마이너스 그대로)
    incremental_low_krw: int | None = None
    incremental_high_krw: int | None = None
    incremental_orders: int | None = None  # 전체 매장: 증분 주문 수 (마이너스 그대로)
    use_rate: float | None = Field(default=None, ge=0, le=1)  # 쿠폰 누적 사용률
    direction: Literal["up", "down", "uncertain"] | None = None


class CampaignAlert(AxModel):
    """알림: loss = 매출이 평소보다 줄었음 (덜 팔림), data_gap = 수집·기록 공백."""

    kind: Literal["loss", "data_gap"]
    message: str
    note: str | None = None  # 예: "기획·재무 확인 필요 · 캠페인을 멈출지는 사람이 정합니다"


class CampaignSalesEffect(AxModel):
    """③ 매출 효과: 판정 + 네 칸 + 영상별 측정 가능 수 + 조회·매출 판정이 반대일 때 한 줄."""

    status: CampaignStatus = "unknown"
    reason: str | None = None
    verdict: CampaignSalesVerdict | None = None
    headline: str | None = None  # "+1,200,000원 · 주문 +40건 (4주 기준선 대비)"
    cells: list[CampaignSalesCell] = Field(default_factory=list)  # 늘 네 칸, 순서 고정
    videos_total: int = Field(default=0, ge=0)  # 이 캠페인 영상 (시험 영상 제외)
    videos_measurable: int = Field(default=0, ge=0)  # 그중 프로모션 링크·쿠폰이 있는 영상
    measurable_line: str = ""  # "영상 N개 중 매출 측정 가능 M개"
    notes: list[str] = Field(default_factory=list)  # 조회 판정과 매출 판정이 반대일 때 등


class CampaignVideoAdvice(AxModel):
    """④ 영상마다 성과 요인·개선 방향 (video_performance 문장 그대로)."""

    video_id: str
    name: str
    verdict: VideoVerdict | None = None
    factors: list[str] = Field(default_factory=list)  # 마케팅 묶음에서는 비움 (개선 방향만)
    actions: list[str] = Field(default_factory=list)


class CampaignFinanceRecord(AxModel):
    """재무 기록용 값 (v2, 재무 묶음): finance `CampaignResultInput` 중 데이터가 아는 값만.

    집행액(spend_krw)·캠페인 이름·채널·쿠폰 금액 칸은 없다. 마케팅이 채워 재무에 보낸다
    (데이터는 집행액을 모른다). 모르는 값은 None (0으로 채우지 않는다).
    """

    campaign_id: str
    period_from: str | None = None  # 측정 기간 (첫 게시 ~ 7일 뒤)의 한국 날짜 YYYY-MM-DD
    period_to: str | None = None
    gmv_krw: int | None = Field(default=None, ge=0)  # max(증가 매출, 0)
    orders: int | None = Field(default=None, ge=0)  # max(증가 주문, 0)
    measured_gmv_krw: int | None = None  # 잰 증가 매출 그대로 (음수 가능)
    measured_orders: int | None = None  # 잰 증가 주문 그대로 (음수 가능)
    effect_generated_at: str | None = None  # campaign_effect의 generated_at
    source: str = "data.campaign_result"


# 기획에 결과 보내기 상태 (data → planning receive_campaign_update, 캠페인당 1번):
# sent 보냄 · pending 보낼 예정(7일 측정 끝난 뒤) · not_sent 보내지 않음(이유) ·
# failed 보내기 실패(다시 시도 중, 한국 날짜 하루 3번) · off 자동 보내기 꺼짐(기능 플래그)
CampaignSendState = Literal["sent", "pending", "not_sent", "failed", "off"]


class CampaignSendStatus(AxModel):
    """기획에 결과 보내기 상태 (v2). 화면은 chip·line을 그대로 쓴다 (문장·시각은 서버 값)."""

    state: CampaignSendState
    chip: str  # 기획에 보냄 · 기획에 보낼 예정 · 보내지 않음 · 보내기 실패 · 다시 시도 중 (n/3) …
    # PROP-3 · "늘었음" · 11월 12일 14:05 / 7일 측정이 끝나는 … 이후 자동으로 보냅니다
    line: str = ""
    proposal_id: str | None = None
    direction: str | None = None  # 보낸 direction (늘었음·같음·줄었음·확인 불가)
    sent_at: UtcDatetime | None = None
    send_after: UtcDatetime | None = None  # 대기면 이 시각 이후 보냄
    failures: int = Field(default=0, ge=0)  # 오늘(한국 날짜) 실패 횟수
    retry_limit: int = Field(default=3, ge=0)
    reason: str | None = None  # 보내지 않음·실패 이유


class CampaignResultRequest(AxModel):
    """성과 결과 1건. view로 부서별 묶음을 고른다 (skill은 하나).

    planning = ①~⑤ 전부, marketing = ① 연결 + ④ 개선 방향만, finance = ① + ③ 매출 + ⑤ 알림.
    """

    campaign_id: str = Field(min_length=1, max_length=120)
    view: CampaignResultView = "planning"


class CampaignResultOutput(AxModel):
    status: CampaignStatus = "unknown"
    reason: str | None = None
    view: CampaignResultView = "planning"
    sections: list[CampaignResultSection] = Field(default_factory=list)  # 이 묶음에 든 칸
    # ① 연결
    campaign_id: str
    campaign_name: str | None = None  # 인사이트 주제 (없으면 campaign_id)
    insight_id: str | None = None
    proposal_id: str | None = None
    window_from: UtcDatetime | None = None  # 측정 기간 (첫 게시 ~ 7일 뒤 또는 지금)
    window_to: UtcDatetime | None = None
    test: bool = False  # 시험 캠페인 (영상이 모두 시험 영상)
    # ② 영상 성과 (video_performance 결과 그대로)
    videos: list[VideoPerformanceOutput] = Field(default_factory=list)
    # ③ 매출 효과
    sales: CampaignSalesEffect | None = None
    # ④ 성과 요인 · 개선 방향 · 한계 문구
    advice: list[CampaignVideoAdvice] = Field(default_factory=list)
    limits: list[str] = Field(default_factory=list)
    # ⑤ 알림
    alerts: list[CampaignAlert] = Field(default_factory=list)
    summary: str = ""
    schema_version: str = ""
    # 아래는 예전 응답은 없다 (v2). 재무 기록용 값(재무 묶음만)과 기획에 결과 보내기 상태(모든 묶음)
    finance_record: CampaignFinanceRecord | None = None
    send_status: CampaignSendStatus | None = None


# 인사이트 카드 "지난번 결과" 띠 색: green = 조회 달성 + 매출 더 팔림, red = 매출 덜 팔림이거나
# 알림 loss (조회 판정과 상관없이), gray = 그 밖 (미달·확인 안 됨·측정 중)
PastBand = Literal["green", "red", "gray"]


class InsightPastResult(AxModel):
    """인사이트의 지난번 캠페인 결과 하나 (campaign_result 값 그대로 + 띠 문장). 기획이 받는다.

    조회와 매출을 합친 점수는 없다. 측정 중이면 "측정 중"만 (예상치 없음).
    views는 영상 조회 중앙값이다 (합계가 아님, 영상 수에 따라 커지지 않게).
    """

    campaign_id: str
    campaign_name: str | None = None
    window_from: UtcDatetime | None = None  # 측정 기간
    window_to: UtcDatetime | None = None
    # 조회 판정: 끝난 영상(7일)의 목표 대비 배수 중앙값이 1 이상이면 achieved, 미만 missed,
    # 끝난 영상이 없으면 measuring, 판정할 영상이 없으면 none
    views_verdict: Literal["achieved", "missed", "measuring", "none"] = "none"
    views_label: str  # "조회 달성" · "조회 미달" · "측정 중" · "조회 판정 없음"
    target_ratio: float | None = Field(default=None, ge=0)  # 끝난 영상 목표 대비 중앙값
    videos_total: int = Field(default=0, ge=0)  # 시험 영상 제외
    videos_achieved: int = Field(default=0, ge=0)
    # 매출 판정 (campaign_result 그대로)
    sales_verdict: CampaignSalesVerdictName | None = None
    sales_label: str  # "더 팔림" · "덜 팔림" · "효과 확인 안 됨(표본 적음)" · "측정 불가: …"
    sales_low_sample: bool = False
    incremental_gross_krw: int | None = None  # 마이너스 그대로
    incremental_orders: int | None = None
    actions: list[str] = Field(default_factory=list)  # 개선 방향 문장 그대로
    alerts: list[CampaignAlert] = Field(default_factory=list)  # loss · data_gap
    # 띠 (서버 문장, 화면은 계산하지 않음)
    band: PastBand = "gray"
    summary_line: str = ""  # "조회 목표의 N배 (영상 M개 중 K개 달성) · 매출 +N원 · 주문 +N건"
    note_line: str | None = None  # "지난번 개선 방향: …" 또는 조회·매출 반대 문구
    # 아래는 예전 응답은 없다. 캠페인 영상(시험 제외)의 게시 7일 조회 중앙값과 그 근거
    views: int | None = Field(default=None, ge=0)  # 캠페인 영상(시험 제외)의 게시 7일 조회 중앙값
    views_at: UtcDatetime | None = None  # 그 7일 조회를 잰 시각 중 가장 늦은 시각
    views_checkpoint: Literal["7d"] | None = None  # 값이 있을 때만 "7d"
    views_videos: int = Field(default=0, ge=0)  # 중앙값에 쓴 영상 수
    views_reason: str | None = None  # 값이 없을 때 이유


# ---------------------------------------------------------------- 데이터분석 AI 직접 대화 (11/9)
# dashboard-api `/api/dashboard/data-chat` → data `data_chat_*`. 기획 AI 대화와 같은 모양.
# `who`는 dashboard-api가 사람 토큰에서만 채운다. 사람 말은 LLM 입력의 데이터 블록(untrusted)에만.
# 대화(thread)는 id·title·status(talking·answering·closed)·who·summary·messages[]
# ·task_id·restricted·message_count·created_at·updated_at.
# 조회·설명만 하고 업무 상태를 바꾸지 않아 execute()를 부르지 않는다
# (대화 기록 저장은 업무 상태가 아님).
DATA_CHAT_SCHEMA_VERSION = "data_chat.v1"
DATA_CHAT_MESSAGE_MAX = 2000


class DataChatSendInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=DATA_CHAT_MESSAGE_MAX)


class DataChatListInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=50, ge=1, le=100)


class DataChatRef(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str = Field(min_length=1, max_length=64)


class DataChatThreadOutput(AxModel):
    thread: dict[str, Any]
    schema_version: str = DATA_CHAT_SCHEMA_VERSION


class DataChatListOutput(AxModel):
    threads: list[dict[str, Any]]
    schema_version: str = DATA_CHAT_SCHEMA_VERSION


# ---------------------------------------------------------------- sales_analysis (매출 분석, 11/16)
# data가 쌓은 표준 판매 저장소(sales_orders 등)만 읽는다. 판매 서버를 직접 읽지 않는다. LLM 없음,
# 회사 상태를 바꾸지 않는다(grade read). 금액은 모두 정수 원.
#
# - 기간: today = 한국 시간 오늘 0시 ~ 기준 시각, 7d·30d = 한국 시간 6일·29일 전 0시 ~ 기준 시각.
#   직전 기간 = 같은 길이로 하루 단위만큼 앞(오늘 → 어제 0시 ~ 어제 같은 시각, 7d → 7일 앞)
# - 매출 = 결제 완료 주문의 상품 금액 합 − 쿠폰 할인 − 포인트 사용 − 기간 안 환불 (배송비 제외).
#   주문은 접수 시각, 환불은 환불 시각으로 기간을 정한다
# - 객단가 = 매출 ÷ 결제 완료 주문 수 (반올림)
# - 재구매 고객 = 이 기간 이전에도 결제 완료 주문이 있는 buyer_id
# - 품질 관문에 걸리면 status "insufficient"이고 숫자 칸(kpis·categories·…)은 비운다
SalesPeriod = Literal["today", "7d", "30d"]
# 추가만 (11/25): cancel_count 기간에 접수해 취소된 주문 수, refund_amount 기간에 일어난 환불 합(원)
SalesKpiKey = Literal[
    "revenue", "order_count", "aov", "repeat_rate", "cancel_count", "refund_amount"
]


class SalesAnalysisInput(AxModel):
    period: SalesPeriod = "7d"
    tenant_id: str = TENANT_ID_DEFAULT


class SalesKpi(AxModel):
    """위쪽 숫자 하나. 금액·건은 정수, 비율은 0~1."""

    key: SalesKpiKey
    label: str
    unit: Literal["won", "count", "rate"]
    value: int | float | None = None
    previous: int | float | None = None
    # 직전 대비 변화. 금액·건은 변화율(0.062 = 6.2%), 비율은 차이(0.018 = 1.8%p).
    # 직전 값이 0이거나 없으면 None
    change: float | None = None
    change_kind: Literal["ratio", "points"] = "ratio"


class SalesCategoryRow(AxModel):
    code: str  # 원본 category_code (화면에는 label만)
    label: str  # 카탈로그 이름. 없으면 "이름 없는 분류"
    revenue: int = 0
    share: float | None = Field(default=None, ge=0, le=1)  # 기간 매출 중 비중. 매출이 0 이하면 None
    previous_revenue: int = 0
    change: float | None = None  # 변화율. 직전이 0 이하면 None


class SalesProductMove(AxModel):
    product_id: str
    label: str  # 상품 이름 (없으면 "이름 없는 상품")
    category_label: str | None = None
    revenue: int = 0
    previous_revenue: int = 0
    change: float | None = None  # 매출 변화율
    orders: int = Field(default=0, ge=0)  # 이 상품이 든 결제 완료 주문 수
    previous_orders: int = Field(default=0, ge=0)


class SalesCustomerMix(AxModel):
    buyers: int = Field(default=0, ge=0)  # 기간 안 결제 완료 주문이 있는 구매자 수
    repeat_buyers: int = Field(default=0, ge=0)
    new_revenue: int = 0
    repeat_revenue: int = 0
    new_share: float | None = Field(default=None, ge=0, le=1)  # 매출이 0 이하면 None
    repeat_share: float | None = Field(default=None, ge=0, le=1)
    coupon_orders: int = Field(default=0, ge=0)  # 쿠폰 할인이 있는 결제 완료 주문 수
    coupon_order_rate: float | None = Field(default=None, ge=0, le=1)


class SalesChannelRow(AxModel):
    source: str | None = None  # 원본 src_source (화면에는 label만)
    medium: str | None = None
    label: str  # "네이버 · 검색 광고", 표시가 없으면 "직접 유입"
    revenue: int = 0
    orders: int = Field(default=0, ge=0)
    share: float | None = Field(default=None, ge=0, le=1)


class SalesQuality(AxModel):
    """품질 보고. 관문에 걸려도 채운다."""

    rows_in_period: int = Field(default=0, ge=0)  # 기간 안 주문 행 (상태 무관, 버린 행 제외)
    # 주문이 하나도 없는 날 (한국 날짜 YYYY-MM-DD)
    empty_days: list[str] = Field(default_factory=list)
    dropped_rows: int = Field(default=0, ge=0)  # 정제에서 버린 행 (접수 시각이 기간 안인 주문)
    dropped_reasons: dict[str, int] = Field(default_factory=dict)  # 이유(한국어) → 행 수
    last_synced_at: UtcDatetime | None = None  # 마지막으로 수집에 성공한 시각
    unknown_category_codes: list[str] = Field(default_factory=list)  # 카탈로그에 이름이 없는 코드
    # 카탈로그 genres에 없는 genre_code (11/16, 고객·시장 분석)
    unknown_genres: list[str] = Field(default_factory=list)
    # 카탈로그 attributes에 없는 고객 속성 값: 속성 key → 매핑 안 된 값 code 목록 (11/16)
    unmapped_attribute_values: dict[str, list[str]] = Field(default_factory=dict)


class SalesBasis(AxModel):
    """근거: 기간, 읽은 행 수, 마지막 수집 시각, 카탈로그 판."""

    period_from: UtcDatetime
    period_to: UtcDatetime
    previous_from: UtcDatetime
    previous_to: UtcDatetime
    rows_read: int = Field(default=0, ge=0)
    last_synced_at: UtcDatetime | None = None
    catalog_id: str
    catalog_version: str | None = None
    # 저장소를 채운 출처가 실제 판매 서버가 아닐 때의 표시 (11/16). 가짜 출처(데모 전용
    # `DATA_SALES_SOURCE=fake`)로 채웠으면 "가짜 데이터(데모용)". 실제 판매 서버면 None
    data_source: str | None = None
    # 결과 캐시 (11/17): 이 답을 계산한 시각, 그리고 판매 기록이 바뀌기 전 예전 결과를 먼저 준
    # 것인지(stale, 새 계산은 뒤에서 돌고 있다). 캐시를 거치지 않은 답은 None·False
    computed_at: UtcDatetime | None = None
    stale: bool = False


class SalesAnalysisOutput(AxModel):
    period: SalesPeriod
    as_of: UtcDatetime  # 기준 시각 (= 기간 끝)
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)  # "데이터 부족: …" 이유 문장
    definition: str = ""  # 숫자 정의 한 줄 (카탈로그 revenue_definition)
    kpis: list[SalesKpi] = Field(default_factory=list)
    categories: list[SalesCategoryRow] = Field(default_factory=list)
    products_up: list[SalesProductMove] = Field(default_factory=list)  # 많이 늘어난 상품 (최대 5)
    products_down: list[SalesProductMove] = Field(default_factory=list)  # 많이 줄어든 상품 (최대 5)
    products_low_sample: int = Field(default=0, ge=0)  # 표본 부족으로 비교하지 않은 상품 수
    min_product_orders: int | None = None  # 상품 비교의 최소 표본 (주문 수)
    customers: SalesCustomerMix | None = None
    channels: list[SalesChannelRow] = Field(default_factory=list)
    quality: SalesQuality = Field(default_factory=SalesQuality)
    basis: SalesBasis
    summary: str = ""  # 코드 템플릿 문장 (숫자는 위 출력값만)


# ---------------------------------------------------------------- 고객·시장 분석 (11/16)
# segment_profile · segment_favorites · market_coverage · event_proposal(_send).
# 표준 판매 저장소(sales_orders·sales_customers 등)만 읽는다. 숫자는 코드가 계산하고 LLM은 이벤트
# 제안의 title·summary만 쓴다(숫자 검증 실패면 코드 템플릿). 이름(속성·장르·이벤트 종류)은 회사
# 카탈로그 `[sales]`에서 온다.
#
# 공통
# - 결제 완료 주문 기준(`sales_clean.counts_as_revenue`). 기간은 한국 시간 0시 기준으로 N일
# - 개인정보: 집단 통계만. 한 묶음이 `min_group_size`(기본 5)명 미만이면 `suppressed=True`이고
#   그 묶음의 숫자 칸(비율·배수·구매자 수 등)은 비운다(None)
# - 품질 관문(`sales_quality.gate`)에 걸리면 status "insufficient"와 이유, 숫자 칸은 비운다
# - `quality`·`basis`는 매출 분석과 같은 모양. 직전 비교가 없는 분석의 `basis.previous_*`는
#   기간 시작 시각과 같게 둔다
# - 대상 고객(이벤트 제안) = 그 속성 묶음에서 최근 `active_days`(기본 365)일 결제 완료 주문이
#   1회 이상인 고객
# 1d·7d는 화면의 짧은 기간으로 뒤에 더했다 (기존 값은 그대로)
SegmentPeriod = Literal["30d", "90d", "365d", "1d", "7d"]
SegmentTargetKind = Literal["category", "genre", "product"]
ExternalStatus = Literal["ok", "key_missing", "limit", "error"]


class SegmentShare(AxModel):
    """속성 값 하나의 구성비: 대상 구매자 중 비율 vs 전체 구매자 중 비율, 배수."""

    value_code: str
    label: str
    target_share: float | None = Field(default=None, ge=0, le=1)
    overall_share: float | None = Field(default=None, ge=0, le=1)
    lift: float | None = Field(default=None, ge=0)  # target_share ÷ overall_share. 못 재면 None
    buyers: int | None = Field(default=None, ge=0)  # 대상 구매자 중 이 값인 사람 수
    suppressed: bool = False  # min_group_size 미만이면 True, 숫자 칸은 None


class SegmentAttributeBreakdown(AxModel):
    key: str
    label: str
    values: list[SegmentShare] = Field(default_factory=list)  # 카탈로그 값 순서
    # 이 속성 값이 없거나(선택 항목이라 고르지 않음) 설정에 없는 값인 대상 구매자 수
    unknown_buyers: int | None = Field(default=None, ge=0)


class SegmentProfileInput(AxModel):
    target_kind: SegmentTargetKind
    target_code: str = Field(min_length=1, max_length=80)
    attribute_keys: list[str] = Field(default_factory=list)  # 비우면 설정의 모든 속성
    period: SegmentPeriod = "90d"
    tenant_id: str = TENANT_ID_DEFAULT


class SegmentProfileOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    target_kind: SegmentTargetKind
    target_code: str
    target_label: str = ""
    period: SegmentPeriod
    target_buyers: int | None = Field(default=None, ge=0)  # 대상을 산 구매자 (suppressed면 None)
    target_suppressed: bool = False
    overall_buyers: int | None = Field(default=None, ge=0)  # 기간 안 전체 구매자
    attributes: list[SegmentAttributeBreakdown] = Field(default_factory=list)
    quality: SalesQuality = Field(default_factory=SalesQuality)
    basis: SalesBasis | None = None
    summary: str = ""


class FavoriteItem(AxModel):
    """우리 쇼핑몰 안 순위 한 줄: 그 집단이 산 장르·상품."""

    kind: Literal["genre", "category", "product"]
    code: str
    label: str
    share: float | None = Field(default=None, ge=0, le=1)  # 집단 구매자 중 이것을 산 비율
    lift: float | None = Field(default=None, ge=0)  # 전체 구매자 중 비율 대비 배수
    buyers: int | None = Field(default=None, ge=0)
    suppressed: bool = False


class ExternalItem(AxModel):
    name: str
    value: float | None = None
    unit: str = ""
    rank: int | None = Field(default=None, ge=1)


class ExternalResult(AxModel):
    """바깥 출처 한 곳의 결과. 키 없음·한도 초과·오류면 이유만, 숫자를 지어내지 않는다."""

    source_id: str
    label: str = ""  # 화면 이름 (예: 도서관 정보나루 인기대출)
    status: ExternalStatus = "ok"
    reason: str = ""
    period_label: str = ""  # 예: 최근 3개월
    condition_label: str = ""  # 바깥 출처에 건 조건 (사람이 읽는 말)
    items: list[ExternalItem] = Field(default_factory=list)


class SegmentFavoritesInput(AxModel):
    filters: dict[str, str] = Field(min_length=1)  # 속성 key → 값 code
    period: SegmentPeriod = "90d"
    top: int = Field(default=10, ge=1, le=20)
    tenant_id: str = TENANT_ID_DEFAULT


class SegmentFavoritesOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    filter_label: str = ""  # 예: "20대 · 여성"
    period: SegmentPeriod
    segment_buyers: int | None = Field(default=None, ge=0)
    segment_suppressed: bool = False
    internal: list[FavoriteItem] = Field(default_factory=list)
    external: list[ExternalResult] = Field(default_factory=list)
    quality: SalesQuality = Field(default_factory=SalesQuality)
    basis: SalesBasis | None = None
    summary: str = ""


class CoverageRow(AxModel):
    """속성 조합 하나: 우리 구매자 수 ÷ 인구."""

    values: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code
    label: str
    buyers: int | None = Field(default=None, ge=0)
    population: int | None = Field(default=None, ge=0)
    population_source: str = ""  # 예: 통계청 주민등록인구 (표 이름)
    base_year: int | None = None
    coverage: float | None = Field(default=None, ge=0, le=1)
    suppressed: bool = False
    status: Literal["ok", "population_missing", "error"] = "ok"
    reason: str = ""
    # ---- 고객 구성 지수 (11/17, 모두 선택). 응답 안 함·인구 없는 줄·숨긴 줄은 None
    # 이 묶음에 합산한 인구 구간 (사람이 읽는 말, 예: "20~24세 + 25~29세", "50세 이상 합산")
    population_note: str = ""
    # 이 묶음 구매자 ÷ 모든 속성 값을 답한 구매자 합 (응답 안 함 제외)
    buyer_share: float | None = Field(default=None, ge=0, le=1)
    # 이 묶음 인구 ÷ 표의 모든 묶음 인구 합
    population_share: float | None = Field(default=None, ge=0, le=1)
    # buyer_share ÷ population_share × 100 (100 = 인구 구성과 같음)
    index: float | None = Field(default=None, ge=0)


class MarketCoverageInput(AxModel):
    attribute_keys: list[str] = Field(min_length=1)  # 설정 population에 있는 조합만
    period: Literal["365d"] = "365d"
    tenant_id: str = TENANT_ID_DEFAULT


class MarketCoverageOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    attribute_keys: list[str] = Field(default_factory=list)
    period: Literal["365d"] = "365d"
    rows: list[CoverageRow] = Field(default_factory=list)
    quality: SalesQuality = Field(default_factory=SalesQuality)
    basis: SalesBasis | None = None
    summary: str = ""


class ProposalReason(AxModel):
    """제안 근거 하나: 숫자 하나와 출처."""

    kind: Literal["segment", "season", "conversion"]
    text: str  # 사람이 읽는 한 줄 (숫자는 value와 같은 값)
    value: float | None = None
    unit: str = ""  # 배 · % 등
    source: str  # 예: 캣북스 주문 기록 최근 90일 / 네이버 검색어 트렌드 / 지난 캠페인 2건 / 가정값


class TimingBasis(AxModel):
    month: str  # 이벤트 달 YYYY-MM
    keywords: list[str] = Field(default_factory=list)  # 시즌 판단 검색어 (카탈로그 genre_keywords)
    last_year_multiple: float | None = Field(default=None, ge=0)  # 작년 같은 달 ÷ 작년 평균
    source: str = ""


class ProposalEstimate(AxModel):
    """예상 효과 = 대상 고객 수 × 전환율 범위 × 객단가. 할인 비용 = 예상 매출 × 할인율."""

    target_customers: int = Field(ge=0)
    conversion_low: float = Field(ge=0, le=1)
    conversion_high: float = Field(ge=0, le=1)
    conversion_source: Literal["past_campaign", "assumption"]
    assumption: bool = False  # 전환율이 가정값이면 True (화면에 "가정")
    aov: int | None = Field(default=None, ge=0)  # 그 장르(분류) 객단가
    orders_low: int = Field(default=0, ge=0)
    orders_high: int = Field(default=0, ge=0)
    revenue_low: int = Field(default=0, ge=0)
    revenue_high: int = Field(default=0, ge=0)
    discount_cost_low: int = Field(default=0, ge=0)
    discount_cost_high: int = Field(default=0, ge=0)
    formula: str = ""  # 계산식 한 줄 (숫자 포함)


class ProposalSent(AxModel):
    """기획에 넘긴 기록."""

    sent_at: UtcDatetime
    outcome: Literal["created", "duplicate", "rejected"]
    proposal_ref: str | None = None  # 기획 제안 번호 (PROP-n)


class ProposalPastResult(AxModel):
    """지난 제안의 예상 vs 실제 (성과 되돌리기)."""

    proposal_id: str
    proposal_ref: str | None = None
    campaign_id: str | None = None
    expected_low: int | None = None  # 예상 매출 범위
    expected_high: int | None = None
    actual: int | None = None  # 캠페인 기간 매출 증분 (campaign_result)
    period_from: UtcDatetime | None = None
    period_to: UtcDatetime | None = None


class BundleBasis(AxModel):
    """묶음 할인 후보의 근거: basket_pairs의 한 쌍 (같은 사람이 기간 안에 둘 다 산 것)."""

    a_code: str
    a_label: str = ""
    b_code: str
    b_label: str = ""
    lift: float | None = Field(default=None, ge=0)
    together_buyers: int | None = Field(default=None, ge=0)


class EventProposalItem(AxModel):
    proposal_id: str  # 날짜·조건으로 만든 해시. 같은 입력이면 같은 값
    target_filters: dict[str, str] = Field(default_factory=dict)  # 누구 (속성 key → 값 code)
    target_label: str = ""
    genre_code: str | None = None  # 무엇 (장르 또는 분류 하나)
    category_code: str | None = None
    item_label: str = ""
    event_kind: str  # 카탈로그 event_kinds code
    event_label: str = ""
    timing_basis: TimingBasis
    reasons: list[ProposalReason] = Field(min_length=3, max_length=3)
    estimate: ProposalEstimate
    limits: list[str] = Field(default_factory=list)  # 한계 문장
    score: float | None = None  # 후보 점수 (코드 규칙, 순서용)
    title: str | None = None  # LLM 또는 코드 템플릿
    summary: str | None = None
    sent: ProposalSent | None = None
    past_results: list[ProposalPastResult] = Field(default_factory=list)
    bundle: BundleBasis | None = None  # 묶음 할인 후보일 때만 (11/17)


class EventProposalInput(AxModel):
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    max_items: int = Field(default=3, ge=1, le=5)
    tenant_id: str = TENANT_ID_DEFAULT


class ExcludedProposal(AxModel):
    """기준(min_season_multiple·min_orders_low)에 못 미쳐 제안에서 뺀 후보 하나."""

    target_label: str = ""
    item_label: str = ""
    reason: str = ""  # 사람이 읽는 이유 한 줄 (숫자는 서버 값)


class EventProposalOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    month: str
    proposals: list[EventProposalItem] = Field(default_factory=list)
    excluded: list[ExcludedProposal] = Field(default_factory=list)  # 기준 미달로 뺀 후보
    quality: SalesQuality = Field(default_factory=SalesQuality)
    basis: SalesBasis | None = None
    summary: str = ""


class EventProposalSendInput(AxModel):
    proposal_id: str = Field(min_length=1, max_length=80)
    tenant_id: str = TENANT_ID_DEFAULT


class EventProposalSendOutput(AxModel):
    outcome: Literal["created", "duplicate", "rejected"]
    proposal_ref: str | None = None  # 기획 제안 번호 (PROP-n)
    reason: str = ""


# ---------------------------------------------------------------- segment_options (11/16)
# 화면(고객·시장 분석 탭)의 드롭다운·칩·목록을 회사 카탈로그 `[sales]`에서 내려 준다. 화면 코드에
# 속성·장르·질문 이름을 고정하지 않게. 설정을 읽기만 한다(grade read, 숫자 없음).
class OptionItem(AxModel):
    code: str
    label: str


class OptionAttribute(AxModel):
    key: str
    label: str
    values: list[OptionItem] = Field(default_factory=list)  # 카탈로그 순서 그대로


class OptionQuestion(AxModel):
    label: str
    filters: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code


class SegmentOptionsInput(AxModel):
    tenant_id: str = TENANT_ID_DEFAULT


class SegmentOptionsOutput(AxModel):
    attributes: list[OptionAttribute] = Field(default_factory=list)
    genres: list[OptionItem] = Field(default_factory=list)
    categories: list[OptionItem] = Field(default_factory=list)
    quick_questions: list[OptionQuestion] = Field(default_factory=list)
    event_kinds: list[OptionItem] = Field(default_factory=list)
    periods: list[SegmentPeriod] = Field(default_factory=lambda: ["30d", "90d", "365d"])
    # market_coverage에 넣을 수 있는 속성 조합 (카탈로그 population 순서)
    population_keys: list[list[str]] = Field(default_factory=list)
    min_group_size: int = Field(default=5, ge=1)
    data_source: str | None = None  # SalesBasis.data_source와 같은 값
    catalog_id: str = ""
    catalog_version: str | None = None
    item_noun: str = "상품"  # 화면에 쓰는 품목 이름 (카탈로그 item_noun)


# ---------------------------------------------------------------- segment_question (11/16)
# 화면 물어보기 칸의 글 질문을 고르는 칸 값으로 바꾼다. LLM은 그 회사 segment_options 안의
# code만 고른다. 보기에 없는 code는 버리고, 아무것도 못 고르거나 LLM이 실패하면 unparsed.
# 숫자는 만들지 않는다(grade read).
class SegmentQuestionInput(AxModel):
    text: str = Field(min_length=1, max_length=200)
    tenant_id: str = TENANT_ID_DEFAULT


class SegmentQuestionOutput(AxModel):
    status: Literal["ok", "unparsed"] = "unparsed"
    filters: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code
    genre_code: str | None = None
    period: SegmentPeriod | None = None
    understood_label: str = ""  # 예: "20대 · 여성 · 직업 전체 · 최근 90일"
    reason: str = ""  # unparsed일 때 이유 (사람이 읽는 말)


# ---------------------------------------------------------------- 데이터 분석 확장 (11/17)
# basket_pairs · retention · channel_value. 모두 grade read(회사 상태를 바꾸지 않음), LLM 없음.
# 고객·시장 분석과 같은 규칙: 표준 판매 저장소만 읽고, 결제 완료 주문 기준, 한국 시간 0시 기준 기간,
# `min_group_size` 미만 묶음은 suppressed=True이고 숫자 칸은 None, 품질 관문에 걸리면
# status "insufficient"와 이유. 기준값(churn_days·repeat_min_orders·value_horizon_days)은
# 카탈로그 `[sales]`에서 온다.
BasketLevel = Literal["genre", "product"]


class BasketPairsInput(AxModel):
    level: BasketLevel = "genre"
    period: SegmentPeriod = "90d"
    filters: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code (비우면 전체)
    max_pairs: int = Field(default=10, ge=1, le=20)
    tenant_id: str = TENANT_ID_DEFAULT


class BasketPair(AxModel):
    """함께 사는 한 쌍. 단위는 '같은 사람이 기간 안에 둘 다 산 것'(주문 단위 아님)."""

    a_code: str
    a_label: str = ""
    b_code: str
    b_label: str = ""
    together_buyers: int | None = Field(default=None, ge=0)  # 둘 다 산 사람 수
    a_buyers: int | None = Field(default=None, ge=0)
    b_buyers: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0, le=1)  # a를 산 사람 중 b도 산 비율
    lift: float | None = Field(default=None, ge=0)  # confidence ÷ (전체 중 b를 산 비율)
    suppressed: bool = False


class BasketPairsOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    level: BasketLevel = "genre"
    period: SegmentPeriod = "90d"
    filters: dict[str, str] = Field(default_factory=dict)
    pairs: list[BasketPair] = Field(default_factory=list)
    basis: SalesBasis | None = None
    quality: SalesQuality = Field(default_factory=SalesQuality)
    summary: str = ""


class RetentionInput(AxModel):
    cohorts: int = Field(default=3, ge=1, le=6)  # 보는 첫 구매 달 수 (최근 달부터)
    filters: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code (비우면 전체)
    tenant_id: str = TENANT_ID_DEFAULT


class RetentionCohort(AxModel):
    """첫 구매 달 하나: 그 달 첫 구매 고객 중 30·60·90일 안에 다시 산 비율."""

    cohort_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    customers: int | None = Field(default=None, ge=0)
    # 기간이 아직 다 지나지 않았거나 suppressed면 None
    r30: float | None = Field(default=None, ge=0, le=1)
    r60: float | None = Field(default=None, ge=0, le=1)
    r90: float | None = Field(default=None, ge=0, le=1)
    suppressed: bool = False


class RetentionAtRisk(AxModel):
    """속성 묶음 하나의 단골 중 churn_days 넘게 안 산 사람 (가정 기준)."""

    filters: dict[str, str] = Field(default_factory=dict)  # 속성 key → 값 code
    label: str = ""
    regulars: int | None = Field(default=None, ge=0)  # 단골 수 (repeat_min_orders 이상)
    at_risk: int | None = Field(default=None, ge=0)
    share: float | None = Field(default=None, ge=0, le=1)  # at_risk ÷ regulars
    top_item_label: str | None = None  # 그 묶음이 가장 많이 산 장르·상품 이름
    suppressed: bool = False


class RetentionOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    filters: dict[str, str] = Field(default_factory=dict)
    cohorts: list[RetentionCohort] = Field(default_factory=list)  # 오래된 달부터
    at_risk: list[RetentionAtRisk] = Field(default_factory=list)
    churn_days: int = Field(default=60, ge=1)  # 쓴 기준 (카탈로그 churn_days, 가정)
    basis: SalesBasis | None = None
    quality: SalesQuality = Field(default_factory=SalesQuality)
    summary: str = ""


class ChannelValueInput(AxModel):
    period: SegmentPeriod = "365d"  # 첫 구매가 들어간 기간
    tenant_id: str = TENANT_ID_DEFAULT


class ChannelValueRow(AxModel):
    """첫 구매 유입 경로 하나: 그 경로로 온 새 고객의 value_horizon_days 동안 가치."""

    source: str | None = None  # 원본 src_source (화면에는 label만). 표시가 없으면 None
    label: str  # 카탈로그 source_labels 이름, 표시가 없으면 "직접 유입"
    new_customers: int | None = Field(default=None, ge=0)
    revenue_per_customer: int | None = Field(default=None, ge=0)  # 원
    repeat_rate: float | None = Field(default=None, ge=0, le=1)  # 기간 안 다시 산 비율
    orders_per_customer: float | None = Field(default=None, ge=0)
    cost_krw: int | None = Field(default=None, ge=0)  # 경로 비용 (재무 연결 전까지 늘 None)
    suppressed: bool = False


class ChannelValueOutput(AxModel):
    status: Literal["ok", "insufficient"] = "ok"
    insufficient_reasons: list[str] = Field(default_factory=list)
    period: SegmentPeriod = "365d"
    horizon_days: int = Field(default=90, ge=1)  # 쓴 기준 (카탈로그 value_horizon_days)
    rows: list[ChannelValueRow] = Field(default_factory=list)
    cost_note: str = "재무 연결 대기"
    basis: SalesBasis | None = None
    quality: SalesQuality = Field(default_factory=SalesQuality)
    summary: str = ""


# ---------- trend_memes (요즘 유행·밈, 11/18) ----------
# 영상 하나가 아니라 여러 채널에서 같이 늘어난 해시태그·제목 구절을 하나의 유행으로 묶는다.
# 재료는 숏츠 표본(shorts_stats 저장소)과 YouTube 인기 스냅샷의 숏츠뿐(새 외부 호출 없음),
# 네이버 검색 배수는 상위 몇 개만. 점수·순위·판정은 코드, LLM은 설명 한 줄만

# week = 이번 7일 vs 직전 7일, day = 어제 vs 지난 4주 같은 요일 평균
MemeWindow = Literal["week", "day"]


class TrendMemesInput(AxModel):
    window: MemeWindow = "week"


class MemeVideo(AxModel):
    """대표 영상 하나 (조회수 순). 썸네일은 주지 않는다(화면은 링크만)."""

    video_id: str = Field(min_length=1)
    title: str | None = None
    channel_title: str | None = None
    views: int | None = Field(default=None, ge=0)
    video_url: str | None = None


class MemeSearch(AxModel):
    """네이버 검색어 트렌드 배수 (trend_scan과 같은 판정 함수). 상위 몇 개만 본다."""

    # rising·normal·unknown = 판정 결과, not_checked = 보지 않음(순위 밖·하루 한도)
    status: Literal["rising", "normal", "unknown", "not_checked"] = "not_checked"
    ratio: float | None = Field(default=None, ge=0)
    reason: str | None = None


class MemePlatform(AxModel):
    """다른 플랫폼에서 본 같은 유행. `web_search`는 keyword_trend 세션(11/18)부터 채운다."""

    channels: int | None = Field(default=None, ge=0)
    posts: int | None = Field(default=None, ge=0)
    ratio: float | None = Field(default=None, ge=0)
    reason: str | None = None
    # 아래는 11/18 추가 (검색 플랫폼: 네이버 블로그·카페 글 수와 검색광고 월간 검색량)
    blog_total: int | None = Field(default=None, ge=0)  # 블로그 전체 검색 결과 수
    blog_7d: int | None = Field(default=None, ge=0)  # 최근 7일 블로그 글 수 (조회한 결과 안에서)
    blog_7d_capped: bool = False  # 조회한 최대 개수가 모두 7일 안이라 실제는 더 많을 수 있음
    cafe_total: int | None = Field(default=None, ge=0)  # 카페글 전체 검색 결과 수
    monthly_volume: int | None = Field(default=None, ge=0)  # 최근 30일 PC + 모바일 검색량
    volume_below_10: bool = False  # 네이버가 "<10"으로 준 값이 있음 (숫자로 바꾸지 않음)
    volume_reason: str | None = None  # 검색량이 없는 이유 (키 없음 등)
    # 아래는 11/18 인스타 세션 추가 (`instagram` 칸: Instagram 해시태그 검색)
    # 최근 24시간 게시 수(`posts`)가 조회한 페이지 상한에 닿음 (실제는 더 많음)
    posts_24h_capped: bool = False
    top_like_median: float | None = Field(default=None, ge=0)  # 인기 글 좋아요 중앙값
    status: Literal["known", "unknown", "not_hashtag", "not_checked"] | None = None


class TrendMeme(AxModel):
    name: str = Field(min_length=1)  # 묶음에서 가장 많이 쓰인 표기 (해시태그면 # 붙임)
    kind: Literal["hashtag", "phrase"]
    members: list[str] = Field(default_factory=list)  # 하나로 합친 단위들 (표기)
    description: str | None = None  # LLM 한 줄 (숫자 없음). 없으면 description_reason
    description_reason: str | None = None
    channels: int = Field(ge=0)  # 이번 기간 채널 수
    videos: int = Field(ge=0)  # 이번 기간 영상 수
    previous_videos: float | None = Field(default=None, ge=0)  # 직전 기간 영상 수(day는 평균)
    # known = 배수 있음, new = 직전 0이라 새로 등장, unknown = 직전 기간 표본 없음
    ratio_status: Literal["known", "new", "unknown"] = "unknown"
    ratio: float | None = Field(default=None, ge=0)
    ratio_reason: str | None = None
    search: MemeSearch = Field(default_factory=MemeSearch)
    topic_category: str | None = None  # 공유 분류기 결과
    real_entity: bool | None = None
    entity_names: list[str] = Field(default_factory=list)
    # ok = 가능, caution = 주의(실존 이름·상표, 분위기·형식만), excluded = 뺌, unknown = 분류 대기
    ad_material: Literal["ok", "caution", "excluded", "unknown"] = "unknown"
    ad_material_reason: str = ""
    products: list[LinkedProduct] = Field(default_factory=list)  # product_link 기준 통과만
    products_reason: str | None = None  # 연결하지 못한 이유 (상품 목록·임베딩)
    top_videos: list[MemeVideo] = Field(default_factory=list)
    # 뒤에 붙일 플랫폼 칸 (화면 칩 인스타·검색). 지금은 늘 None이고 화면에 그리지 않는다.
    # 네이버 검색 배수는 위 `search` 칸이다
    instagram: MemePlatform | None = None
    web_search: MemePlatform | None = None
    # 11/19 몫(share) 판정: 이번 기간 그 묶음 영상 수 ÷ 이번 기간 전체 숏츠 표본 수,
    # 직전은 직전 칸들 평균 영상 수 ÷ 평균 표본 수. share_ratio = share ÷ previous_share.
    # judged_by가 "share"면 기준(min_ratio)을 share_ratio에 쓴다.
    # ratio·previous_videos는 그대로 채운다(화면 호환)
    share: float | None = Field(default=None, ge=0)
    previous_share: float | None = Field(default=None, ge=0)
    share_ratio: float | None = Field(default=None, ge=0)
    judged_by: Literal["share", "count"] = "count"


class MemeShortsVideo(AxModel):
    """밈 키워드 쇼츠 검색 결과 하나 (search.list 최근 7일·short·KR·조회수 순 → videos·channels)."""

    video_id: str = Field(min_length=1)
    keyword: str  # 이 영상을 찾은 회사 밈 키워드 (여러 키워드면 처음 것)
    title: str | None = None  # untrusted: 화면에는 텍스트로만
    channel_id: str | None = None
    channel_title: str | None = None
    published_at: UtcDatetime | None = None
    views: int | None = Field(default=None, ge=0)
    subscribers: int | None = Field(default=None, ge=0)  # 숨김·모르면 None
    views_per_subscriber: float | None = Field(default=None, ge=0)  # 조회 ÷ 구독자
    big_channel: bool = False  # 구독자 10만 이상 (따로 표시)
    video_url: str | None = None
    thumbnail_url: str | None = None
    # 11/24 채널 이름·제목에 욕설·성인 표현 → "광고 근거 제외: 표현" (InstagramRadarPost와 같음)
    ad_excluded: str | None = None


class MemeShortsSearch(AxModel):
    # known = 오늘(또는 마지막) 검색 저장분, unknown = 검색 못 함(이유), not_set = 키워드 없음
    status: Literal["known", "unknown", "not_set"] = "not_set"
    reason: str | None = None
    day: dt.date | None = None  # 검색한 날 (한국)
    keywords: list[str] = Field(default_factory=list)  # 회사 밈 키워드 (최대 10개)
    searched: list[str] = Field(default_factory=list)  # 실제로 검색한 키워드
    limits: list[str] = Field(default_factory=list)  # 건너뛴 키워드·이유 (쿼터 30% 등)
    units: int = Field(default=0, ge=0)  # 그 검색이 쓴 YouTube 단위
    items: list[MemeShortsVideo] = Field(default_factory=list)  # 구독자 대비 조회 배수 순
    removed: list[MemeSkipped] = Field(default_factory=list)  # 실존 인물·사건 분류로 뺀 영상
    searched_at: UtcDatetime | None = None


class MemeSkipped(AxModel):
    """유행에서 뺀 단위와 이유."""

    name: str
    reason: str
    # exclude_tag = 설정 제외·회사 씨앗, stop_share = 너무 흔한 태그, category = 뉴스·사건 등 분류,
    # person = 사람 이름 자체
    kind: Literal["exclude_tag", "stop_share", "category", "person"]


class MemeCriteria(AxModel):
    min_channels: int = Field(ge=1)
    min_videos: int = Field(ge=1)
    min_ratio: float = Field(ge=0)
    max_items: int = Field(ge=1)
    stop_share: float = Field(gt=0, le=1)
    overlap: float = Field(gt=0, le=1)
    search_top: int = Field(ge=0)


class TrendMemesOutput(AxModel):
    window: MemeWindow = "week"
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None  # unknown이거나 비었으면 이유
    current_from: UtcDatetime | None = None
    current_to: UtcDatetime | None = None
    # 직전 기간 (day면 같은 요일 4일 중 첫날~마지막 날)
    previous_from: UtcDatetime | None = None
    previous_to: UtcDatetime | None = None
    sample_videos: int = Field(default=0, ge=0)  # 중복 뺀 숏츠 수 (게시 시각을 아는 것)
    current_videos: int = Field(default=0, ge=0)
    previous_videos: int = Field(default=0, ge=0)  # 직전 기간 표본 수 (0이면 배수 unknown)
    previous_days: int | None = Field(default=None, ge=0)  # day: 표본이 있는 같은 요일 수
    unknown_published: int = Field(default=0, ge=0)  # 게시 시각을 몰라 세지 않은 수
    criteria: MemeCriteria | None = None
    items: list[TrendMeme] = Field(default_factory=list)
    skipped: list[MemeSkipped] = Field(default_factory=list)
    below_threshold: int = Field(default=0, ge=0)  # 기준(채널·영상·배수)을 못 넘은 묶음 수
    naver_calls: int = Field(default=0, ge=0)  # 이번 계산에서 쓴 네이버 호출
    limits: list[str] = Field(default_factory=list)
    generated_at: UtcDatetime | None = None
    summary: str = ""
    # 11/24 밈 소스: 회사 밈 키워드로 찾은 쇼츠 (하루 1번 수집 저장분). 키워드가 없으면 not_set
    shorts_search: MemeShortsSearch | None = None
    # 11/24 조회 1,000만 이상 (레이더 릴스·쇼츠 검색 저장분, 평소 대비 배수와 상관없이)
    mega_views: MegaViews | None = None


# ---------- keyword_trend (검색 트렌드 · 도서 분야, 11/18) ----------
# 키워드 풀(검색광고 키워드 도구 연관어, 하루 1번) → 데이터랩 판정·추정. 숫자·판정은 코드,
# AI 요약 한 줄만 LLM(explain 숫자 검증). 검색광고 키가 없으면 검색량 칸은 None + 이유, 배수·판정만

# day = 어제 vs 지난 4주 같은 요일, week = 최근 7일 vs 그 전 4주
KeywordWindow = Literal["day", "week"]


class KeywordTrendInput(AxModel):
    window: KeywordWindow = "day"


class KeywordVolume(AxModel):
    """검색광고 키워드 도구 값 (최근 30일). "<10"은 숫자로 바꾸지 않고 below_10으로 둔다."""

    pc: int | None = Field(default=None, ge=0)
    mobile: int | None = Field(default=None, ge=0)
    pc_below_10: bool = False
    mobile_below_10: bool = False
    competition: Literal["low", "mid", "high"] | None = None  # compIdx (PC 광고 기준)

    @property
    def total(self) -> int | None:
        """PC + 모바일. 한쪽이라도 모르면 None ("<10"도 None, 숫자로 바꾸지 않는다)."""
        if self.pc is None or self.mobile is None:
            return None
        return self.pc + self.mobile


class KeywordNews(AxModel):
    title: str
    # 네이버 뉴스: 원문 주소 도메인(언론사 이름 칸이 없음), RSS: 출처 이름
    source: str | None = None
    url: str | None = None
    published_at: UtcDatetime | None = None


class KeywordSpread(AxModel):
    """블로그·카페·뉴스 확산 (NAVER API HUB 검색 API, HUB 앱에서 켠 경우만)."""

    status: Literal["known", "unavailable", "not_checked"] = "not_checked"
    reason: str | None = None
    blog_total: int | None = Field(default=None, ge=0)
    blog_7d: int | None = Field(default=None, ge=0)  # 조회한 결과(최대 개수) 안에서 날짜로 센 수
    blog_7d_capped: bool = False
    cafe_total: int | None = Field(default=None, ge=0)
    # 카페글 검색 응답에는 글 날짜가 없어 최근 7일 수를 셀 수 없다 (늘 None + cafe_7d_reason)
    cafe_7d: int | None = Field(default=None, ge=0)
    cafe_7d_reason: str | None = None
    news: list[KeywordNews] = Field(default_factory=list)  # 최대 3개


class RisingKeyword(AxModel):
    keyword: str = Field(min_length=1)
    rank: int = Field(ge=1)
    status: TrendStatus  # trend_scan과 같은 판정 함수
    ratio: float | None = Field(default=None, ge=0)  # 평소 대비 배수
    score: float | None = None  # z
    reason: str | None = None  # unknown이면 이유
    category: str | None = None  # 공용 분류기 분류명
    volume: KeywordVolume | None = None  # 검색광고 키가 없으면 None
    monthly_volume: int | None = Field(default=None, ge=0)
    volume_reason: str | None = None
    estimate: int | None = Field(default=None, ge=0)  # 어제(week면 최근 7일) 추정 검색 수
    increase: int | None = None  # 추정 − 지난 4주 같은 요일(week면 그 전 4주) 평균
    increase_reason: str | None = None
    news_count: int | None = Field(default=None, ge=0)  # 관련 뉴스 수
    news_source: Literal["naver_news", "google_rss"] | None = None
    news: list[KeywordNews] = Field(default_factory=list)
    spread: KeywordSpread | None = None
    products: int | None = Field(default=None, ge=0)  # product_link 기준 통과 상품 수
    product_names: list[str] = Field(default_factory=list)
    products_reason: str | None = None
    # v3(11/20): rising · warming(normal이고 ratio ≥ [search] warming_ratio) · normal · unknown
    level: Literal["rising", "warming", "normal", "unknown"] = "unknown"
    # 판정에 쓴 마지막 값들(상대값, 지금은 28개)
    points: list[float] = Field(default_factory=list, max_length=35)
    baseline: float | None = None  # 평소 값 (판정 evidence 그대로)
    # 후보로 고른 출처 (같은 말이면 모두)
    candidate_sources: list[
        Literal["new", "related_up", "youtube", "google", "company", "pool"]
    ] = Field(default_factory=list)
    why_line: str | None = Field(default=None, max_length=160)  # 코드 문장 (뉴스·차트·연관어)
    # 11/26 추가: 끝난 급상승 (trend_scan evidence.faded와 같은 판정). status·level·ratio는 그대로
    faded: bool = False
    peak_end: dt.date | None = None  # 정점 구간 끝 날짜 (판정 못 하면 None)


class NewKeyword(AxModel):
    keyword: str
    monthly_volume: int | None = Field(default=None, ge=0)
    volume_below_10: bool = False


class NewKeywordDay(AxModel):
    day: dt.date
    status: Literal["known", "pending"] = "known"  # pending = 그날 풀이 아직 없음(집계 전)
    reason: str | None = None
    items: list[NewKeyword] = Field(default_factory=list)


class SeasonKeyword(AxModel):
    keyword: str
    group: str | None = None  # 공용 분류기 분류명
    ratio: float = Field(ge=0)  # 작년 이달 비율 ÷ 지난 12개월 평균
    month_volume: int | None = Field(default=None, ge=0)  # 월간 검색량 × (이달 비율 ÷ 최근 달 비율)
    volume_reason: str | None = None
    peak_from: str | None = None  # "MM-DD" (작년 같은 시기 일별 비율이 평균 1.5배 넘는 연속 구간)
    peak_to: str | None = None
    peak_reason: str | None = None  # 구간이 없거나 못 본 이유 (화면은 "-")
    products: int | None = Field(default=None, ge=0)
    product_names: list[str] = Field(default_factory=list)
    products_reason: str | None = None


class SeasonGroup(AxModel):
    name: str
    count: int = Field(ge=0)


class KeywordTrendOutput(AxModel):
    window: KeywordWindow = "day"
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    generated_at: UtcDatetime | None = None
    pool_day: dt.date | None = None  # 키워드 풀 날짜 (한국)
    pool_size: int = Field(default=0, ge=0)
    # 검색광고 키워드 도구 연결 (화면 데이터 출처 줄에도 쓴다)
    searchad_status: Literal["connected", "key_missing", "failed"] = "key_missing"
    searchad_reason: str | None = None
    # 블로그·카페·뉴스 검색 (HUB에서 켠 경우만)
    spread_status: Literal["connected", "unavailable", "not_checked"] = "not_checked"
    spread_reason: str | None = None
    rising: list[RisingKeyword] = Field(default_factory=list)
    new_keywords: list[NewKeywordDay] = Field(default_factory=list)  # 최근 4일
    season_month: int | None = Field(default=None, ge=1, le=12)
    season: list[SeasonKeyword] = Field(default_factory=list)
    season_groups: list[SeasonGroup] = Field(default_factory=list)
    season_reason: str | None = None
    summary: str | None = None  # AI 요약 (숫자 검증)
    summary_fallback: bool = False
    summary_reason: str | None = None
    naver_calls: int = Field(default=0, ge=0)  # 이번 계산의 데이터랩 호출
    limits: list[str] = Field(default_factory=list)
    # v3(11/20): 한도로 오늘 못 본 키워드에 직전 판정을 다시 썼으면 그 날짜(여럿이면 가장 오래된 날)
    judged_on: dt.date | None = None
    stale_reason: str | None = None  # "데이터랩 하루 몫을 다 써서 MM-DD 판정을 보여 줘요"
    candidate_counts: dict[str, int] = Field(default_factory=dict)  # 후보 출처별 개수


# ---------- 인스타그램 (11/18) ----------
# Meta 공식 Graph API(Facebook 로그인, graph.facebook.com)만
# 캡션·제목은 외부 글(untrusted). 화면은 텍스트로만. 0으로 채우지 않고 모르면 unknown + 이유

IgStatus = Literal["known", "unknown"]
IgSourceStatus = Literal["connected", "key_missing", "permission", "rate_limited", "failed",
                         "not_registered"]  # fmt: skip


class InstagramSource(AxModel):
    """화면 데이터 출처 줄 하나 (Instagram Audio·Business Discovery·해시태그 검색·수동 메모)."""

    source_id: Literal["ig_audio", "ig_business_discovery", "ig_hashtag_search", "ig_memo"]
    name: str
    status: IgSourceStatus
    reason: str | None = None


class InstagramAudioInput(AxModel):
    audio_type: Literal["music", "original_sound"] = "music"


class InstagramWhyFact(AxModel):
    """ "왜 떴나·왜 뜨나" 근거 한 줄. 숫자는 전부 코드가 계산한 값 (LLM 없음).

    레이더 묶음: spread(퍼짐) · format(형식) · engagement(댓글률) · caption(첫 줄 질문·길이) ·
    time(올린 시간대) · cross(유튜브·네이버) · unknown(공식 API로 볼 수 없는 것).
    인기 오디오: rank(7일 전 → 오늘) · youtube(쇼츠 제목·태그) · search(네이버) · unknown.
    모르면 value None + reason (0으로 채우지 않음).
    """

    kind: Literal["spread", "format", "engagement", "caption", "time", "cross", "unknown",
                  "rank", "youtube", "search"]  # fmt: skip
    label: str
    value: str | None = None  # 이번 값 (화면 표기)
    baseline: str | None = None  # 평소 값 (비교 기준 표기, 없으면 None)
    ratio: float | None = Field(default=None, ge=0)  # 이번 ÷ 평소 (구할 수 없으면 None)
    reason: str | None = None  # 값을 모르는 이유


class InstagramAudioItem(AxModel):
    rank: int = Field(ge=1)
    audio_id: str
    title: str | None = None  # untrusted
    artist: str | None = None  # 음악은 display_artist, 원본 사운드는 만든이 ig_username
    duration_ms: int | None = Field(default=None, ge=0)
    link: str | None = None  # on_platform_audio_preview_link (인스타 안 미리보기 링크)
    previous_rank: int | None = Field(default=None, ge=1)  # 어제 순위 (없으면 None)
    rank_change: int | None = None  # 어제 순위 − 오늘 순위 (양수 = 오름)
    is_new: bool = False  # 어제 목록에 없었음
    streak_days: int = Field(default=1, ge=1)  # 연속으로 목록에 있던 날 수 (오늘 포함)
    overlap: list[str] = Field(default_factory=list)  # 같은 오디오를 쓴 유행 (지금은 늘 비어 있음)
    # "왜 뜨나" 근거 (결론 문장 없음)
    why_facts: list[InstagramWhyFact] = Field(default_factory=list)


class InstagramAudioOutput(AxModel):
    audio_type: Literal["music", "original_sound"] = "music"
    status: IgStatus = "unknown"
    reason: str | None = None
    day: dt.date | None = None  # 목록 날짜 (한국)
    previous_day: dt.date | None = None
    items: list[InstagramAudioItem] = Field(default_factory=list)
    overlap_reason: str | None = None  # "겹침"을 못 보는 이유
    limitation: str = "재생 수는 메타가 주지 않음, 앱 안 목록과 다를 수 있음"
    sources: list[InstagramSource] = Field(default_factory=list)
    generated_at: UtcDatetime | None = None


class InstagramRadarInput(AxModel):
    pass


IgSourceKind = Literal["benchmark", "meme"]


class InstagramRadarPost(AxModel):
    """터진 게시물 하나 (계정 이름·게시 링크·지표, 캡션 일부는 untrusted 텍스트)."""

    account: str
    media_id: str
    media_product_type: str | None = None  # FEED · REELS · STORY · AD
    posted_at: UtcDatetime | None = None
    metric: Literal["view_count", "like_count"]
    value: int = Field(ge=0)
    account_median: float = Field(ge=0)
    ratio: float = Field(ge=0)  # value ÷ account_median
    permalink: str | None = None
    caption_excerpt: str | None = None  # 앞 80자, untrusted
    # 썸네일 주소 (https 인스타 CDN만, oe 만료 전만. 이미지는 내려받지 않음). 릴스·영상은
    # thumbnail_url, 사진·캐러셀은 media_url(첫 장)
    thumbnail_url: str | None = None
    media_type: str | None = None  # IMAGE · VIDEO · CAROUSEL_ALBUM
    # 계정 평소와 가장 크게 다른 점 한 줄 (코드 템플릿, LLM 없음). 다른 점이 없으면 None
    standout: str | None = None
    # 11/24 밈 소스: 레이더 계정 종류 (benchmark = 기업·경쟁사 계정, meme = 밈·크리에이터 계정)
    source_kind: IgSourceKind = "benchmark"
    # 팔로워 대비 반응 = (좋아요 + 댓글) ÷ 팔로워 (business_discovery followers_count, 같은 호출).
    # 팔로워·좋아요를 모르면 None (0으로 채우지 않음)
    followers: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    follower_engagement: float | None = Field(default=None, ge=0)
    # 11/24 대상층: 계정 메타 (RadarAccountMeta). 메타가 없으면 kind는 source_kind로, 층은 빈 목록
    account_kind: RadarKind | None = None
    audience_ages: list[AgeBand] = Field(default_factory=list)
    audience_genders: list[AudienceGender] = Field(default_factory=list)
    topic: str | None = None
    # 11/24 계정 이름·캡션 앞부분에 욕설·성인 표현 → "광고 근거 제외: 표현" (보고서 근거에서 뺌,
    # 원자료 표에는 이 표시로 남김). 걸리지 않으면 None
    ad_excluded: str | None = None
    # 11/26 추가: 브랜드 공지·광고 게시물이면 이유 ("유료 광고 표시" · "광고 표시 #광고" ·
    # "공지 낱말 예매·티켓"). 코드 규칙(data notice_ad). 보고서 근거·밈 재료에서 빼고 화면에는
    # 칩 "공지·광고"만. 아니면 None. 예전 저장분은 없다
    notice_ad: str | None = None


# ---------- 11/24 레이더 계정 메타 (대상층) ----------
# 인스타 공식 API는 다른 계정의 팔로워 나이·성별을 주지 않는다. 계정마다 "주로 보는 층" 태그를 두고
# 의뢰 대상층에 맞는 계정의 게시물만 재료로 쓴다. 태그는 AI가 먼저 달고(ai) 사람이 고친다(human)
# industry = 업종·경쟁, creator = 크리에이터·인플루언서, meme = 밈·코미디, brand = 기타 브랜드
RadarKind = Literal["industry", "creator", "meme", "brand"]
AgeBand = Literal["10s", "20s", "30s", "40s", "50s", "60plus"]
AudienceGender = Literal["male", "female", "all"]


class RadarAccountMeta(AxModel):
    account: str
    kind: RadarKind = "industry"
    ages: list[AgeBand] = Field(default_factory=list)
    genders: list[AudienceGender] = Field(default_factory=list)
    topic: str | None = Field(default=None, max_length=20)
    # None = 메타 없음(옛 목록: kind는 source_kind로)
    tag_source: Literal["ai", "human"] | None = None
    followers: int | None = Field(default=None, ge=0)
    # 판정에서 뺀 이유 (creator·meme 계정이 팔로워 하한보다 작을 때:
    # "팔로워 5만 미만이라 제외 (N명)")
    excluded_reason: str | None = None


class InstagramRadarGroup(AxModel):
    name: str
    description: str | None = None  # LLM 한 줄 (숫자 없음)
    description_reason: str | None = None
    accounts: int = Field(ge=0)
    posts: int = Field(ge=0)
    best_ratio: float = Field(ge=0)  # 묶음 안 가장 큰 평소 대비 배수
    members: list[str] = Field(default_factory=list)  # 묶은 해시태그·구절
    topic_category: str | None = None
    ad_material: Literal["ok", "caution", "excluded", "unknown"] = "unknown"
    ad_material_reason: str = ""
    top_posts: list[InstagramRadarPost] = Field(default_factory=list)
    # "왜 떴나": 근거 숫자(코드 계산, 평소와 다른 점만) + LLM 결론 한 줄(숫자 없음, 120자 이하)
    why_facts: list[InstagramWhyFact] = Field(default_factory=list)
    why_line: str | None = None
    why_line_reason: str | None = None  # why_line이 없는 이유


class InstagramRadarOutput(AxModel):
    status: IgStatus = "unknown"
    reason: str | None = None
    day: dt.date | None = None
    accounts_total: int = Field(default=0, ge=0)  # 설정한 계정 수
    accounts_read: int = Field(default=0, ge=0)  # 오늘 읽은 계정 수
    accounts_failed: list[str] = Field(default_factory=list)  # 못 읽은 계정 (이유는 limits)
    bursts: int = Field(default=0, ge=0)  # 터진 게시물 수
    groups: list[InstagramRadarGroup] = Field(default_factory=list)
    criteria: str = ""
    limits: list[str] = Field(default_factory=list)
    sources: list[InstagramSource] = Field(default_factory=list)
    generated_at: UtcDatetime | None = None
    # 11/24 밈 소스: accounts_total 중 밈·크리에이터 계정 수, 그 계정들의 뜬 게시물
    # (묶음과 상관없이 평소 대비 배수 순, 최대 12개. 밈 계정은 같은 해시태그로 잘 안 묶인다)
    meme_accounts_total: int = Field(default=0, ge=0)
    meme_posts: list[InstagramRadarPost] = Field(default_factory=list)
    # 11/24 대상층: 계정마다 메타(팔로워·제외 이유 포함), industry가 아닌 계정의 뜬 게시물
    # (묶음과 상관없이 배수 순, 최대 40개. 보고서가 의뢰 대상층으로 고른다), 팔로워 하한
    accounts_meta: list[RadarAccountMeta] = Field(default_factory=list)
    audience_posts: list[InstagramRadarPost] = Field(default_factory=list)
    min_followers: int | None = Field(default=None, ge=0)


# ---------- 11/24 조회 1,000만 이상 모음 (인스타 레이더 릴스 + 유튜브 쇼츠 검색) ----------


class MegaViewItem(AxModel):
    platform: Literal["instagram", "youtube"]
    account: str  # 인스타 계정 · 유튜브 채널 이름
    item_id: str  # 인스타 media_id · 유튜브 video_id
    views: int = Field(ge=0)
    posted_at: UtcDatetime | None = None
    link: str | None = None
    thumbnail_url: str | None = None
    title: str | None = None  # 유튜브 제목만 (untrusted). 인스타 캡션은 넣지 않는다
    # 계정 층 태그 (인스타 레이더 계정 메타. 유튜브는 없음)
    account_kind: RadarKind | None = None
    audience_ages: list[AgeBand] = Field(default_factory=list)
    audience_genders: list[AudienceGender] = Field(default_factory=list)
    topic: str | None = None
    # 11/24 계정·채널 이름·제목에 욕설·성인 표현 → "광고 근거 제외: 표현"
    # (InstagramRadarPost와 같음)
    ad_excluded: str | None = None


class MegaViews(AxModel):
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = None
    instagram_min: int = Field(default=10_000_000, ge=0)  # [instagram] radar_mega_views
    youtube_min: int = Field(default=10_000_000, ge=0)  # [youtube] shorts_mega_views
    days: int = Field(default=30, ge=1)  # 최근 며칠 안 게시만
    items: list[MegaViewItem] = Field(default_factory=list)  # 조회수 순


MemoKind = Literal["audio", "format", "hashtag", "other"]


class InstagramMemoAddInput(AxModel):
    kind: MemoKind
    name: str = Field(min_length=1, max_length=60)
    memo: str = Field(default="", max_length=200)
    author: str = Field(min_length=1, max_length=40)
    linked_trend: str | None = Field(default=None, max_length=60)
    tenant_id: str = TENANT_ID_DEFAULT


class InstagramMemo(AxModel):
    memo_id: str
    kind: MemoKind
    name: str
    memo: str = ""
    author: str
    created_at: UtcDatetime
    linked_trend: str | None = None
    source: Literal["manual"] = "manual"  # 출처 표시는 늘 "수동 입력"


class InstagramMemoAddOutput(AxModel):
    outcome: Literal["added", "rejected"]
    memo: InstagramMemo | None = None
    reason: str | None = None


class InstagramMemoListInput(AxModel):
    limit: int = Field(default=50, ge=1, le=200)
    tenant_id: str = TENANT_ID_DEFAULT


class InstagramMemoListOutput(AxModel):
    items: list[InstagramMemo] = Field(default_factory=list)
    # 쓰기(instagram_memo_add)는 safety 등록과 회사 설정이 있어야 화면에 입력을 보인다
    can_add: bool = False
    add_reason: str | None = None


# --------------------------------------------------------------- 트렌드 분석 의뢰 보고서
# 고객 의뢰(제품·타깃·영상 길이·경쟁사·키워드)를 폼 값이나 의뢰 문장으로 받아 보고서를 만든다
# (trend_report_create · trend_report_get · trend_report_list). 숫자는 전부 코드가 계산하고 LLM은
# 결론 한 줄과 콘티 문장만 쓴다(숫자 금지). 모르면 status "unknown" + reason, 0으로 채우지 않는다.
# 프론트와 같이 쓰는 계약이라 필드 이름·값을 바꾸지 않는다.

ReportStatus = Literal["queued", "running", "done", "partial", "failed", "interrupted"]
# skipped(11/20 추가): 이 의뢰에는 계산하지 않는 섹션 (주제 섹션이 theme_mode find가 아닐 때)
# empty(11/20 추가): 봤지만 이번 기간에 해당하는 것이 없음 (볼 수 없었던 unknown과 다르다).
# 보고서 완료 판정에서 known과 같이 센다
SectionStatus = Literal["waiting", "running", "known", "unknown", "failed", "skipped", "empty"]
# company(11/20 추가): 연결된 회사(tenant)의 domain_profile·판매 집단·키워드 풀·확인된 인스타
# 계정으로 채운 칸 (값 우선순위: 폼 > 의뢰문 > 프리셋 > 회사)
# theme(11/20 추가): 주제 섹션이 데이터랩으로 확인해 순위를 매긴 후보를 키워드 앞에 넣은 칸
FieldSource = Literal["input", "request_text", "preset", "company", "theme"]
ThemeMode = Literal["find", "use"]  # find: 주제 안에서 대상을 찾아 달라 · use: 주제를 그대로 쓴다
ReportCaller = Literal["human", "orchestrator", "planning"]
BeatSlot = Literal["0-3", "3-15", "15-25", "25-30"]


class TrendReportInput(AxModel):
    preset: str | None = None  # "eco_beauty" 등. null이면 request_text(또는 제품·키워드)로 고른다
    request_text: str | None = Field(default=None, max_length=4000)  # 의뢰 문장 그대로, untrusted
    product: str | None = None
    target_gender: Literal["f", "m"] | None = None
    target_ages: list[str] | None = None  # 사람 나이 구간 "10","20","30","40","50"
    format: str | None = None  # "인스타 릴스 30초"
    competitors: list[str] | None = None  # 인스타 아이디
    keywords: list[str] | None = None
    accounts: list[str] | None = None  # 추가로 볼 인스타 아이디
    caller: ReportCaller = "human"
    context_id: str | None = None
    rebuild: bool = False  # true면 같은 의뢰 저장본이 있어도 새로 만든다
    goal: str | None = None  # 목적 ("브랜드 인지도"). product와 따로 (v2 추가)
    # 데이터분석 대화 카드에서 넣은 의뢰면 그 대화 id. 진행·완료 메시지를 그 대화에 붙인다
    chat_thread_id: str | None = Field(default=None, max_length=64)
    # 11/20 추가: 의뢰 주제와 쓰임 (대화 카드 칸 값으로 접수할 때 의뢰문을 다시 읽지 않아도 남게)
    theme: str | None = Field(default=None, max_length=30)
    theme_mode: ThemeMode | None = None
    # 11/26 추가: 주제를 넓힌 검색어 (대화 카드가 의뢰문에서 뽑은 것을 그대로 넘긴다. 최대 6)
    related_terms: list[str] | None = Field(default=None, max_length=6)


class TrendReportCreated(AxModel):
    report_id: str
    status: ReportStatus
    reused: bool  # 같은 의뢰 저장본을 연 것
    eta_seconds: int | None = None


class TrendReportRef(AxModel):
    report_id: str


class TrendReportListInput(AxModel):
    limit: int = Field(default=20, ge=1, le=100)


class TrendReportBriefSources(AxModel):
    product: FieldSource
    target: FieldSource
    format: FieldSource
    competitors: FieldSource
    keywords: FieldSource
    goal: FieldSource | None = None  # goal이 없으면 None (v2 추가)
    theme: FieldSource | None = None  # theme이 없으면 None (11/20 추가)


class TrendReportBrief(AxModel):
    product: str | None = None
    target_gender: Literal["f", "m"] | None = None
    target_ages: list[str] = Field(default_factory=list)
    format: str | None = None
    competitors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    preset: str | None = None
    sources: TrendReportBriefSources
    # v2 추가: 목적("브랜드 인지도"), 결과물 칩(코드가 길이 표현만 떼어 냄. 못 나누면 [format]),
    # 경쟁사 표시 이름(business_discovery `name`, 못 받으면 None)
    goal: str | None = None
    format_parts: list[str] = Field(default_factory=list)
    competitor_names: dict[str, str | None] = Field(default_factory=dict)
    # 11/20 추가: 의뢰가 다루는 주제·소재("아이돌", "캠핑")와 그 쓰임(find·use).
    # 의뢰문에 없으면 None
    theme: str | None = Field(default=None, max_length=30)
    theme_mode: ThemeMode | None = None


# 검색 행의 근거: filtered(연령·성별 필터) · all(필터 없이 전체) · volume_only(검색광고 월 검색량만)
# · none(값 없음)
SearchBasis = Literal["filtered", "all", "volume_only", "none"]


class TrendSearchRow(AxModel):
    keyword: str
    search_change_pct: float | None = None  # 최근 7일 평균 ÷ 직전 28일 평균 − 1 (%)
    search_reason: str | None = None
    shopping_change_pct: float | None = None
    shopping_reason: str | None = None
    # v2 추가. small_base: 직전 28일 평균 < 2.0(네이버 상대값)이거나 0인 날이 절반 이상 → 변화율이
    # 쉽게 커지니 "급증(기준값 작음)"으로 보이고 AI 추천 근거에서 뺀다
    basis: SearchBasis | None = None  # v1 저장본은 None
    monthly_volume: int | None = Field(default=None, ge=0)  # 검색광고 월 검색량 (PC+모바일)
    small_base: bool = False  # 검색 변화율
    shopping_small_base: bool = False  # 쇼핑 변화율
    # 11/19: monthly_volume이 null이고 이 값이 "under_10"이면 "월 검색 10 미만" (검색광고 "< 10")
    monthly_volume_note: Literal["under_10"] | None = None


class TrendSearchSection(AxModel):
    status: SectionStatus = "waiting"
    reason: str | None = None
    filter_note: str = ""  # 연령·성별 필터를 어떻게 걸었는지
    rows: list[TrendSearchRow] = Field(default_factory=list)


class TrendHashtagRow(AxModel):
    tag: str
    accounts: int = Field(ge=0)  # 이번 7일에 쓴 계정 수
    previous_accounts: int = Field(ge=0)  # 직전 7일
    burst_posts: int = Field(ge=0)  # 그중 터진 게시물 수
    # v2 추가: 이번 7일에 쓴 계정 아이디 최대 5 (그 계정 사용 수 많은 순),
    # 새로 뜸 = 직전 7일 0곳이고 이번 7일 3곳 이상
    used_by: list[str] = Field(default_factory=list)
    is_new: bool = False


class TrendHashtagSection(AxModel):
    status: SectionStatus = "waiting"
    reason: str | None = None
    rows: list[TrendHashtagRow] = Field(default_factory=list)
    account_names: dict[str, str] = Field(default_factory=dict)  # v2: used_by 표시 이름 (아는 것만)


class TrendReportMeme(AxModel):
    name: str
    description: str | None = None
    ad_material: Literal["ok", "caution", "excluded", "unknown"]


class TrendContentSection(AxModel):
    status: SectionStatus = "waiting"
    reason: str | None = None
    posts: list[InstagramRadarPost] = Field(default_factory=list)  # 썸네일·standout 포함
    memes: list[TrendReportMeme] = Field(default_factory=list)
    # 11/24 대상층: 섹션 머리 한 줄("대상층(20대 남성)에 맞는 계정 N곳 중 뜬 게시물 M개"),
    # 대상층 말, 맞는 계정이 5곳 미만이라 전체로 넓혔는지, 인스타 표본이 적은 층인지(3곳 미만)
    audience_line: str | None = None
    audience_label: str | None = None
    audience_widened: bool = False
    audience_thin: bool = False
    # 쇼츠 검색 근거 (대상층·밈·유행 의뢰일 때만, 실존 인물·사건 분류 뒤),
    # 조회 천만 이상 (대상층 우선)
    shorts: list[MemeShortsVideo] = Field(default_factory=list)
    mega: list[MegaViewItem] = Field(default_factory=list)


class TrendCompetitorTrait(AxModel):
    """잘 된 게시물의 공통점 하나 (v2). 값은 화면에 그대로 쓰는 짧은 문자열."""

    label: Literal["릴스 비율", "첫 줄 질문", "올린 시간", "캡션 길이"]
    value: str  # "100%" · "밤 9~12시" · "120자"
    baseline: str  # "평소 35%"


class TrendCompetitorRow(AxModel):
    account: str
    posts_per_week: float | None = None
    reels_share: float | None = Field(default=None, ge=0, le=1)
    reels_view_median: int | None = None
    best_post: InstagramRadarPost | None = None
    traits: list[str] = Field(default_factory=list)  # 잘 된 게시물의 공통점 (코드 템플릿)
    reason: str | None = None
    name: str | None = None  # v2: 표시 이름 (business_discovery `name`, 못 받으면 None)
    traits_structured: list[TrendCompetitorTrait] = Field(default_factory=list)  # v2


class TrendAccountFailed(AxModel):
    account: str
    reason: str


class TrendCompetitorSection(AxModel):
    status: SectionStatus = "waiting"
    reason: str | None = None
    rows: list[TrendCompetitorRow] = Field(default_factory=list)
    accounts_failed: list[TrendAccountFailed] = Field(default_factory=list)


class TrendReportEvidence(AxModel):
    """콘티 근거 하나 (v2). id = "<섹션>:<행 번호(1부터)>" (search·shopping·hashtag·content·
    meme·competitor). 행 번호는 그 섹션 rows(content는 posts·memes) 순서."""

    id: str
    text: str  # evidence의 같은 자리 문장
    short: str = Field(max_length=30)  # 칩용 짧은 형태 ("비건 선크림 검색 +14%")


class TrendBeat(AxModel):
    slot: BeatSlot
    text: str
    evidence: list[str] = Field(default_factory=list)  # 코드가 만든 근거 문장
    # v2: evidence와 같은 순서
    evidence_items: list[TrendReportEvidence] = Field(default_factory=list)


# 기획에 넘길 재료의 종류 (11/23): 해시태그 · 형식 · 관심 주제 · 밈 · 글쓰기 · 올리는 때 · 그 밖
TrendMaterialKind = Literal["hashtag", "format", "topic", "meme", "writing", "timing", "other"]


class TrendMaterial(AxModel):
    """기획에 넘길 재료 하나 (11/23). 무엇을 쓰라는지 짧은 말(숫자 없음)과, 코드가 만든 근거.
    시간별 장면·대본은 기획(planning video_plan) 몫이라 데이터는 재료까지만 낸다."""

    kind: TrendMaterialKind
    text: str = Field(max_length=80)
    evidence: list[str] = Field(default_factory=list)  # 코드가 만든 근거 문장
    evidence_items: list[TrendReportEvidence] = Field(default_factory=list)  # evidence와 같은 순서


class TrendRecommendationSection(AxModel):
    status: SectionStatus = "waiting"
    reason: str | None = None
    conclusion: str | None = None
    # 옛 보고서의 시간별 콘티 (지우지 않는다). 11/23부터 새 보고서는 비우고 materials를 쓴다
    beats: list[TrendBeat] = Field(default_factory=list)
    music_note: str = ""
    # 아래는 예전 응답은 없다 (11/23). 기획에 넘길 재료 (종류마다 하나, 최대 4)
    materials: list[TrendMaterial] = Field(default_factory=list, max_length=4)
    # 아래는 예전 응답은 없다 (11/24). 이 보고서 추천이 따른 홍보 방향(회사 설정 + 의뢰 문장).
    # 기획 넘김(trend_report_insight)이 같은 값을 ReceiveInsightInput에 싣는다. 예전 보고서는 None
    promo_style: PromoStyle | None = None
    brand_exposure: BrandExposure | None = None


ThemeSeenIn = Literal["radar", "youtube", "llm"]


class TrendThemeRow(AxModel):
    """주제 후보 하나 (11/20). 이름은 저장된 수집 데이터·LLM에서 모으고 데이터랩 값이 있는 것만
    남긴다."""

    name: str
    level: int = Field(ge=0, le=100)  # 최근 28일 평균, 가장 큰 후보 = 100 (묶음 기준 이름으로 맞춤)
    change_pct: float | None = None  # 최근 7일 평균 ÷ 직전 28일 평균 − 1 (%)
    seen_in: list[ThemeSeenIn] = Field(default_factory=list)  # 어디서 나온 이름인지
    products: list[str] = Field(default_factory=list, max_length=3)  # 이름이 든 회사 상품 (이름만)
    basis: str = ""  # "네이버 검색 · 여성 10대" 같은 근거 줄


class TrendThemeDropped(AxModel):
    name: str
    reason: str


class TrendThemeSection(AxModel):
    """주제 후보 순위 (11/20 추가). theme_mode find일 때만 계산, 아니면 skipped."""

    status: SectionStatus = "waiting"
    reason: str | None = None
    theme: str | None = None
    target_word: str = ""  # "여성 10대"
    rows: list[TrendThemeRow] = Field(default_factory=list)
    dropped: list[TrendThemeDropped] = Field(default_factory=list)  # 값 없음 등으로 버린 후보
    method: str = ""  # 순위 계산 방식 한 줄


class TrendReportSections(AxModel):
    search: TrendSearchSection = Field(default_factory=TrendSearchSection)
    hashtags: TrendHashtagSection = Field(default_factory=TrendHashtagSection)
    content: TrendContentSection = Field(default_factory=TrendContentSection)
    competitors: TrendCompetitorSection = Field(default_factory=TrendCompetitorSection)
    recommendation: TrendRecommendationSection = Field(default_factory=TrendRecommendationSection)
    # 11/20 추가. 예전 저장본은 기본값(waiting)으로 읽히니 화면은 theme이 없으면 숨긴다
    theme: TrendThemeSection = Field(default_factory=TrendThemeSection)


class TrendReportCalls(AxModel):
    instagram: int = Field(default=0, ge=0)
    naver: int = Field(default=0, ge=0)
    llm: int = Field(default=0, ge=0)


# 11/26 추가: 의뢰 주제로 찾은 근거 표 (주제가 있는 의뢰만). kind = 어디서 온 근거인지
TopicEvidenceKind = Literal[
    "scan", "search", "shopping", "hashtag", "shorts", "content", "mega", "meme"
]
# 결론·핵심 숫자에서 뺀 이유: off_topic(의뢰 주제·확장어와 연결 낱말 없음) · notice_ad(브랜드
# 공지·광고 게시물) · faded(끝난 급상승, 정점이 지나고 지금은 평소 수준)
ExcludedReason = Literal["off_topic", "notice_ad", "faded"]


class TrendTopicEvidenceRow(AxModel):
    kind: TopicEvidenceKind
    label: str  # "기말고사 검색"
    value: str  # "상승 중" · "평소의 3.2배" · "12편" (코드가 만든 짧은 글)
    source: str  # "감지 탭 · 네이버 데이터랩"
    evidence_id: str | None = None  # 결론 근거 id ("scan:1"), 참고 행이면 None
    reference: bool = False  # 참고로만 (결론 근거에 쓰지 않음: 끝난 급상승)
    peak_end: dt.date | None = None  # 끝난 급상승이면 정점 끝 날짜 (칩 "지난 급상승 · M/D 정점")


class TrendExcludedEvidence(AxModel):
    label: str
    value: str
    reason: ExcludedReason
    evidence_id: str | None = None  # 근거 목록에서 뺀 id ("content:2"). 없으면 처음부터 안 넣은 것


class TrendReportSample(AxModel):
    """11/26 추가: 표본 한 줄 "전체 N곳 · 대상 맞음 M곳 · 사람 확인 K곳" + 인스타 상태.

    계정 언어는 레이더·보고서 저장분 최근 캡션의 한글 비율(코드 계산). 대상 맞음 = 한국어 계정이면서
    계정 대상층 태그가 의뢰 대상층과 맞는 것(대상층이 전체면 한국어만). 사람 확인 = 대상층 태그를
    사람이 확인한(tag_source human) 계정 수. 계정 추가는 사람이 레이더에서 한다(코드로 넣지 않음)
    """

    total: int = Field(default=0, ge=0)  # 레이더·의뢰 계정 전체 (중복은 하나)
    matched: int = Field(default=0, ge=0)  # 대상 맞음
    human_checked: int = Field(default=0, ge=0)  # 사람 확인
    korean: int = Field(default=0, ge=0)  # 그중 한국어 계정 (한글 비율 기준 이상)
    korean_first: bool = False  # 대상층이 한국이라 한국어 계정 근거를 순위 앞으로 옮겼는지
    instagram_connected: bool | None = None  # 인스타 토큰·계정 id가 있는지 (모르면 None)
    instagram_read: int | None = Field(default=None, ge=0)  # 이번 보고서가 읽은 인스타 계정 수


# 11/26 추가: 웃음 재료 · 형식 카드. 상황 요약은 공식 API로 받은 제목·캡션·해시태그(의뢰 주제 근거,
# 공지·광고 제외)에서 LLM이 쓴 요약만 담는다 (원문·링크는 담지 않는다)
HumorSourceKind = Literal["shorts_title", "reels_caption", "hashtag"]


class TrendHumorSituation(AxModel):
    # 상황 한 줄 요약 (원문과 연속 10자 넘게 같으면 버림)
    text: str = Field(min_length=1, max_length=40)
    format_id: str = ""  # 형식 카드 id (F-01…) 또는 "" (모름)
    source_kind: HumorSourceKind  # 화면 근거 칸에는 출처 종류만


class TrendFormatSignal(AxModel):
    """인스타 인기 오디오 이름 "Reels Sound - <형식>"의 형식별 개수.

    보조 신호라 결론에 쓰지 않는다."""

    name: str  # <형식> 그대로 ("Before & After")
    count: int = Field(ge=0)  # 최근 날 목록에서 그 이름 오디오 수
    days_seen: int = Field(ge=0)  # 관측한 날 수 (최근 30일)
    new: bool = False  # 관측 3일 미만 → 칩 "새로 N일 · 아직 추세 아님"
    format_id: str | None = None  # 맞는 형식 카드 id (없으면 None)


class TrendFormatCardBeat(AxModel):
    label: str
    ratio: float = Field(gt=0, le=1)  # 영상 길이 중 이 구간 비율
    punch: bool = False  # 펀치라인 구간


class TrendFormatCard(AxModel):
    """형식 카드 (agent-template format_cards.toml 사본. 보고서에는 추천한 카드만)."""

    id: str
    name: str
    beats: list[TrendFormatCardBeat] = Field(default_factory=list)
    hook: str = ""
    punchline: Literal["자막", "표정", "컷"]
    people: int = Field(ge=0)
    ai_difficulty: Literal["쉬움", "보통", "어려움"]
    note: str = ""


class TrendLimitGroups(AxModel):
    """근거와 한계를 셋으로 (v2). limits는 그대로 채운다."""

    method: list[str] = Field(default_factory=list)  # 숫자 계산 방식
    cannot_see: list[str] = Field(default_factory=list)  # 볼 수 없는 것
    this_report: list[str] = Field(default_factory=list)  # 이번 보고서에서 생긴 주의


class TrendReport(AxModel):
    report_id: str
    status: ReportStatus
    created_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    caller: ReportCaller
    context_id: str | None = None
    brief: TrendReportBrief
    summary_line: str | None = None
    summary_reason: str | None = None
    sections: TrendReportSections = Field(default_factory=TrendReportSections)
    limits: list[str] = Field(default_factory=list)  # 근거와 한계 문장
    calls: TrendReportCalls = Field(default_factory=TrendReportCalls)
    limit_groups: TrendLimitGroups = Field(default_factory=TrendLimitGroups)  # v2
    # 11/26 추가: 주제를 넓힌 검색어(의뢰문에서, 최대 6) · 주제로 찾은 근거 표 · 결론·핵심 숫자에서
    # 뺀 근거(이유 코드). 주제가 없는 의뢰는 모두 비어 있다. 예전 저장본도 비어 있다
    related_terms: list[str] = Field(default_factory=list)
    topic_evidence: list[TrendTopicEvidenceRow] = Field(default_factory=list)
    excluded: list[TrendExcludedEvidence] = Field(default_factory=list)
    # 11/26 추가: 표본 수·인스타 상태 (예전 저장본은 None)
    sample: TrendReportSample | None = None
    # 11/26 추가: 웃음 재료(상황 요약 최대 5 · 형식 신호) · 추천 형식 카드(1~3, 코드 점수).
    # humor_status는 이 묶음의 상태 (LLM 실패면 unknown + humor_reason, 보고서는 계속).
    # 예전 저장본은 None
    humor_status: SectionStatus | None = None
    humor_reason: str | None = None
    humor_situations: list[TrendHumorSituation] = Field(default_factory=list)
    format_signals: list[TrendFormatSignal] = Field(default_factory=list)
    recommended_formats: list[str] = Field(default_factory=list)  # 카드 id (점수 순)
    recommended_cards: list[TrendFormatCard] = Field(default_factory=list)  # 위 id의 카드 내용


class TrendReportListItem(AxModel):
    report_id: str
    status: ReportStatus
    created_at: UtcDatetime
    caller: ReportCaller
    product: str | None = None
    summary_line: str | None = None
    # v3(11/20): 기획 입력을 만들 수 있는지(저장된 보고서로 계산), 기획에 보낸 결과
    has_insight: bool | None = None
    handoff: TrendReportHandoffOutput | None = None


class TrendReportListOutput(AxModel):
    items: list[TrendReportListItem] = Field(default_factory=list)


# ---------------------------------------------------------------- 총괄 연결 (11/20, 15번)
# 총괄 plan: ① data trend_report_create(caller orchestrator → 긴 작업: WORKING + task_id, 진행
# task.progress, 끝 task.completed) → ② data trend_report_insight(report_id) → ③ planning
# receive_insight(②의 insight 그대로). 총괄은 결과에서 번호만 넘긴다.


class TrendReportCompletedOutput(AxModel):
    """task.completed output (번호와 짧은 사실만, 보고서 본문은 싣지 않는다)."""

    report_id: str
    status: ReportStatus
    summary_line: str | None = None
    report_path: str  # "/data/trend#request?id=<id>"
    has_insight: bool = False  # trend_report_insight가 기획 입력을 만들 수 있는지
    insight_reason: str | None = None  # 못 만들면 이유


class TrendReportInsightInput(AxModel):
    report_id: str = Field(min_length=1, max_length=64)


class TrendReportInsightOutput(AxModel):
    """보고서 → 기획 receive_insight 입력 그대로 (같은 보고서면 늘 같은 결과, insight_id 고정)."""

    report_id: str
    insight: ReceiveInsightInput | None = None
    reason: str | None = None  # insight가 null인 이유 (보고서 없음·아직 만드는 중·근거 없음)


# ---------------------------------------------------------------- 기획에 보내기 (11/20, v3)
# 사람이 보낸 의뢰의 인사이트를 기획 receive_insight로 (trend_report_handoff, grade A, execute
# "send_trend_report_insight" 먼저). insight_id = "trend_report:" + report_id (총괄과 같은 키라
# 총괄이 먼저 보냈어도 기획이 duplicate로 받는다).


class TrendReportHandoffInput(AxModel):
    report_id: str = Field(min_length=1, max_length=64)


class TrendReportHandoffOutput(AxModel):
    report_id: str
    outcome: Literal["created", "duplicate", "rejected", "not_sent"]
    proposal_id: str | None = None
    reason: str | None = None
    sent_at: UtcDatetime | None = None
    by: str | None = None


# ---------------------------------------------------------------- 데이터분석 대화 카드 (11/19)
# data_chat_get·data_chat_send 응답의 messages[] 하나에 선택 필드 `card`(없거나 null이면 일반 글).
# 대화는 의뢰 카드(미리보기)만 만들고 접수하지 않는다. 접수는 사람이 카드 버튼으로
# trend_report_create(caller "human" + chat_thread_id)를 부를 때만. 프론트와 같이 쓰는 모양이라
# 이름·값을 바꾸지 않는다.
CardFieldSource = Literal["request_text", "preset", "company"]  # company: 11/20 추가


class TrendRequestCardSources(AxModel):
    product: CardFieldSource
    target: CardFieldSource
    format: CardFieldSource
    competitors: CardFieldSource
    keywords: CardFieldSource


class TrendRequestCardBrief(AxModel):
    product: str | None = None
    target_gender: Literal["f", "m"] | None = None
    target_ages: list[str] = Field(default_factory=list)
    format: str | None = None
    competitors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    competitor_names: dict[str, str] = Field(default_factory=dict)  # 아는 것만, 없으면 {}
    sources: TrendRequestCardSources
    # 11/20 추가: 의뢰 주제와 쓰임 (없으면 None)
    theme: str | None = None
    theme_mode: ThemeMode | None = None
    # 11/26 추가: 주제를 넓힌 검색어 (접수할 때 그대로 넘겨 의뢰문을 다시 읽지 않게)
    related_terms: list[str] = Field(default_factory=list)


class TrendRequestCard(AxModel):
    kind: Literal["trend_request"] = "trend_request"
    request_text: str
    brief: TrendRequestCardBrief
    preset: str | None = None
    cost_note: str  # "인스타 약 45번·네이버 약 6번·AI 2번"
    brief_reason: str | None = None  # 의뢰문에서 칸을 못 뽑았을 때 이유
    # 11/19: 값이 있으면 이미 접수된 카드 (trend_report_create가 chat_thread_id와 오면 data가 적음)
    accepted_report_id: str | None = None


class TrendReportProgressCard(AxModel):
    kind: Literal["trend_report_progress"] = "trend_report_progress"
    report_id: str


class TrendReportDoneCard(AxModel):
    kind: Literal["trend_report_done"] = "trend_report_done"
    report_id: str
    status: ReportStatus
    summary_line: str | None = None
    top_evidence: list[str] = Field(default_factory=list, max_length=3)
    report_path: str  # "/data/trend#request?id=<id>"


DataChatCard = TrendRequestCard | TrendReportProgressCard | TrendReportDoneCard


# ---------------------------------------------------------------- 영상 깊은 지표 (11/26)
# 유튜브 분석(YouTube Analytics·Reporting API, 채널 주인 OAuth)과 인스타 인사이트(Graph API)를
# 받은 그대로 저장한다. 비율·합계 계산은 코드(받은 값으로만), 반올림은 화면. 플랫폼이 값을 안 주면
# (표본 부족) 비워 두고 이유를 적는다(0으로 채우지 않는다). 토큰 값은 어디에도 넣지 않는다.
VideoInsightsSourceId = Literal[
    "youtube_analytics", "youtube_reporting", "instagram_insights", "instagram_audience"
]


class VideoInsightsRequest(AxModel):
    """video_url 또는 campaign_id(등록된 영상) 중 하나. list_all이면 그 회사 등록 영상 전부.

    저장분만 읽는다(수집은 하루 1번 수집기)."""

    video_url: str | None = Field(default=None, min_length=1, max_length=500)
    campaign_id: str | None = Field(default=None, min_length=1, max_length=120)
    list_all: bool = False

    @model_validator(mode="after")
    def _one_of(self) -> VideoInsightsRequest:
        if not self.video_url and not self.campaign_id and not self.list_all:
            raise ValueError("video_url 또는 campaign_id가 있어야 한다 (전부 보기는 list_all)")
        return self


class VideoInsightsWatch(AxModel):
    """시청 값 (받은 그대로). 유튜브: Analytics API 영상별. 인스타: 미디어 인사이트."""

    views: int | None = Field(default=None, ge=0)
    avg_view_seconds: float | None = Field(default=None, ge=0)  # averageViewDuration · 릴스 평균
    avg_view_percent: float | None = Field(default=None, ge=0)  # averageViewPercentage (%)
    watch_minutes: float | None = Field(default=None, ge=0)  # estimatedMinutesWatched
    likes: int | None = Field(default=None, ge=0)
    shares: int | None = Field(default=None, ge=0)
    subscribers_gained: int | None = Field(default=None, ge=0)
    # 인스타 미디어 인사이트 (유튜브는 비움)
    reach: int | None = Field(default=None, ge=0)
    saved: int | None = Field(default=None, ge=0)
    total_interactions: int | None = Field(default=None, ge=0)
    source: str = Field(default="", max_length=40)  # youtube_analytics · youtube_reporting · …


class VideoInsightsRetentionPoint(AxModel):
    """유지 곡선 한 점: 영상 길이 비율(0~1)에서 그 순간 본 비율(audienceWatchRatio, 받은 그대로)."""

    ratio: float = Field(ge=0, le=1)
    watch_ratio: float = Field(ge=0)


class VideoInsightsTraffic(AxModel):
    """유입 경로 한 줄. share는 코드 계산(그 줄 조회 ÷ 유입 경로 조회 합계)."""

    source: str = Field(min_length=1, max_length=60)  # insightTrafficSourceType 그대로
    views: int = Field(ge=0)
    share: float | None = Field(default=None, ge=0, le=1)
    ad: bool = False  # ADVERTISING = 광고


class VideoInsightsAudience(AxModel):
    """시청층 한 칸 (연령대 × 성별). percent: viewerPercentage 그대로 · 인스타는 코드 비율(%)."""

    age: str = Field(default="", max_length=20)  # age18-24 · 18-24 (받은 그대로)
    gender: str = Field(default="", max_length=20)  # male · female · F · M · U
    percent: float = Field(ge=0, le=100)


class VideoInsightsReach(AxModel):
    """노출·클릭률 (유튜브 Reporting channel_reach_basic_a1). 합계·가중 평균은 코드."""

    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = Field(default=None, max_length=200)  # 예약 필요 · 리포트 기다리는 중
    impressions: int | None = Field(default=None, ge=0)  # video_thumbnail_impressions 합계
    # video_thumbnail_impressions_ctr를 노출로 가중 평균 (리포트 단위 그대로)
    ctr: float | None = Field(default=None, ge=0)
    period: str | None = Field(default=None, max_length=40)  # 리포트 날짜 "<from>~<to>"


class VideoInsightsSource(AxModel):
    """출처 연결 상태 한 줄 (화면 "출처 연결 상태" 표). 토큰 값·일부 글자 없음 (env 이름만)."""

    source: VideoInsightsSourceId
    status: Literal["connected", "waiting", "check"]  # 연결됨 · 연결 대기 · 권한·설정 확인
    reason: str | None = Field(default=None, max_length=200)
    needs: list[str] = Field(default_factory=list, max_length=8)  # 필요한 것 (env 이름·권한)


class VideoInsightsOutput(AxModel):
    """영상 하나의 깊은 지표 (list_all이면 videos에 영상마다 같은 모양, 위 칸은 비움)."""

    schema_version: str = "video_insights.v1"
    platform: VideoPlatform | None = None
    video_key: str | None = Field(default=None, max_length=80)  # 플랫폼:영상 id (video_performance)
    video_id: str | None = Field(default=None, max_length=80)
    video_url: str | None = Field(default=None, max_length=500)
    campaign_id: str | None = Field(default=None, max_length=120)
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = Field(default=None, max_length=200)
    as_of: dt.date | None = None  # 데이터 기준 날짜 (그날까지의 값)
    collected_at: UtcDatetime | None = None  # 저장한 시각
    watch: VideoInsightsWatch | None = None
    retention: list[VideoInsightsRetentionPoint] = Field(default_factory=list, max_length=20)
    traffic: list[VideoInsightsTraffic] = Field(default_factory=list, max_length=30)
    ad_share: float | None = Field(default=None, ge=0, le=1)  # 광고 유입 비율 (코드)
    audience: list[VideoInsightsAudience] = Field(default_factory=list, max_length=60)
    audience_basis: Literal["video", "account"] | None = None  # account = 계정 기준 (인스타)
    audience_reason: str | None = Field(default=None, max_length=200)  # 표본 부족 등
    reach: VideoInsightsReach | None = None
    needs: list[VideoInsightsSource] = Field(default_factory=list, max_length=8)
    facts: list[DataFact] = Field(default_factory=list)
    summary: str = Field(default="", max_length=300)
    videos: list[VideoInsightsOutput] = Field(default_factory=list)


TrendInsight.model_rebuild()
VideoInsightsOutput.model_rebuild()
