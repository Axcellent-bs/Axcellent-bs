"""지표 카탈로그.

캣북스 값은 예전 코드와 같고, 다른 회사 카탈로그로도 엔진이 캣북스 단어 없이 돈다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template.llm import MockLLMClient
from data_agent import catalog, detect
from data_agent.explain import explain
from data_agent.metrics.judge import Settings

FAKE = Path(__file__).resolve().parent / "catalogs" / "coldchain.toml"
CATBOOKS_WORDS = ("결제", "주문", "고객", "판매자", "쿠폰", "문의", "도서")


@pytest.fixture
def coldchain():
    cat = catalog.load_catalog(str(FAKE))
    catalog.use_catalog(cat)
    yield cat
    catalog.use_catalog(None)


# ------------------------------------------------------------ 캣북스 = 예전 코드에 있던 값
def test_catbooks_catalog_keeps_the_values_that_were_in_the_code() -> None:
    cat = catalog.current()
    assert cat.id == "catbooks"
    assert detect.WATCHED_METRICS == [
        ("payment_fail_rate", "mean"),
        ("order_fail_count", "sum"),
        ("order_count", "sum"),
        ("inquiry_count", "sum"),
    ]
    assert catalog.rate_metric_ids() == frozenset({"payment_fail_rate"})
    assert [m for m in catalog.metric_ids() if catalog.is_drop(m)] == ["order_count"]
    assert catalog.metric("payment_fail_rate").denominator == "payment_count"
    assert catalog.metric("order_fail_count").scale_with_volume is True
    assert cat.volume_metric == "order_count"
    assert catalog.fault_codes() == frozenset(
        {
            "PG_UNAVAILABLE",
            "PG_TIMEOUT",
            "PG_ERROR",
            "PG_5XX",
            "GATEWAY_TIMEOUT",
            "PAYMENT_GATEWAY_TIMEOUT",
            "TIMEOUT",
            "SERVER_ERROR",
            "NETWORK_ERROR",
        }
    )
    assert detect.EMPTY_WINDOW == (
        "구간에 주문·결제 기록이 하나도 없음 (자료가 빠졌는지 정말 0건인지 알 수 없음)"
    )
    assert catalog.label("payment_fail_rate") == "결제 실패율"
    assert (cat.impact.unit_label, cat.impact.customer_label) == ("주문", "고객")


def test_catalog_env_picks_the_file(monkeypatch) -> None:
    monkeypatch.setenv(catalog.CATALOG_ENV, str(FAKE))
    assert catalog.current().id == "coldchain"
    monkeypatch.setenv(catalog.CATALOG_ENV, "catbooks")
    assert catalog.current().id == "catbooks"


def test_subject_particle_follows_the_last_syllable() -> None:
    assert catalog.with_subject("주문") == "주문이"
    assert catalog.with_subject("출고") == "출고가"


# ------------------------------------------------------------ 테스트 전용 가짜 회사
class SyntheticSource:
    """5분마다 평소 2건. 냉장 유통 이탈만 마지막 30분에 5분마다 20건으로 튄다."""

    def __init__(self, spike_from: datetime) -> None:
        self.spike_from = spike_from
        self.asked: list[str] = []

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):
        self.asked.append(metric)
        points, at = [], window_from
        while at < window_to:
            spike = metric == "cold_chain_breach_count" and at >= self.spike_from
            points.append((at, 20.0 if spike else 2.0))
            at += timedelta(minutes=5)
        return points


def test_fake_company_anomaly_uses_its_own_metrics(coldchain) -> None:
    end = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
    start = end - timedelta(minutes=30)
    source = SyntheticSource(spike_from=start)
    judged = asyncio.run(detect.judge_window(source, start, end, None, Settings()))
    by_metric = {j.metric: j for j in judged.judgements}
    assert list(by_metric) == ["cold_chain_breach_count", "shipment_delay_count"]
    assert by_metric["cold_chain_breach_count"].status == "anomaly"
    assert by_metric["cold_chain_breach_count"].direction == "up"
    assert by_metric["shipment_delay_count"].status == "normal"
    assert set(source.asked) == {
        "cold_chain_breach_count",
        "shipment_delay_count",
    }  # 캣북스 지표 안 물음
    assert not judged.empty and not judged.fault_codes_read
    notes = " ".join(j.note for j in judged.judgements)
    assert not any(w in notes for w in CATBOOKS_WORDS)


def test_fake_company_explanations_use_its_own_words(coldchain) -> None:
    llm = MockLLMClient()  # 응답이 없어 템플릿 문장으로 대체된다
    anomaly = asyncio.run(
        explain(
            "anomaly",
            {
                "metric": catalog.label("cold_chain_breach_count"),
                "baseline": 2.0,
                "observed": 20.0,
                "ratio": 10.0,
            },
            llm,
        )
    )
    assert anomaly.fallback
    assert anomaly.summary == "냉장 유통 이탈 건수 지표가 기준선 2.0 대비 20.0로 10.0배입니다."
    impact = asyncio.run(
        explain(
            "impact",
            {"failed_order_count": 3, "affected_customer_count": 2, "damage_amount": 1000},
            llm,
        )
    )
    assert impact.summary == "장애 구간 실패 출고 3건, 영향 거래처 2명, 피해 금액 1,000원입니다."
    for text in (anomaly.summary, impact.summary):
        assert not any(w in text for w in CATBOOKS_WORDS), text
    assert catalog.category_label("frozen") == "냉동"


def test_fake_company_report_metric_names(coldchain) -> None:
    from ax_contracts.schemas.data import ActivityCompany, CompanyFigure
    from data_agent.skills.activity_report import _candidates

    fig = CompanyFigure(value=None, previous_period=None, source="x")
    company = ActivityCompany(
        gmv=fig,
        commission_revenue=fig,
        order_count=fig,
        payment_success_rate=fig,
        incident_count=fig,
    )
    labels = {key: label for key, label, _, _ in _candidates(company, [])}
    assert labels["company.order_count"] == "회사 출고 수"


def test_catbooks_report_and_impact_words_are_unchanged() -> None:
    from ax_contracts.schemas.data import ActivityCompany, CompanyFigure
    from data_agent.skills.activity_report import _candidates

    fig = CompanyFigure(value=None, previous_period=None, source="x")
    company = ActivityCompany(
        gmv=fig,
        commission_revenue=fig,
        order_count=fig,
        payment_success_rate=fig,
        incident_count=fig,
    )
    labels = {key: label for key, label, _, _ in _candidates(company, [])}
    assert labels["company.order_count"] == "회사 주문 수"
    impact = asyncio.run(
        explain(
            "impact",
            {"failed_order_count": 3, "affected_customer_count": 2, "damage_amount": 1000},
            MockLLMClient(),
        )
    )
    assert impact.summary == "장애 구간 실패 주문 3건, 영향 고객 2명, 피해 금액 1,000원입니다."
