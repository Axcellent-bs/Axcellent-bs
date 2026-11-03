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
    fallback_reason: str | None = None  # llm_unavailable·llm_error·format_error·number_invalid
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


class PopularCategory(AxModel):
    """화면용 카테고리 하나의 "지금 인기" 1~N위 (회사 설정 [popular] categories)."""

    category: TopicCategory
    items: list[PopularTopic] = Field(default_factory=list)  # 점수 순, 최대 per_category개
    # N개를 못 채웠으면 이유 (예: 이 카테고리로 분류된 주제가 3개뿐)
    shortage_reason: str | None = None


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
    limitation: str = ""  # 한계 (이미 뜬 영상끼리의 비교 등)
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
