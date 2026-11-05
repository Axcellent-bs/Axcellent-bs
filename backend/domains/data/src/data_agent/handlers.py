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
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any

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
    TrendAgeOutput,
    TrendAgeRequest,
    TrendCandidatesRequest,
    TrendHistoryRequest,
    TrendInsightBriefRequest,
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
from data_agent.explain import ExplainCache
from data_agent.planning_questions import answer_question
from data_agent.report_store import ReportStore, get_report_store
from data_agent.response_cache import DB_ENV as RESPONSES_DB_ENV
from data_agent.response_cache import ResponseCache
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
from data_agent.skills.trend_age import popular_targets
from data_agent.skills.trend_age import trend_age as run_trend_age
from data_agent.skills.trend_candidates import find_candidates
from data_agent.skills.trend_history import history
from data_agent.skills.trend_popular import popular
from data_agent.skills.trend_scan import NoTrendGroups, TrendGroupsUnknown, scan_trends
from data_agent.skills.youtube_velocity import measure_velocity_named
from data_agent.stage_timing import note_cache, stage, timed_handler


class TimedLLMClient(LLMClient):
    """LLM 응답 대기 시간을 요청 단계 기록(`stage_timing`)의 llm에 더한다. 동작은 그대로."""

    async def structured(self, *args: Any, **kwargs: Any) -> Any:
        with stage("llm"):
            return await super().structured(*args, **kwargs)

    async def embed(self, texts: list[str], context_id: str | None = None) -> list[list[float]]:
        with stage("llm"):
            return await super().embed(texts, context_id)


def get_llm() -> LLMClient:
    """설명 문장용 LLM. 테스트는 이 함수를 바꿔 끼운다. 미설정이면 explain이 템플릿으로 대체한다."""
    return TimedLLMClient("data")


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


def _profile_seeds(context_id: str | None):  # noqa: ANN202
    """기본 키워드 그룹용 씨앗: 도메인 프로필(저장 재사용)의 씨앗 키워드. 못 얻으면 이유."""

    async def provide() -> tuple[list[str], str | None]:
        from ax_contracts.schemas.data import DomainProfileRequest
        from data_agent.skills.domain_profile import make_profile
        from data_agent.skills.product_link import read_catalog

        catalog = await read_catalog(get_catalog())
        got = await make_profile(
            DomainProfileRequest(), get_llm(), env=os.environ, catalog=catalog,
            context_id=context_id,
        )  # fmt: skip
        out = got.output
        return [s.keyword for s in out.seed_keywords], out.reason

    return provide


async def trend_scan(request: AgentRequest) -> AgentResult:
    """키워드 그룹별 검색어 급상승. 조회만 하고 상태를 바꾸지 않는다(grade read)."""
    data = _parse(request, TrendScanRequest)
    if isinstance(data, AgentResult):
        return data
    try:
        result = await scan_trends(
            data, get_trend_connector(), get_llm(), env=os.environ,
            category_cache=get_category_cache(),
            seed_provider=_profile_seeds(request.meta.context_id),
            explain_cache=EXPLAINED,
        )  # fmt: skip
    except TrendGroupsUnknown as error:
        return _result(
            request, TaskStatus.FAILED, outcome="unknown", output={"message": str(error)}
        )
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


async def _stored(
    request: AgentRequest,
    skill: str,
    payload: dict[str, Any],
    compute: Callable[[], Awaitable[tuple[str, dict[str, Any], Evidence]]],
) -> AgentResult:
    """저장된 계산 결과가 있으면 읽기만, 없으면 계산해 저장 (`response_cache` 참고).

    키 = skill + 입력 + 데이터 도장 + 규칙 버전. 수집기가 새 스냅샷 직후 같은 키로 미리 넣는다.
    계산이 끝난 뒤의 도장으로 저장한다 (이 요청이 새로 분류해 저장분이 바뀌었을 수 있다).
    """
    from data_agent.response_cache import data_stamp, rules_version
    from data_agent.today_board import BOARD

    key = RESPONSES.key(skill, payload, data_stamp(os.environ, BOARD.stamp()), rules_version())
    if (hit := RESPONSES.get(key)) is not None:
        note_cache("hit")
        outcome, output, evidence = hit
        return _result(request, TaskStatus.COMPLETED, outcome=outcome, output=output,
                       evidence=Evidence.model_validate(evidence))  # fmt: skip
    outcome, output, evidence = await compute()
    after = RESPONSES.key(skill, payload, data_stamp(os.environ, BOARD.stamp()), rules_version())
    RESPONSES.put(after, [outcome, output, evidence.model_dump(mode="json")], skill)
    note_cache("stored")
    return _result(request, TaskStatus.COMPLETED, outcome=outcome, output=output,
                   evidence=evidence)  # fmt: skip


async def trend_popular(request: AgentRequest) -> AgentResult:
    """지금 인기 주제 순위 (급상승 판정과 별개, 네이버 안 부름). 조회만 한다(grade read).

    수집기가 미리 계산해 둔 결과를 읽는다. 없을 때만 지금 계산하고 저장한다(`_stored`).
    """
    data = _parse(request, TrendPopularRequest)
    if isinstance(data, AgentResult):
        return data

    async def compute() -> tuple[str, dict[str, Any], Evidence]:
        result = await popular(
            data,
            get_llm(),
            env=os.environ,
            context_id=request.meta.context_id,
            category_cache=get_category_cache(),
            youtube=get_youtube(),
        )
        outcome = "ranked" if result.output.items else "empty"
        return outcome, result.output.model_dump(mode="json"), result.evidence

    return await _stored(request, "trend_popular", data.model_dump(mode="json"), compute)


def get_youtube() -> YouTubeConnector:
    """YouTube Data API 커넥터 (키는 .env). 테스트는 이 함수를 바꿔 끼운다."""
    return YouTubeConnector(os.environ)


async def shorts_stats(request: AgentRequest) -> AgentResult:
    """숏츠 통계 (카테고리별·형식별). 검색은 하루 1번 저장, 조회만 한다(grade read)."""
    data = _parse(request, ShortsStatsRequest)
    if isinstance(data, AgentResult):
        return data

    async def compute() -> tuple[str, dict[str, Any], Evidence]:
        result = await run_shorts_stats(
            data,
            get_youtube(),
            get_llm(),
            env=os.environ,
            context_id=request.meta.context_id,
            category_cache=get_category_cache(),
        )
        return result.output.status, result.output.model_dump(mode="json"), result.evidence

    return await _stored(request, "shorts_stats", data.model_dump(mode="json"), compute)


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


async def _season_keywords(env: Mapping[str, str], limit: int) -> list[str]:
    """시즌 키워드: 오늘 만든 기획안의 시즌 확인에서 기준을 넘은 것, 없으면 카탈로그에서 코드로."""
    from data_agent.skills import promo_concepts as promo
    from data_agent.skills.product_link import read_catalog

    if limit <= 0:
        return []
    config = promo.load_config(env)
    store = promo.PromoStore(env.get(promo.DB_ENV) or promo.DEFAULT_DB)
    try:
        hit = store.latest(promo._today(datetime.now(UTC)))
    finally:
        store.close()
    if hit is not None:
        passed = [
            c.keyword for c in hit.season_checks
            if c.status == "known" and max(c.same_month_ratio or 0, c.next_month_ratio or 0)
            >= config.season_min_ratio
        ]  # fmt: skip
        if passed:
            return passed[:limit]
    shop = await read_catalog(get_catalog())
    return [k for k, _ in promo.season_keywords(shop.products, shop.categories, limit)]


async def trend_age(request: AgentRequest) -> AgentResult:
    """연령대별 상승 (네이버 연령대별 조회, 하루 상한·캐시). 상태를 바꾸지 않는다(grade read)."""
    from data_agent.skills.trend_age import load_config as load_age_config

    data = _parse(request, TrendAgeRequest)
    if isinstance(data, AgentResult):
        return data
    config = load_age_config(os.environ)
    popular_items: list[tuple[str, list[str]]] = []
    season: list[str] = []
    if not data.keywords:
        popular_items = await popular_targets(
            get_llm(), os.environ, datetime.now(UTC), config.popular_topics * 2,
            category_cache=get_category_cache(),
        )  # fmt: skip
        season = await _season_keywords(os.environ, config.season_topics)
    result = await run_trend_age(
        data, get_trend_connector(), env=os.environ, popular_items=popular_items,
        season_keywords=season, caller="trend_age",
    )  # fmt: skip
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


def cand_aliases(env: Mapping[str, str]) -> dict[str, tuple[str, ...]]:
    """회사 별칭 표 `[candidates.aliases]` (대표 이름 = 별칭들)."""
    from data_agent.skills import trend_candidates as cand_mod

    return dict(cand_mod.load_config(env).aliases)


async def trend_insight_brief(request: AgentRequest) -> AgentResult:
    """기획에 넘기는 인사이트 3~7개 (기존 skill 결과를 코드로 묶음, 하루 캐시). grade read."""
    from data_agent.skills import promo_concepts as promo
    from data_agent.skills.product_link import read_catalog
    from data_agent.skills.trend_insight_brief import cached_brief, make_brief
    from data_agent.skills.trend_popular import popular
    from data_agent.today_board import BOARD

    data = _parse(request, TrendInsightBriefRequest)
    if isinstance(data, AgentResult):
        return data
    env = os.environ
    # 오늘 저장된 브리프가 있으면 재료(지금 인기·숏츠·상품)를 계산하지 않고 바로 준다
    if (hit := cached_brief(data, env)) is not None:
        note_cache("hit")
        output = hit.output.model_dump(mode="json")
        return _result(request, TaskStatus.COMPLETED, outcome=hit.output.status, output=output,
                       evidence=hit.evidence)  # fmt: skip
    llm = get_llm()
    popular_out = None
    popular_why: str | None = None
    try:
        pop = await popular(
            TrendPopularRequest(), llm, env=env, context_id=request.meta.context_id,
            category_cache=get_category_cache(), youtube=get_youtube(),
        )  # fmt: skip
        popular_out = pop.output
        popular_why = pop.output.empty_reason
    except Exception as error:  # 지금 인기를 못 읽어도 급상승만으로 묶는다
        popular_why = f"지금 인기를 읽지 못함 ({type(error).__name__})"
    rising = [v for n in BOARD.rising_names() if (v := BOARD.verdict([n])) is not None]
    store = promo.PromoStore(env.get(promo.DB_ENV) or promo.DEFAULT_DB)
    try:
        promo_out = store.latest(promo._today(datetime.now(UTC)))
    finally:
        store.close()
    catalog = await read_catalog(get_catalog())
    shorts_out = None
    try:  # 숏츠 형식 값은 숏츠 통계와 같은 시점 (검색은 하루 1번 저장 재사용)
        shorts_out = (
            await run_shorts_stats(
                ShortsStatsRequest(), get_youtube(), llm, env=env,
                context_id=request.meta.context_id, category_cache=get_category_cache(),
            )
        ).output  # fmt: skip
    except Exception:  # 숏츠 통계를 못 받아도 브리프는 만든다 (형식 칸만 비움)
        shorts_out = None

    async def ages(keywords: list[str]) -> TrendAgeOutput:
        result = await run_trend_age(
            TrendAgeRequest(keywords=keywords[:30]),
            get_trend_connector(),
            env=env,
            caller="trend_insight_brief",
        )
        return result.output

    async def opportunities():  # noqa: ANN202
        return await _opportunities(request.meta.context_id, shorts_out, ages)

    result = await make_brief(
        data, llm, env=env, popular=popular_out, popular_reason=popular_why, rising=rising,
        age_fn=ages, promo=promo_out, catalog=catalog, aliases=cand_aliases(env),
        category_cache=get_category_cache(), shorts=shorts_out,
        context_id=request.meta.context_id, opportunities_fn=opportunities,
    )  # fmt: skip
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
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
        seed_provider=_profile_seeds(request.meta.context_id),
    )
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="recommended" if result.output.items else "empty",
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
        fallback=result.fallback,
    )


async def domain_profile(request: AgentRequest) -> AgentResult:
    """도메인 프로필 (회사 소개 + 카탈로그 → 업종·상품군·고객 축·질문·씨앗 키워드). grade read."""
    from ax_contracts.schemas.data import DomainProfileRequest
    from data_agent.skills.domain_profile import make_profile
    from data_agent.skills.product_link import read_catalog

    data = _parse(request, DomainProfileRequest)
    if isinstance(data, AgentResult):
        return data
    result = await make_profile(
        data, get_llm(), env=os.environ, catalog=await read_catalog(get_catalog()),
        context_id=request.meta.context_id,
    )  # fmt: skip
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def data_sources(request: AgentRequest) -> AgentResult:
    """데이터 출처 목록 (능력·상태·승인). 확인이 필요한 출처만 처음 한 번 부른다. grade read."""
    from ax_contracts.schemas.data import DataSourcesRequest
    from data_agent.skills.data_sources import list_sources

    data = _parse(request, DataSourcesRequest)
    if isinstance(data, AgentResult):
        return data
    result = await list_sources(os.environ)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome="known",
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def _plan_inputs(context_id: str | None):  # noqa: ANN202
    """수집 계획 재료: 도메인 프로필(저장 재사용)과 출처 목록."""
    from ax_contracts.schemas.data import DomainProfileRequest
    from data_agent.skills.data_sources import list_sources
    from data_agent.skills.domain_profile import make_profile
    from data_agent.skills.product_link import read_catalog

    catalog = await read_catalog(get_catalog())
    profile = await make_profile(
        DomainProfileRequest(), get_llm(), env=os.environ, catalog=catalog, context_id=context_id
    )
    sources = await list_sources(os.environ)
    return catalog, profile.output, sources.output


def _subtopic_pending(profile: object) -> int:
    """오늘 새로 할 하위 주제 숏츠 검색 수 (캐시 밖, 하루 상한 안). 수집 계획 할당량에 넣는다."""
    from data_agent.skills import domain_subtopics as ds

    seeds = [s.keyword for s in getattr(profile, "seed_keywords", [])]
    store = ds.SubtopicStore(os.environ.get(ds.DB_ENV) or ds.DEFAULT_DB)
    try:
        return ds.pending_searches(seeds, store, ds.load_config(os.environ), datetime.now(UTC))
    finally:
        store.close()


def _subtopic_baseline(profile: object) -> tuple[float, int] | None:
    """씨앗 검색 숏츠 전체 조회수 중앙값 (하위 주제 숏츠 적합도의 분모)."""
    from data_agent.skills import domain_subtopics as ds

    seeds = [s.keyword for s in getattr(profile, "seed_keywords", [])]
    store = ds.SubtopicStore(os.environ.get(ds.DB_ENV) or ds.DEFAULT_DB)
    try:
        return ds.baseline(seeds, store, ds.load_config(os.environ), datetime.now(UTC))
    finally:
        store.close()


async def _subtopics(profile: object, context_id: str | None):  # noqa: ANN202
    """하위 주제 넓히기 (씨앗 → 한국 숏츠 검색, 일주일 캐시·하루 상한 → 함께 나온 말 → 축)."""
    from ax_contracts.schemas.data import DomainSubtopicsRequest
    from data_agent.skills.domain_subtopics import make_subtopics
    from data_agent.youtube_store import DB_ENV as YT_DB_ENV
    from data_agent.youtube_store import DEFAULT_DB as YT_DEFAULT_DB
    from data_agent.youtube_store import popular_history

    try:
        history = popular_history(os.environ.get(YT_DB_ENV) or YT_DEFAULT_DB)
    except Exception:  # 쌓인 인기 영상이 없으면 흔한 말 제외만 못 한다
        history = []
    return await make_subtopics(
        DomainSubtopicsRequest(), get_llm(), env=os.environ, profile=profile,
        youtube=get_youtube(), history=history, context_id=context_id,
    )  # fmt: skip


async def domain_subtopics(request: AgentRequest) -> AgentResult:
    """하위 주제 넓히기 (씨앗 키워드 → YouTube 한국 숏츠 → 함께 나오는 말 → 축 분류). grade read."""
    from ax_contracts.schemas.data import DomainSubtopicsRequest

    data = _parse(request, DomainSubtopicsRequest)
    if isinstance(data, AgentResult):
        return data
    _, profile, _ = await _plan_inputs(request.meta.context_id)
    result = await _subtopics(profile, request.meta.context_id)
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


async def _opportunities(context_id: str | None, shorts: object, ages: object):  # noqa: ANN202
    """기회 가설 카드: 프로필 → 계획(저장 재사용) → 사용 가능·승인 출처만 수집."""
    from ax_contracts.schemas.data import SourcePlanRequest
    from data_agent.skills.domain_opportunity import default_consumption, make_opportunities
    from data_agent.skills.source_plan import make_plan, shopping_verifier
    from data_agent.skills.topic_category import TopicInput, classify_topics
    from data_agent.skills.trend_scan import yesterday_kst

    llm = get_llm()
    catalog, profile, sources = await _plan_inputs(context_id)
    plan = await make_plan(
        SourcePlanRequest(), llm, env=os.environ, profile=profile, sources=sources,
        youtube_categories=get_youtube().video_categories,
        verify_shopping=shopping_verifier(os.environ), context_id=context_id,
        subtopic_pending=_subtopic_pending(profile),
    )  # fmt: skip
    subtopics = (await _subtopics(profile, context_id)).output
    baseline = _subtopic_baseline(profile)

    async def classify(names: list[str]) -> dict[str, str | None]:
        found = await classify_topics(
            [TopicInput(n, ()) for n in names], llm, cache=get_category_cache(),
            context_id=context_id,
        )  # fmt: skip
        return {n: (found[n].category if n in found else None) for n in names}

    end = yesterday_kst()
    return await make_opportunities(
        env=os.environ, llm=llm, profile=profile, plan=plan.output, sources=sources,
        shorts=shorts, catalog_products=catalog.products, classify=classify,
        trend_connector=get_trend_connector(), age_fn=ages,
        consumption_fn=default_consumption(os.environ, end), day=datetime.now(UTC).date(),
        end=end, context_id=context_id, subtopics=subtopics, baseline=baseline,
    )  # fmt: skip


async def source_plan(request: AgentRequest) -> AgentResult:
    """수집 계획 (프로필 + 출처 → LLM이 고르고 코드가 검증, 저장). 수집은 안 함. grade read."""
    from ax_contracts.schemas.data import SourcePlanRequest
    from data_agent.skills.source_plan import make_plan, shopping_verifier

    data = _parse(request, SourcePlanRequest)
    if isinstance(data, AgentResult):
        return data
    _, profile, sources = await _plan_inputs(request.meta.context_id)
    result = await make_plan(
        data, get_llm(), env=os.environ, profile=profile, sources=sources,
        youtube_categories=get_youtube().video_categories,
        verify_shopping=shopping_verifier(os.environ), subtopic_pending=_subtopic_pending(profile),
        context_id=request.meta.context_id,
    )  # fmt: skip
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=result.output.status,
        output=result.output.model_dump(mode="json"),
        evidence=result.evidence,
    )


_category_cache: topic_category.CategoryCache | None = None
# 트렌드 감지 탭 계산 결과 저장(`DATA_TREND_RESPONSES_DB` SQLite, 없으면 메모리)과 설명 문장 캐시
RESPONSES = ResponseCache(os.environ.get(RESPONSES_DB_ENV) or None)
EXPLAINED = ExplainCache()


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
        result = await history(
            data, get_trend_connector(), env=os.environ,
            seed_provider=_profile_seeds(request.meta.context_id),
        )  # fmt: skip
    except TrendGroupsUnknown as error:
        return _result(
            request, TaskStatus.FAILED, outcome="unknown", output={"message": str(error)}
        )
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

    async def compute() -> tuple[str, dict[str, Any], Evidence]:
        result = await measure_velocity_named(data, env=os.environ, youtube=get_youtube())
        return result.output.status, result.output.model_dump(mode="json"), result.evidence

    return await _stored(request, "youtube_velocity", data.model_dump(mode="json"), compute)


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


# 트렌드 감지 탭이 부르는 skill은 단계별 시간(sqlite·외부·LLM·계산 ms)을 로그 한 줄로 남긴다
TIMED_SKILLS = frozenset({
    "trend_popular", "trend_scan", "trend_candidates", "youtube_velocity", "shorts_stats",
    "promo_concepts", "trend_insight_brief",
})  # fmt: skip

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
    "trend_age": trend_age,
    "trend_insight_brief": trend_insight_brief,
    "domain_profile": domain_profile,
    "data_sources": data_sources,
    "domain_subtopics": domain_subtopics,
    "source_plan": source_plan,
}
HANDLERS = {k: timed_handler(k, v) if k in TIMED_SKILLS else v for k, v in HANDLERS.items()}
