"""회사 단어는 카탈로그에서 온다: 테스트 가짜 회사(냉장 물류)로 돌린 문장에 캣북스 단어가 없다.

엔진 문장(이상 탐지 판정·안내, 영향 분석 요약·근거, 리포트)에 "결제"·"주문"·"판매자"가
나오면 실패한다.
캣북스 전용으로 남긴 것(다른 부서 skill 이름 "문의 급증", 판매자 사기 점수, shop-api 커넥터 안내)은
이 경로에 나오지 않는다(README "캣북스 전용으로 남긴 것").
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template.common_log import SqliteLogStore
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_contracts.schemas.data import (
    ActivityReportInput,
    AnomalyDetectionInput,
    CatalogMetric,
    ImpactAnalysisInput,
)
from data_agent import catalog, detect
from data_agent.clients.log_client import CommonLogSource
from data_agent.connectors import EVENTS_LIMIT, ConnectorUnavailable, FailureEvent, FailureEvents
from data_agent.metrics.judge import Settings
from data_agent.skills.activity_report import build_report
from data_agent.skills.anomaly_detection import detect_anomalies
from data_agent.skills.impact_analysis import analyze_impact

FAKE = Path(__file__).resolve().parent / "catalogs" / "coldchain.toml"
FORBIDDEN = ("결제", "주문", "판매자")
END = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
START = END - timedelta(minutes=30)
STEP = timedelta(minutes=5)


def no_catbooks_words(text: str) -> None:
    found = [w for w in FORBIDDEN if w in text]
    assert not found, f"캣북스 단어 {found}: {text}"


class Memory:
    """테스트 전용 메모리 커넥터 (메서드 3개)."""

    def __init__(self, events=(), volume: float | None = 10.0, rate: float = 0.0) -> None:  # noqa: ANN001
        self.events, self.volume, self.rate = list(events), volume, rate

    async def metric_series(self, metric_id, window_from, window_to, bucket="5m"):  # noqa: ANN001, ANN201
        out, at = [], window_from
        while at < window_to:
            if metric_id == "spoilage_rate":
                value = self.rate
            else:
                value = 20.0 if metric_id == "cold_chain_breach_count" and at >= START else 2.0
            out.append((at, value))
            at += STEP
        return out

    async def failure_events(
        self,
        window_from,  # noqa: ANN001
        window_to,  # noqa: ANN001
        *,
        category=None,  # noqa: ANN001
        limit=EVENTS_LIMIT,  # noqa: ANN001
        tenant_id=TENANT_ID_DEFAULT,  # noqa: ANN001
    ) -> FailureEvents:
        picked = [e for e in self.events if window_from <= e.at <= window_to]
        return FailureEvents(picked[:limit])

    async def transactions_volume(self, window_from, window_to):  # noqa: ANN001, ANN201
        if self.volume is None:
            return None
        out, at = [], window_from
        while at < window_to:
            out.append((at, self.volume))
            at += STEP
        return out


def _event(minute: int, code: str, customer: str = "acct_a", amount: int = 1000) -> FailureEvent:
    return FailureEvent(
        at=START + timedelta(minutes=minute),
        code=code,
        customer_id=customer,
        amount=amount,
        category="frozen",
        event_id=f"shp_{minute}",
    )


@pytest.fixture
def coldchain():
    catalog.use_catalog(catalog.load_catalog(str(FAKE)))
    yield catalog.current()
    catalog.use_catalog(None)


# ------------------------------------------------------------------ 이상 탐지
def test_anomaly_sentences_have_no_catbooks_words(coldchain) -> None:
    result = asyncio.run(
        detect_anomalies(
            AnomalyDetectionInput(window_from=START, window_to=END),
            Memory(),
            MockLLMClient(),
            settings=Settings(),
        )
    )
    out = result.output
    assert out.any_anomaly
    no_catbooks_words(out.summary)
    no_catbooks_words(result.evidence.data_summary)
    no_catbooks_words(json.dumps(out.model_dump(mode="json"), ensure_ascii=False))


def test_rate_metric_wording_follows_the_catalog() -> None:
    """비율 지표 안내("5분 X가 N건 미만…")의 X는 카탈로그 `denominator_label`이다."""
    base = catalog.load_catalog(str(FAKE))
    rate = CatalogMetric(
        id="spoilage_rate",
        label="폐기율",
        bad_direction="up",
        unit="비율",
        aggregate="mean",
        denominator="shipment_attempts",
        denominator_label="출고",
    )
    catalog.use_catalog(base.model_copy(update={"metrics": [*base.metrics, rate]}))
    try:
        mem = Memory(volume=1.0, rate=0.1)  # 5분 출고 1건 → 기준(5건) 미만
        judged = asyncio.run(detect.judge_window(mem, START, END, ["spoilage_rate"], Settings()))
        (j,) = judged.judgements
        assert j.status == "unknown"
        assert "5분 출고가 5건 미만이라" in j.note and "(구간 출고 6건)" in j.note
        no_catbooks_words(j.note)
        empty = Memory(volume=0.0)
        (k,) = asyncio.run(
            detect.judge_window(empty, START, END, ["spoilage_rate"], Settings())
        ).judgements
        assert "지난 7일 같은 시간대 출고 기록이 없어" in k.note
    finally:
        catalog.use_catalog(None)


# ------------------------------------------------------------------ 영향 분석
def test_impact_sentences_have_no_catbooks_words(coldchain) -> None:
    events = [_event(1, "REEFER_FAILURE"), _event(3, "DOOR_LEFT_OPEN", "acct_b", 500)]
    data = ImpactAnalysisInput(window_from=START, window_to=END)
    result = asyncio.run(
        analyze_impact(data, TENANT_ID_DEFAULT, MockLLMClient(), connector=Memory(events))
    )
    assert result.output.failed_order_count == 2
    no_catbooks_words(result.output.summary)
    no_catbooks_words(result.evidence.data_summary)
    assert "실패 출고 2건, 영향 거래처 2명, 피해 금액 1500원" in result.evidence.data_summary


def test_impact_unknown_texts_have_no_catbooks_words(coldchain) -> None:
    data = ImpactAnalysisInput(window_from=START, window_to=END)
    no_volume = asyncio.run(
        analyze_impact(
            data,
            TENANT_ID_DEFAULT,
            MockLLMClient(),
            connector=Memory([_event(1, "REEFER_FAILURE")], volume=None),
        )
    )
    assert "평소 비교: 확인할 수 없음" in no_volume.evidence.data_summary
    no_catbooks_words(no_volume.evidence.data_summary)
    with pytest.raises(ConnectorUnavailable) as missing:
        asyncio.run(
            analyze_impact(
                data, TENANT_ID_DEFAULT, MockLLMClient(), connector=Memory([_event(1, "")])
            )
        )
    assert missing.value.code == "NO_FAILURE_REASON"
    no_catbooks_words(str(missing.value))


# ------------------------------------------------------------------ 리포트
class _Finance:
    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        lines = [{"code": "gmv", "amount": 1000}, {"code": "commission_revenue", "amount": 100}]
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"lines": lines})


def test_report_sentences_have_no_catbooks_words(coldchain) -> None:
    logs = CommonLogSource(SqliteLogStore(":memory:"))
    meta = RequestMeta(
        context_id="ctx_req_t",
        token="x",
        tenant_id=TENANT_ID_DEFAULT,
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    result = asyncio.run(
        build_report(
            ActivityReportInput(start=date(2026, 10, 21)),
            meta,
            MockLLMClient(),
            logs=logs,
            metrics=Memory(),
            send=_Finance(),
            now=datetime(2026, 11, 3, tzinfo=UTC),
            enabled=lambda s: True,
            owner=lambda s: None,
        )
    )
    out = result.output
    texts = [
        out.summary,
        *(u.reason for u in out.unknown_sections),
        *(h.label for h in out.highlights),
        *(f.note or "" for f in (out.company.order_count, out.company.payment_success_rate)),
    ]
    for text in texts:
        no_catbooks_words(text)
