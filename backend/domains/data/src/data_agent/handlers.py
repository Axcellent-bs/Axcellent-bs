"""data 에이전트 skill 핸들러.

- impact_analysis, anomaly_detection은 읽기 전용(grade read)이라 `execute()`를 거치지 않는다.
- kpi_summary는 공통 로그 저장소를 읽기만 한다(grade read).
- activity_report는 공통 로그를 읽고, 회사 숫자는 재무 closing_report(A2A)와 shop-api 지표로 받는다
  (grade read). 다른 부서 DB는 직접 열지 않는다.
- root_cause_hypotheses는 다른 부서에 조회만 하고(A2A) 원인 후보를 고른다(grade read).
- fraud_score는 shop-api 판매자 정보·가격을 읽어 규칙 점수를 낸다(grade read). 승인·보류는 정하지
  않는다. 확인할 수 없는 규칙은 점수에서 빼고 `unknown_rules`로 알린다. 없는 판매자는 `not_found`다.
- trend_scan은 네이버 검색어 트렌드를 읽어 키워드 그룹별 급상승을 판정한다(grade read). 그룹마다
  따로 부르고, 모든 그룹에서 못 읽으면 숫자 없이 `failed`·`unavailable`이다.
- trend_candidates는 YouTube 인기 영상 스냅샷 태그에서 후보 키워드를 코드 규칙으로 뽑고, 후보마다
  trend_scan과 같은 함수로 네이버 급상승을 붙인다(grade read). 스냅샷이 없거나 오래되면
  `completed`에 status unknown + 이유, 네이버를 못 읽은 후보는 그 후보만 unknown이다.
- content_recommend는 trend_scan·trend_candidates 판정에서 급상승한 것만 코드 점수로 순위를 매기고
  소재마다 영상 아이디어 문장을 LLM으로 쓴다(grade read, 숫자 검증). 급상승이 없으면
  빈 목록 + 이유다.
- trend_history는 키워드 그룹별 네이버 월 단위 값과 작년 같은 달 대비 배수만 준다
  (grade read, 판정 안 함).
  그룹마다 따로 부르고, 모든 그룹에서 못 읽으면 숫자 없이 `failed`·`unavailable`이다.
- youtube_velocity는 YouTube 스냅샷 최근 두 시각 사이 영상별 시간당 조회 증가(지금 속도)와 가속을
  보여 준다(grade read, 판정 안 함). 스냅샷이 2개 미만이면 `completed`에 unknown + 이유다.
- answer_planning_question은 data가 보낸 요청의 기록으로만 기획 질문에 답한다(grade read,
  `planning_questions.py`). 기록에 없으면 `unknown`이다.
- campaign_effect는 캠페인 기간 주문 증분·쿠폰 누적 사용률을 코드로 계산한다(grade read).
  주문 지표를 읽지 못하면 숫자 없이 `failed`·`unavailable`이다.
- 외부 입력(`request.untrusted`)은 읽지 않는다.
- shop-api에서 데이터를 받지 못하면(`ShopUnavailable`) 숫자를 0으로 내지 않고 `failed`·`unavailable`
  과 "확인할 수 없음" 근거로 답한다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from ax_agent_template.client import call_agent
from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.data import (
    AccuracyReportInput,
    ActivityReportInput,
    AnomalyDetectionInput,
    CampaignEffectInput,
    ContentRecommendRequest,
    FraudScoreInput,
    ImpactAnalysisInput,
    KpiSummaryInput,
    PromoConceptsRequest,
    RootCauseInput,
    ShortsStatsRequest,
    TrendCandidatesRequest,
    TrendHistoryRequest,
    TrendPopularRequest,
    TrendScanRequest,
    YouTubeVelocityRequest,
)
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from data_agent.clients.fact_sources import notices_at
from data_agent.clients.log_client import get_log_source
from data_agent.clients.shop_client import (
    HttpSellerClient,
    SellerClient,
    ShopUnavailable,
    get_connector,
    shop_api,
)
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import NaverDatalabConnector
from data_agent.connectors.youtube import YouTubeConnector
from data_agent.planning_questions import answer_question
from data_agent.report_store import ReportStore, get_report_store
from data_agent.skills import topic_category
from data_agent.skills.accuracy_report import eval_dir, load_report
from data_agent.skills.activity_report import InvalidActivityInput, build_report
from data_agent.skills.anomaly_detection import detect_anomalies, env_settings, get_metric_source
from data_agent.skills.campaign_effect import analyze_campaign
from data_agent.skills.content_recommend import recommend
from data_agent.skills.fraud_score import score_fraud
from data_agent.skills.impact_analysis import analyze_impact
from data_agent.skills.kpi_summary import summarize_kpi
from data_agent.skills.promo_concepts import make_concepts
from data_agent.skills.root_cause import estimate_root_cause
from data_agent.skills.shorts_stats import shorts_stats as run_shorts_stats
from data_agent.skills.trend_candidates import find_candidates
from data_agent.skills.trend_history import history
from data_agent.skills.trend_popular import popular
from data_agent.skills.trend_scan import NoTrendGroups, scan_trends
from data_agent.skills.youtube_velocity import measure_velocity_named


def get_llm() -> LLMClient:
    """설명 문장용 LLM. 테스트는 이 함수를 바꿔 끼운다. 미설정이면 explain이 템플릿으로 대체한다."""
    return LLMClient("data")


def get_seller_client() -> SellerClient:
    """판매자 정보 클라이언트(shop-api). 테스트·평가는 이 함수를 바꿔 끼운다.

    목 판매자 데이터는 없어서 `DATA_MOCK`이어도 shop-api를 부른다(지어내지 않는다).
    """
    return HttpSellerClient(shop_api(os.environ))


def _fallback_reason(result: object) -> dict:
    """설명을 템플릿으로 대체한 이유 (`AgentResult.fallback_reason`·`fallback_numbers`)."""
    return {
        "fallback_reason": getattr(result, "fallback_reason", None),
        "fallback_numbers": list(getattr(result, "fallback_numbers", ())),
    }


def _result(request: AgentRequest, status: TaskStatus, **kwargs) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _parse[T: BaseModel](request: AgentRequest, model: type[T]) -> T | AgentResult:
    try:
        return model.model_validate(request.input)
    except ValidationError as error:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={
                "errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            },
        )


def _unavailable(request: AgentRequest, error: NotImplementedError) -> AgentResult:
    return _result(
        request, TaskStatus.FAILED, outcome="unavailable", output={"message": str(error)}
    )


def _shop_unavailable(request: AgentRequest, error: ShopUnavailable, query: str) -> AgentResult:
    """shop-api 데이터 없이 답한다. 숫자는 내지 않고 근거에 확인할 수 없는 이유를 남긴다."""
    message = f"확인할 수 없음: {error} [{error.code}]"
    return _result(
        request,
        TaskStatus.FAILED,
        outcome="unavailable",
        output={"message": message, "code": error.code},
        evidence=Evidence(queries=[query], data_summary=message, collected_at=datetime.now(UTC)),
    )


async def impact_analysis(request: AgentRequest) -> AgentResult:
    data = _parse(request, ImpactAnalysisInput)
    if isinstance(data, AgentResult):
        return data
    if data.window_from > data.window_to:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")
    try:
        result = await analyze_impact(data, request.meta.tenant_id, get_llm(), os.environ)
    except NotImplementedError as error:
        return _unavailable(request, error)
    except ShopUnavailable as error:
        return _shop_unavailable(request, error, "shop_failed_orders")
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


async def anomaly_detection(request: AgentRequest) -> AgentResult:
    data = _parse(request, AnomalyDetectionInput)
    if isinstance(data, AgentResult):
        return data
    if data.window_from >= data.window_to:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")
    try:
        source = get_metric_source(os.environ)
    except NotImplementedError as error:
        return _unavailable(request, error)

    async def campaigns(start: datetime, end: datetime) -> list[str]:
        """주문이 늘었을 때 게시 중이던 공지·캠페인 (근거용). 마케팅이 꺼져 있으면 그 이유 한 줄."""
        return [f.summary for f in await notices_at(start, end, request.meta)]

    try:
        result = await detect_anomalies(
            data,
            source,
            get_llm(),
            settings=env_settings(request.meta.tenant_id, os.environ),
            campaigns=campaigns,
        )
    except ShopUnavailable as error:
        return _shop_unavailable(request, error, "shop_metric_series")
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


async def kpi_summary(request: AgentRequest) -> AgentResult:
    data = _parse(request, KpiSummaryInput)
    if isinstance(data, AgentResult):
        return data
    if data.period_from and data.period_to and data.period_from > data.period_to:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")
    try:
        source = get_log_source(os.environ)
    except NotImplementedError as error:
        return _unavailable(request, error)
    result = await summarize_kpi(data, request.meta.tenant_id, get_llm(), source=source)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


async def root_cause_hypotheses(request: AgentRequest) -> AgentResult:
    """사실을 모아 원인 후보를 고른다. 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, RootCauseInput)
    if isinstance(data, AgentResult):
        return data
    if data.window_from >= data.window_to:
        return _result(request, TaskStatus.FAILED, outcome="invalid_input")
    result = await estimate_root_cause(data, request.meta, get_llm())
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


async def accuracy_report(request: AgentRequest) -> AgentResult:
    """정확도 평가 결과 파일을 읽어 준다. 결과가 없으면 source none(확인할 수 없음)."""
    data = _parse(request, AccuracyReportInput)
    if isinstance(data, AgentResult):
        return data
    report = load_report(eval_dir(os.environ))
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=report.model_dump(mode="json"),
        evidence=Evidence(
            queries=["eval/results/latest.json", "eval/baseline.json"],
            data_summary=report.summary,
            collected_at=datetime.now(UTC),
        ),
    )


def get_fraud_card_sender():  # noqa: ANN201 - 테스트·평가가 바꿔 끼우는 자리
    """hr 유사 사기 카드 조회에 쓰는 A2A 함수. 기본은 `call_agent`."""
    return call_agent


async def fraud_score(request: AgentRequest) -> AgentResult:
    data = _parse(request, FraudScoreInput)
    if isinstance(data, AgentResult):
        return data
    try:
        result = await score_fraud(
            data, get_seller_client(), meta=request.meta, send=get_fraud_card_sender()
        )
    except ShopUnavailable as error:
        query = f"shop_seller_profile seller_id={data.seller_id}"
        if error.code == "SELLER_NOT_FOUND":
            message = f"확인할 수 없음: shop-api에 판매자 {data.seller_id}가 없음 [{error.code}]"
            return _result(
                request,
                TaskStatus.FAILED,
                outcome="not_found",
                output={"message": message, "code": error.code},
                evidence=Evidence(
                    queries=[query], data_summary=message, collected_at=datetime.now(UTC)
                ),
            )
        return _shop_unavailable(request, error, query)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


def get_activity_store() -> ReportStore | None:
    """업무 리포트 일간 저장소 (`DATA_REPORT_DB`). 없으면 None이고 리포트는 바로 계산한다."""
    return get_report_store(os.environ)


def get_activity_sender():  # noqa: ANN201 - 테스트가 바꿔 끼우는 자리
    """재무 closing_report 조회에 쓰는 A2A 함수. 기본은 `call_agent`."""
    return call_agent


async def activity_report(request: AgentRequest) -> AgentResult:
    """부서별 AI 업무 리포트. 공통 로그·재무·shop-api를 읽기만 한다(grade read)."""
    data = _parse(request, ActivityReportInput)
    if isinstance(data, AgentResult):
        return data
    try:
        logs = get_log_source(os.environ)
    except NotImplementedError as error:
        return _unavailable(request, error)
    try:
        result = await build_report(
            data,
            request.meta,
            get_llm(),
            logs=logs,
            metrics=get_metric_source(os.environ),
            send=get_activity_sender(),
            store=get_activity_store(),
        )
    except InvalidActivityInput as error:
        return _result(
            request, TaskStatus.FAILED, outcome="invalid_input", output={"errors": [str(error)]}
        )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


def get_trend_connector() -> NaverDatalabConnector:
    """네이버 검색어 트렌드 커넥터. 테스트는 이 함수를 바꿔 끼운다(실제 API를 부르지 않게)."""
    return NaverDatalabConnector(os.environ)


async def trend_scan(request: AgentRequest) -> AgentResult:
    """키워드 그룹별 검색어 급상승. 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, TrendScanRequest)
    if isinstance(data, AgentResult):
        return data
    try:
        result = await scan_trends(data, get_trend_connector(), get_llm(), env=os.environ)
    except NoTrendGroups as error:
        return _result(
            request, TaskStatus.FAILED, outcome="invalid_input", output={"message": str(error)}
        )
    except ConnectorUnavailable as error:
        message = f"확인할 수 없음: 네이버 검색어 트렌드를 읽지 못함 ({error}) [{error.code}]"
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="unavailable",
            output={"message": message, "code": error.code},
            evidence=Evidence(
                queries=["naver search-trend"], data_summary=message, collected_at=datetime.now(UTC)
            ),
        )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


async def trend_candidates(request: AgentRequest) -> AgentResult:
    """YouTube 인기 태그 후보 + 네이버 급상승. 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, TrendCandidatesRequest)
    if isinstance(data, AgentResult):
        return data
    result = await find_candidates(data, get_trend_connector(), env=os.environ)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def trend_popular(request: AgentRequest) -> AgentResult:
    """지금 인기 주제 순위 (급상승 판정과 별개, 네이버 안 부름). 조회만 한다(grade read)."""
    data = _parse(request, TrendPopularRequest)
    if isinstance(data, AgentResult):
        return data
    result = await popular(
        data,
        get_llm(),
        env=os.environ,
        context_id=request.meta.context_id,
        category_cache=get_category_cache(),
        youtube=get_youtube(),
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="ranked" if result.output.items else "empty",
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


def get_youtube() -> YouTubeConnector:
    """YouTube Data API 커넥터 (키는 .env). 테스트는 이 함수를 바꿔 끼운다."""
    return YouTubeConnector(os.environ)


async def shorts_stats(request: AgentRequest) -> AgentResult:
    """숏츠 통계 (카테고리별·형식별). 검색은 하루 1번 저장, 조회만 한다(grade read)."""
    data = _parse(request, ShortsStatsRequest)
    if isinstance(data, AgentResult):
        return data
    result = await run_shorts_stats(
        data,
        get_youtube(),
        get_llm(),
        env=os.environ,
        context_id=request.meta.context_id,
        category_cache=get_category_cache(),
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def promo_concepts(request: AgentRequest) -> AgentResult:
    """홍보 영상 기획안 3~5개 (하루 캐시, 기획안 기록만 남김). 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, PromoConceptsRequest)
    if isinstance(data, AgentResult):
        return data
    result = await make_concepts(
        data,
        get_trend_connector(),
        get_llm(),
        env=os.environ,
        catalog=get_catalog(),
        context_id=request.meta.context_id,
        category_cache=get_category_cache(),
        youtube=get_youtube(),
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="drafted" if result.output.concepts else "empty",
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def content_recommend(request: AgentRequest) -> AgentResult:
    """영상 소재 추천 (콘텐츠 루프 2단계). 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, ContentRecommendRequest)
    if isinstance(data, AgentResult):
        return data
    result = await recommend(
        data,
        get_trend_connector(),
        get_llm(),
        env=os.environ,
        context_id=request.meta.context_id,
        catalog=get_catalog(),
        category_cache=get_category_cache(),
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="recommended" if result.output.items else "empty",
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
    )


_category_cache: topic_category.CategoryCache | None = None


def get_category_cache() -> topic_category.CategoryCache:
    """주제 분류 저장소 (backend/.data, 보관 기간·덮어쓰기 표는 회사 설정). 프로세스에 하나."""
    global _category_cache
    if _category_cache is None:
        _category_cache = topic_category.cache_from_env(os.environ)
    return _category_cache


def get_catalog() -> object | None:
    """회사 상품 목록 커넥터(캣북스 shop-api). 없으면 None. 테스트는 이 함수를 바꿔 끼운다."""
    try:
        return get_connector(os.environ)
    except NotImplementedError:
        return None


async def trend_history(request: AgentRequest) -> AgentResult:
    """키워드 그룹별 월별 흐름(작년 같은 달 대비). 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, TrendHistoryRequest)
    if isinstance(data, AgentResult):
        return data
    try:
        result = await history(data, get_trend_connector(), env=os.environ)
    except NoTrendGroups as error:
        return _result(
            request, TaskStatus.FAILED, outcome="invalid_input", output={"message": str(error)}
        )
    groups = result.output.groups
    if groups and all(
        (g.unknown_reason or "").startswith("네이버 검색어 트렌드를 읽지 못함") for g in groups
    ):
        message = f"확인할 수 없음: {groups[0].unknown_reason}"
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="unavailable",
            output={"message": message},
            evidence=Evidence(
                queries=["naver search-trend month"],
                data_summary=message,
                collected_at=datetime.now(UTC),
            ),
        )
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def youtube_velocity(request: AgentRequest) -> AgentResult:
    """YouTube 영상 지금 속도(판정 없음). 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, YouTubeVelocityRequest)
    if isinstance(data, AgentResult):
        return data
    result = await measure_velocity_named(data, env=os.environ, youtube=get_youtube())
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def answer_planning_question(request: AgentRequest) -> AgentResult:
    """기획이 되묻는 근거 질문에 data가 보낸 요청의 기록으로 답한다(grade read)."""
    data = _parse(request, AnswerPlanningQuestionInput)
    if isinstance(data, AgentResult):
        return data
    out = answer_question(data, request.meta.tenant_id, request.meta.context_id)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=out.status,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[f"data.sent_requests request_id={data.request_id}"],
            data_summary=f"{data.question_id} → {out.status}",
            collected_at=datetime.now(UTC),
            ref_ids=out.evidence_ids,
        ),
    )


def get_campaign_connector():  # noqa: ANN201 - 테스트·평가가 바꿔 끼우는 자리
    """캠페인 효과가 읽는 커넥터 (회사 카탈로그의 커넥터, 캣북스는 shop-api)."""
    return get_connector(os.environ)


async def campaign_effect(request: AgentRequest) -> AgentResult:
    """캠페인 효과(주문 증분·쿠폰 누적 사용률). 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, CampaignEffectInput)
    if isinstance(data, AgentResult):
        return data
    try:
        connector = get_campaign_connector()
    except NotImplementedError as error:
        return _unavailable(request, error)
    try:
        result = await analyze_campaign(data, connector, get_llm())
    except ShopUnavailable as error:
        span = f"{data.window_from.isoformat()}~{data.window_to.isoformat()}"
        return _shop_unavailable(request, error, f"shop_metric_series metric=order_count {span}")
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
        **_fallback_reason(result),
    )


HANDLERS = {
    "impact_analysis": impact_analysis,
    "anomaly_detection": anomaly_detection,
    "fraud_score": fraud_score,
    "kpi_summary": kpi_summary,
    "root_cause_hypotheses": root_cause_hypotheses,
    "accuracy_report": accuracy_report,
    "activity_report": activity_report,
    "trend_scan": trend_scan,
    "trend_candidates": trend_candidates,
    "trend_history": trend_history,
    "youtube_velocity": youtube_velocity,
    "content_recommend": content_recommend,
    "trend_popular": trend_popular,
    "promo_concepts": promo_concepts,
    "shorts_stats": shorts_stats,
    "answer_planning_question": answer_planning_question,
    "campaign_effect": campaign_effect,
}
