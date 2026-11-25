"""의뢰 흐름 SVG 콘티 + [Higgsfield 그림으로 다시 그리기] 전용 스킬 (11/25).

가짜 LLM·가짜 Higgsfield만 쓴다.

- 의뢰 흐름: 칸을 코드로 다 짠 뒤 회사 LLM 1회 → 칸별 svg만 채운다(칸 문장·시간 그대로)
- LLM이 없거나 실패하거나 svg가 다 버려지면 "layout", 흐름은 멈추지 않는다
- 다시 그리기(`creative_board_redraw`): LLM 0회, force, 관문 보류면 멈춤, 다른 회사 기록은 not_found
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from ax_agent_template.llm import LLMResponseError, MockLLMClient
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from marketing_agent import board_svg as bs
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import handlers, request_creative, video_draft


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


Q = _load("request_creative_helpers_board_svg", "test_request_creative.py")
M = _load("creative_render_mcp_helpers_board_svg", "test_creative_render_mcp.py")
env = Q.env
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(64)


def _svg(word: str) -> str:
    return (
        '<svg viewBox="0 0 90 160"><rect x="1" y="1" width="88" height="158" fill="#eee"/>'
        f'<circle cx="45" cy="80" r="10" fill="orange"/><text x="45" y="12">{word}</text></svg>'
    )


def _llm(panels: list[dict[str, Any]]) -> MockLLMClient:
    read = bs.BoardSvgRead.model_validate({"panels": panels})
    return MockLLMClient(responses={bs.BoardSvgRead: read})


def _draw(rid: str, factory: Any) -> str:
    return asyncio.run(bs.draw(rid, Q.CTX, factory))


def _prepared(env: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    rid, why = Q._prepare(Q._brand(env))  # noqa: SLF001
    assert why is None and rid
    return rid, env["store"].get(rid)["storyboard"]


# ---------------------------------------------------------------- 의뢰 흐름 SVG


def test_request_flow_asks_the_llm_once_and_fills_only_svg(env: dict[str, Any]) -> None:
    rid, before = _prepared(env)
    assert before["board_source"] == "layout"
    llm = _llm([{"no": n, "svg": _svg(f"칸{n}")} for n in (1, 2, 3)])
    assert _draw(rid, lambda: llm) == "svg"
    assert len(llm.calls) == 1
    after = env["store"].get(rid)["storyboard"]
    assert after["board_source"] == "svg"
    assert all(s["svg"] and "<circle" in s["svg"] for s in after["shots"])
    # 칸 문장·구도·프롬프트·길이는 코드 결과 그대로
    for key in ("scene_ko", "composition_ko", "prompt_en", "shot_size", "no"):
        assert [s[key] for s in after["shots"]] == [s[key] for s in before["shots"]]
    assert after["sheet_prompt_en"] == before["sheet_prompt_en"]
    assert after.get(rr.PLAN_DURATION_KEY) == before.get(rr.PLAN_DURATION_KEY)
    # 장면 글은 데이터 블록으로만 간다
    assert "책방 문을 여는 고양이" in llm.calls[0]
    # 같은 의뢰를 다시 준비해도 LLM을 또 부르지 않는다
    assert _draw(rid, lambda: llm) == "skipped"
    assert len(llm.calls) == 1


def test_some_panels_svg_is_still_svg_and_others_stay_empty(env: dict[str, Any]) -> None:
    rid, _ = _prepared(env)
    bad = '<svg viewBox="0 0 90 160"><script>alert(1)</script></svg>'.replace("<svg", "<div")
    llm = _llm([{"no": 1, "svg": _svg("훅")}, {"no": 2, "svg": bad}, {"no": 9, "svg": _svg("x")}])
    assert _draw(rid, lambda: llm) == "svg"
    shots = env["store"].get(rid)["storyboard"]["shots"]
    assert shots[0]["svg"] and not shots[1].get("svg") and not shots[2].get("svg")


def test_no_llm_keeps_the_layout_and_the_flow(env: dict[str, Any]) -> None:
    rid, before = _prepared(env)
    assert _draw(rid, lambda: None) == "layout"
    after = env["store"].get(rid)["storyboard"]
    assert after["board_source"] == "layout"
    assert [s["scene_ko"] for s in after["shots"]] == [s["scene_ko"] for s in before["shots"]]


def test_llm_failure_or_all_dropped_svg_is_layout(env: dict[str, Any]) -> None:
    class Broken(MockLLMClient):
        async def structured(self, *_a: Any, **_k: Any) -> Any:
            self.calls.append("x")
            raise LLMResponseError("시간 초과")

    rid, _ = _prepared(env)
    broken = Broken()
    assert _draw(rid, lambda: broken) == "layout"
    assert len(broken.calls) == 1
    assert env["store"].get(rid)["storyboard"]["board_source"] == "layout"

    rid2, _ = Q._prepare(Q._brand(env, note="다른 캐릭터"))  # noqa: SLF001
    assert rid2 and rid2 != rid
    dropped = _llm([{"no": 1, "svg": "<html>안 됨</html>"}])
    assert _draw(rid2, lambda: dropped) == "layout"


def test_prepare_skill_draws_svg_after_the_storyboard(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid, _ = _prepared(env)
    seen: list[str] = []

    def fake_prepare(request: AgentRequest, _sns: Any) -> AgentResult:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            output={"creative_record_id": rid},
            evidence=Evidence(queries=["x"], data_summary="x", collected_at=datetime.now(UTC)),
        )

    llm = _llm([{"no": 1, "svg": _svg("훅")}])
    monkeypatch.setattr(video_draft, "prepare_video_draft", fake_prepare)
    monkeypatch.setattr(bs, "LLM", lambda: seen.append("llm") or llm)
    request = AgentRequest(meta=Q._meta(), skill="prepare_video_draft_from_plan", input={})  # noqa: SLF001
    out = asyncio.run(handlers.prepare_video_draft_from_plan_skill(request))
    assert out.status == TaskStatus.COMPLETED and out.output == {"creative_record_id": rid}
    assert seen == ["llm"]
    assert env["store"].get(rid)["storyboard"]["board_source"] == "svg"


def test_prepare_skill_without_a_record_does_not_call_the_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_prepare(request: AgentRequest, _sns: Any) -> AgentResult:
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output={})

    monkeypatch.setattr(video_draft, "prepare_video_draft", fake_prepare)
    monkeypatch.setattr(bs, "LLM", lambda: pytest.fail("LLM을 부르면 안 된다"))
    request = AgentRequest(meta=Q._meta(), skill="prepare_video_draft_from_plan", input={})  # noqa: SLF001
    assert asyncio.run(handlers.prepare_video_draft_from_plan_skill(request)).status == (
        TaskStatus.COMPLETED
    )


def test_code_only_storyboard_is_still_layout_before_svg() -> None:
    board = request_creative.plan_storyboard(Q.PLAN, Q.SHEET_FULL)
    assert board is not None and board["board_source"] == "layout"


# ---------------------------------------------------------------- 다시 그리기 전용 스킬


@pytest.fixture
def mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)
    e = next(gen)
    monkeypatch.setattr(handlers.STATE, "media_ledger", e["ledger"])
    monkeypatch.setattr(bs, "LLM", lambda: pytest.fail("다시 그리기는 LLM을 부르지 않는다"))
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: pytest.fail("LLM 0회"))
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    yield e
    gen.close()


def _virtual(e: dict[str, Any]) -> str:
    with company_scope.using("catbooks"):
        rid = cr.store().add_sheet(M.SHEET | {"kind": "virtual_human"}, None, None)
        rr.save_original(rid, JPEG, "image/jpeg")
        cr.store().attach_storyboard(rid, "들판 이야기", 6, M.BOARD | {"board_source": "svg"})
    return rid


def _redraw(rid: str, company: str = "catbooks") -> AgentResult:
    request = AgentRequest(meta=M.META, skill="creative_board_redraw", input={"record_id": rid})

    async def go() -> AgentResult:
        out = await handlers.creative_board_redraw(request)
        await rr.wait_idle()
        return out

    with company_scope.using(company):
        return asyncio.run(go())


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def test_redraw_makes_the_board_without_the_llm(mcp: dict[str, Any]) -> None:
    rid = _virtual(mcp)
    out = _redraw(rid)
    assert out.status == TaskStatus.COMPLETED
    assert out.output["part"] == "board_image" and out.output["record_id"] == rid
    with company_scope.using("catbooks"):
        assert cr.store().get_render(rid)["board_image"]["status"] == "done"
        assert cr.store().get(rid)["storyboard"]["board_source"] == "higgsfield"
    # 지금 스토리보드 그림 관문 그대로: generate_media(B, 그림)
    assert [a for a, _ in mcp["gate"].calls] == ["generate_media"]


def test_redraw_held_by_the_gate_stops_before_higgsfield(mcp: dict[str, Any]) -> None:
    mcp["gate"].status["generate_media"] = "held"
    rid = _virtual(mcp)
    out = _redraw(rid)
    assert out.status == TaskStatus.COMPLETED
    with company_scope.using("catbooks"):
        board = cr.store().get_render(rid)["board_image"]
    assert board["status"] == "blocked"
    assert mcp["ledger"].month_usage(_month(), "image") == 0
    assert mcp["hf"].waits == 0  # Higgsfield 작업을 기다린 적 없다(부르지 않았다)


def test_redraw_of_another_company_record_is_not_found(mcp: dict[str, Any]) -> None:
    rid = _virtual(mcp)
    out = _redraw(rid, company="other-co")
    assert out.status == TaskStatus.FAILED and out.outcome == "not_found"
    assert mcp["gate"].calls == []


def test_redraw_without_a_storyboard_is_refused(mcp: dict[str, Any]) -> None:
    with company_scope.using("catbooks"):
        rid = cr.store().add_sheet(M.SHEET, None, None)
    out = _redraw(rid)
    assert out.status == TaskStatus.FAILED and out.outcome == "no_storyboard"
    assert mcp["gate"].calls == []


def test_redraw_skill_is_registered() -> None:
    assert handlers.HANDLERS["creative_board_redraw"] is handlers.creative_board_redraw
