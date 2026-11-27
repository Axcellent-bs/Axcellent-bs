"""새 회사에서 보고서 근거가 없어도 의뢰가 이어진다 (11/27 운영 · 새 회사 "가비").

의뢰 "우리 앱을 홍보할만한 10초짜리 영상": 검색 키워드·회사/경쟁사 인스타 설정이 비어 모든 칸이
unknown → 보고서 failed → 총괄이 2단계에서 의뢰를 멈췄다. 오류가 아니라 설정이 비어 못 본 것.
재현 님 원칙: 의뢰 문장이 출발점이고 시장 자료는 있으면 더하는 참고(설정을 채우라고 하지 않는다).
가짜만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.data import TrendReport, TrendReportInput
from data_agent import trend_report_hooks as hooks
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.skills import trend_report_insight as ti


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_no_evidence", "test_trend_report.py")
NOW = datetime(2026, 11, 27, 6, 17, tzinfo=UTC)
WHY = {
    "search": "검색어 트렌드 값 없음",
    "hashtags": "읽은 인스타 계정이 없음",
    "content": "읽은 인스타 계정이 없음",
    "competitors": "경쟁사 인스타 아이디가 없음",
    "recommendation": "근거가 없어 추천을 만들지 않음",
}


def _gabi(states: dict[str, str] | None = None) -> TrendReport:
    """가비 의뢰 보고서: 제품·대상·길이는 의뢰문에서 읽었고 칸은 모두 못 봄."""
    brief = tb.input_brief(TrendReportInput(product="가비 가족 비서 앱", format="10초 숏폼"))
    report = TrendReport(report_id="tr_gabi0001", status="running", created_at=NOW,
                         caller="orchestrator", brief=brief, finished_at=NOW)  # fmt: skip
    for name in tr.SECTIONS:
        section = getattr(report.sections, name)
        section.status = (states or {}).get(name, "unknown")
        section.reason = WHY[name]
    report.sections.theme.status = "skipped"
    return report


def test_all_unknown_is_partial_with_one_limit_per_section() -> None:
    report = _gabi()
    assert tr.final_status(report) == "partial"
    lines = tr.unseen_limits(report)
    assert lines == ["검색: 검색어 트렌드 값 없음", "해시태그: 읽은 인스타 계정이 없음",
                     "콘텐츠: 읽은 인스타 계정이 없음", "경쟁사: 경쟁사 인스타 아이디가 없음",
                     "AI 추천: 근거가 없어 추천을 만들지 않음"]  # fmt: skip
    # 본 칸이 하나라도 있으면 칸별 줄을 더하지 않는다 (지금 한계 줄 그대로)
    seen = _gabi({"search": "known"})
    assert tr.unseen_limits(seen) == []


def test_one_exception_with_the_rest_unknown_is_partial_and_all_exceptions_fail() -> None:
    assert tr.final_status(_gabi({"content": "failed"})) == "partial"
    broken = _gabi(dict.fromkeys(tr.SECTIONS, "failed"))
    assert tr.final_status(broken) == "failed"
    broken.status = "failed"
    assert ti.build_insight(broken) == (None, ti.NO_FACTS)  # 진짜 실패는 지금처럼 인사이트 없음


def test_no_market_evidence_hands_over_a_request_based_insight() -> None:
    report = _gabi()
    report.status = tr.final_status(report)  # type: ignore[assignment]
    insight, why = ti.build_insight(report)
    assert why is None and insight is not None
    assert [(f.label, f.source) for f in insight.facts] == [(ti.REQUEST_BASED, ti.REQUEST_SOURCE)]
    assert insight.facts[0].value == "가비 가족 비서 앱 · 10초 숏폼"  # 의뢰문에서 읽은 것만
    assert insight.summary == ti.REQUEST_SUMMARY
    assert report.sections.recommendation.beats == []  # AI 추천은 지금처럼 만들지 않는다
    # 완료 알림은 실패가 아니라 완료 (총괄이 다음 단계로)
    out = hooks.completed_output(report)
    assert out["status"] == "partial" and out["has_insight"] is True


def test_request_fact_without_anything_read_says_request_text() -> None:
    brief = tb.input_brief(TrendReportInput())
    report = TrendReport(report_id="tr_gabi0002", status="partial", created_at=NOW,
                         caller="orchestrator", brief=brief)  # fmt: skip
    assert ti.request_fact(report).value == "의뢰 문장"


def test_monthly_volume_only_makes_the_search_section_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    brief = tb.input_brief(TrendReportInput(product="가비", keywords=["가족 비서 앱"]))

    async def volumes(words: list[str], *_a: Any) -> tuple[dict[str, Any], int]:
        return {w: tr.UNDER_10 for w in words}, 1

    monkeypatch.setattr(tr, "monthly_volumes", volumes)
    words = set(brief.keywords)  # 작은 키워드: 검색어 트렌드 값을 하나도 주지 않는다
    search = H.GappySearch(no_filtered=words, no_all=words)
    section, _, _ = asyncio.run(tr.search_section(brief, None, search, None,
                                                  H._env(tmp_path), H.NOW))  # fmt: skip
    assert section.status == "known"  # 예전 unknown (월 검색 10 미만도 사실)
    assert section.rows[0].monthly_volume_note == tr.UNDER_10
