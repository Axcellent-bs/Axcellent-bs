"""새 회사·시장 근거 0이어도 의뢰가 사람 확인까지 간다 (11/27 운영 · 새 회사 "가비").

`test_video_request_e2e`(#1831)의 가짜 흐름 그대로, 데이터 보고서만 "설정이 비어 모든 칸 unknown"
(partial)인 보고서의 진짜 `build_insight` 결과(의뢰문 기반 근거 한 줄)를 넘긴다.
총괄은 2단계에서 멈추지 않고 인사이트 → 기획 제작안 → 사람 확인까지 가고, 기획은 의뢰문·회사
캐릭터로 제작안을 쓴다. 영상 초안 준비는 누르지 않는다(사람 확인에서 멈춘다)."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult
from ax_contracts.schemas.data import TrendReport, TrendReportInput
from ax_orchestrator import request_tasks
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests
from data_agent.skills import trend_report as tr
from data_agent.skills import trend_report_brief as tb
from data_agent.skills import trend_report_insight as ti


def _load(name: str, file: Path) -> Any:
    spec = importlib.util.spec_from_file_location(name, file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


E = _load("video_request_e2e_helpers_no_evidence",
          Path(__file__).with_name("test_video_request_e2e.py"))
world = E.world
REQUEST_TEXT = "우리 앱을 홍보할만한 10초짜리 영상"
NOW = datetime(2026, 11, 27, 6, 17, tzinfo=UTC)


def _empty_report() -> TrendReport:
    """새 회사: 검색 키워드·회사/경쟁사 인스타 설정이 없어 모든 칸 unknown."""
    brief = tb.input_brief(TrendReportInput(product="가비 가족 비서 앱", format="10초 숏폼"))
    report = TrendReport(report_id="tr_gabi_e2e", status="running", created_at=NOW,
                         caller="orchestrator", brief=brief, finished_at=NOW)  # fmt: skip
    for name in tr.SECTIONS:
        getattr(report.sections, name).status = "unknown"
    report.sections.theme.status = "skipped"
    report.status = tr.final_status(report)  # type: ignore[assignment]
    return report


class NoEvidenceDepartments(E.Departments):
    async def _handle(self, req: AgentRequest) -> AgentResult:
        if req.skill == "trend_report_insight":
            insight, why = ti.build_insight(_empty_report())
            assert why is None and insight is not None
            return E._result(report_id="tr_gabi_e2e", insight=insight.model_dump(mode="json"))
        return await super()._handle(req)


async def _until_confirm(e: dict[str, Any], llm: Any) -> tuple[WorkRequests, str, Any]:
    brand = E._brand(e)
    reqs = WorkRequests()
    rid = reqs.create("member", REQUEST_TEXT)["request_id"]
    plan = request_tasks.plan_of(request_tasks.BY_NAME["promo_video"])
    reqs.set_plan(rid, task="promo_video", confidence=0.9, reason="r", alternatives=[], plan=plan)
    reqs.link_thread(rid, "orchchat-gabi")
    depts = NoEvidenceDepartments(llm, e["ledger"])
    runner = RequestRunner(reqs, lambda: depts, retry_delay=0, notify=lambda _t, _m: None)
    await runner.run(rid)
    assert runner.answer_brand_character(rid, brand, by="태윤") == ("ok", False)
    await runner.wait_idle()
    return reqs, rid, depts


def test_new_company_without_market_evidence_reaches_the_human_check(world: dict[str, Any]) -> None:
    assert _empty_report().status == "partial"  # 예전 failed → 의뢰가 2단계에서 멈췄다
    reqs, rid, depts = E._run(_until_confirm(world, E._planning_llm()))
    steps = reqs.get(rid)["steps"]
    assert steps["trend"]["status"] == "completed" and steps["insight"]["status"] == "completed"
    assert steps["plan"]["status"] == "completed"
    assert steps["confirm"]["status"] == "waiting_human"  # 사람 확인에서 멈춘다
    assert depts.calls == ["trend_report_create", "trend_report_insight", "receive_insight"]
    # 기획: 의뢰문 기반 근거로 제작안을 쓴다 (근거 0이라 거절·규칙 초안으로 떨어지지 않는다)
    p = next(x for x in depts.planning.state.proposals if x.video_plan is not None)
    assert [(f.label, f.source) for f in p.facts] == [(ti.REQUEST_BASED, ti.REQUEST_SOURCE)]
    assert p.video_plan is not None and p.video_plan.written_by == "llm"
    # 고른 회사 캐릭터 번호가 기획까지 간다 (외형 읽기는 마케팅 A2A, 이 가짜 흐름 밖)
    assert p.brand_character_record_id and p.brand_character_record_id.startswith("CR-")
    assert p.request_text == REQUEST_TEXT
