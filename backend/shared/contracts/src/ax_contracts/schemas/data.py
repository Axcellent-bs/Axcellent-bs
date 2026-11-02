"""data 에이전트 skill 입출력. 이슈 #36 요청(data), #33 함께 해결.

- `impact_analysis` (DEP-DATA-02): 장애 영향 숫자의 원천은 data 한 곳이다 (agent-common 8장).
  건수·금액은 SQL로 계산하고 고객은 가명 ID만 담는다.
- `anomaly_detection` (DEP-DATA-01): infra `verify_recovery`의 `business_metrics_ok` 근거.
- `fraud_score` (DEP-DATA-03): 판매자 사기 점수.
- `kpi_summary` (DSH-04 지원): 사건별 KPI 요약.
"""

from __future__ import annotations

import datetime as dt
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


class YouTubeTagEvidence(AxModel):
    """후보 키워드의 YouTube 근거 (스냅샷 한 시각의 인기 영상 목록 안에서 센 값)."""

    video_count: int = Field(ge=1)  # 이 태그(정리한 뒤)가 붙은 인기 영상 수
    snapshot_at: UtcDatetime  # 스냅샷 수집 시각
    video_ids: list[str] = Field(default_factory=list)  # 대표 영상 id (인기 순위 순, 몇 개만)


class TrendCandidate(AxModel):
    keyword: str = Field(min_length=1)  # 정리한 태그 (앞뒤 공백·# 제거, 영문 소문자)
    youtube: YouTubeTagEvidence
    # 네이버 검색어 급상승 판정 (trend_scan과 같은 함수의 결과 그대로).
    # 네이버를 못 읽으면 이 후보만 status unknown + 이유
    naver: TrendVerdict


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

    @model_validator(mode="after")
    def _known_or_reason(self) -> Self:
        if self.status == "unknown" and not self.unknown_reason:
            raise ValueError("unknown이면 unknown_reason이 있어야 한다")
        if self.status == "unknown" and self.candidates:
            raise ValueError("unknown이면 후보가 없어야 한다")
        return self


# --------------------------------------------------------------- campaign_effect (캠페인 효과)
# 캠페인 기간 주문·매출을 지난 7일 같은 시간대 평균과 비교한 증분과, 캠페인 쿠폰 누적 사용률.
# 항목마다 "앎(값·근거)" 또는 "모름 + 이유"다. 모르면 0으로 채우지 않는다.

CampaignDirection = Literal["up", "none", "down"]  # 늘었음·같음·줄었음
CampaignStatus = Literal["known", "unknown"]


class CampaignEffectInput(AxModel):
    campaign_id: str | None = None  # 없으면 쿠폰은 확인할 수 없음 (주문·매출은 본다)
    window_from: UtcDatetime  # 캠페인 시작
    window_to: UtcDatetime  # 캠페인 끝

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


class CampaignEffectOutput(AxModel):
    campaign_id: str | None = None
    window_from: UtcDatetime
    window_to: UtcDatetime
    orders: CampaignChange
    revenue: CampaignChange
    coupons: CampaignCoupons
    summary: str  # 한두 문장 (LLM, 숫자 검증 실패면 고정 문장)

