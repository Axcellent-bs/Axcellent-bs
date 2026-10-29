"""data 에이전트 skill 핸들러.

- impact_analysis, anomaly_detection은 읽기 전용(grade read)이라 `execute()`를 거치지 않는다.
- kpi_summary는 공통 로그 저장소를 읽기만 한다(grade read).
- root_cause_hypotheses는 다른 부서에 조회만 하고(A2A) 원인 후보를 고른다(grade read).
- fraud_score는 shop-api 판매자 정보·가격을 읽어 규칙 점수를 낸다(grade read). 승인·보류는 정하지
  않는다. 확인할 수 없는 규칙은 점수에서 빼고 `unknown_rules`로 알린다. 없는 판매자는 `not_found`다.
- 외부 입력(`request.untrusted`)은 읽지 않는다.
- shop-api에서 데이터를 받지 못하면(`ShopUnavailable`) 숫자를 0으로 내지 않고 `failed`·`unavailable`
  과 "확인할 수 없음" 근거로 답한다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

from pydantic import BaseModel, ValidationError

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.data import (
    AccuracyReportInput,
    AnomalyDetectionInput,
    FraudScoreInput,
    ImpactAnalysisInput,
    KpiSummaryInput,
    RootCauseInput,
)
from data_agent.clients.fact_sources import notices_at
from data_agent.clients.log_client import get_log_source
from data_agent.clients.shop_client import (
    HttpSellerClient,
    SellerClient,
    ShopUnavailable,
    shop_api,
)
from data_agent.skills.accuracy_report import eval_dir, load_report
from data_agent.skills.anomaly_detection import detect_anomalies, env_settings, get_metric_source
from data_agent.skills.fraud_score import score_fraud
from data_agent.skills.impact_analysis import analyze_impact
from data_agent.skills.kpi_summary import summarize_kpi
from data_agent.skills.root_cause import estimate_root_cause


def get_llm() -> LLMClient:
    """설명 문장용 LLM. 테스트는 이 함수를 바꿔 끼운다. 미설정이면 explain이 템플릿으로 대체한다."""
    return LLMClient("data")


def get_seller_client() -> SellerClient:
    """판매자 정보 클라이언트(shop-api). 테스트·평가는 이 함수를 바꿔 끼운다.

    목 판매자 데이터는 없어서 `DATA_MOCK`이어도 shop-api를 부른다(지어내지 않는다).
    """
    return HttpSellerClient(shop_api(os.environ))


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


async def fraud_score(request: AgentRequest) -> AgentResult:
    data = _parse(request, FraudScoreInput)
    if isinstance(data, AgentResult):
        return data
    try:
        result = await score_fraud(data, get_seller_client())
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


HANDLERS = {
    "impact_analysis": impact_analysis,
    "anomaly_detection": anomaly_detection,
    "fraud_score": fraud_score,
    "kpi_summary": kpi_summary,
    "root_cause_hypotheses": root_cause_hypotheses,
    "accuracy_report": accuracy_report,
}
