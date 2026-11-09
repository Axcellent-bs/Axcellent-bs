"""이상 탐지 출력의 지표 id 칸 (#221): 캣북스는 예전 칸과 새 칸 둘 다, 다른 회사는 새 칸만."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import AnomalyDetectionInput, AnomalyDetectionOutput
from data_agent import catalog
from data_agent.seed_metrics import MockMetricSource
from data_agent.skills.anomaly_detection import LEGACY_METRICS, detect_anomalies

FAKE = Path(__file__).resolve().parent / "catalogs" / "coldchain.toml"
END = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
START = END - timedelta(minutes=30)


class SpikeSource:
    """5분마다 평소 2건. 냉장 유통 이탈만 구간 안에서 20건으로 튄다 (가짜 회사)."""

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):
        points, at = [], window_from
        while at < window_to:
            spike = metric == "cold_chain_breach_count" and at >= START
            points.append((at, 20.0 if spike else 2.0))
            at += timedelta(minutes=5)
        return points


class EmptySource:
    """아무 점도 없음 → 모든 지표가 확인할 수 없음."""

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):
        return []


@pytest.fixture
def coldchain():
    catalog.use_catalog(catalog.load_catalog(str(FAKE)))
    yield
    catalog.use_catalog(None)


def _detect(source) -> AnomalyDetectionOutput:  # noqa: ANN001
    data = AnomalyDetectionInput(window_from=START, window_to=END)
    out = asyncio.run(detect_anomalies(data, source, MockLLMClient())).output
    # 계약을 그대로 통과하는지 (A2A로 나갈 때와 같다)
    return AnomalyDetectionOutput.model_validate(out.model_dump(mode="json"))


def test_catbooks_output_fills_both_old_and_new_fields() -> None:
    out = _detect(MockMetricSource())
    assert out.verdicts, "캣북스 지표 판정이 있어야 한다"
    assert [v.metric for v in out.verdicts] == [v.metric_id for v in out.metric_verdicts]
    for old, new in zip(out.verdicts, out.metric_verdicts, strict=True):
        assert old.metric_id == old.metric == new.metric_id
        assert old.metric_label == new.metric_label == catalog.label(old.metric)
        assert old.metric_label != old.metric  # 카탈로그의 한국어 이름
        # 같은 판정 값
        assert old.model_dump(exclude={"metric"}) == new.model_dump()
    assert set(out.unknown_metric_ids) == set(out.unknown_metrics)


def test_catbooks_unknown_metrics_fill_both_lists() -> None:
    out = _detect(EmptySource())
    assert out.status == "unknown"
    assert out.unknown_metrics and out.unknown_metrics == out.unknown_metric_ids


def test_fake_company_output_fills_only_new_fields(coldchain) -> None:  # noqa: ANN001
    out = _detect(SpikeSource())
    assert out.verdicts == [] and out.unknown_metrics == []  # 예전 칸에는 못 싣는다
    by_id = {v.metric_id: v for v in out.metric_verdicts}
    assert list(by_id) == ["cold_chain_breach_count", "shipment_delay_count"]
    assert not set(by_id) & LEGACY_METRICS
    breach = by_id["cold_chain_breach_count"]
    assert (breach.metric_label, breach.status, breach.is_anomaly) == (
        "냉장 유통 이탈 건수",
        "anomaly",
        True,
    )
    assert by_id["shipment_delay_count"].metric_label == "출고 지연 건수"
    assert out.any_anomaly and out.status == "anomaly"


def test_fake_company_unknown_metrics_go_only_to_the_new_list(coldchain) -> None:  # noqa: ANN001
    out = _detect(EmptySource())
    assert out.unknown_metrics == []
    assert out.unknown_metric_ids == ["cold_chain_breach_count", "shipment_delay_count"]
