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
    # 평소보다 더 실패한 건수: 구간 장애 쪽 실패 − 지난 7일 같은 시각 ±1시간의 평소 실패
    # (구간 길이만큼, 반올림, 0 미만이면 0). 지난 7일 자료가 없으면 None. 예전 응답에는 없다
    excess_failed_order_count: int | None = Field(default=None, ge=0)

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

    @model_validator(mode="after")
    def _range(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date가 start_date보다 앞이다")
        names = [g.group_name for g in self.keyword_groups]
        if len(names) != len(set(names)):
            raise ValueError("group_name이 겹친다")
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
    shorts_format_median_views: float | None = Field(default=None, ge=0)
    shorts_format_at: UtcDatetime | None = None
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


BriefTrigger = Literal["auto", "manual"]
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


TrendInsight.model_rebuild()
