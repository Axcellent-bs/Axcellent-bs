"""의뢰 흐름 SVG 콘티 + [Higgsfield 그림으로 다시 그리기] 전용 스킬 (11/25).

가짜 LLM·가짜 Higgsfield만 쓴다.

- 의뢰 흐름: 칸을 코드로 다 짠 뒤 회사 LLM 1회 → 칸별 svg만 채운다(칸 문장·시간 그대로)
- LLM이 없거나 실패하거나 svg가 다 버려지면 "layout", 흐름은 멈추지 않는다
- 다시 그리기(`creative_board_redraw`): LLM 0회, force, 관문 보류면 멈춤, 다른 회사 기록은 not_found
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
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


POSE = {"shot": "medium", "yaw": 35, "head": {"yaw": -40}, "arm_l": {"raise": 40, "bend": 60},
        "label_ko": "돌아봄"}  # fmt: skip


def _llm(panels: list[dict[str, Any]]) -> MockLLMClient:
    read = bs.BoardPoseRead.model_validate({"panels": panels})
    return MockLLMClient(responses={bs.BoardPoseRead: read})


def _is_mannequin(svg: Any) -> bool:
    """3D 마네킹 콘티: 구 음영(radialGradient)·바닥 격자(g stroke)·피부 구(url(#sk))."""
    return bool(svg) and all(
        p in str(svg) for p in ("<radialGradient", 'stroke="#e6dccd"', 'fill="url(#sk)"')
    )


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
    # 11/25: LLM은 칸별 포즈 숫자만, 그림은 공용 렌더러 (svg를 적어 보내도 쓰지 않는다)
    llm = _llm([{"no": n, "pose": POSE, "svg": _svg(f"칸{n}")} for n in (1, 2, 3)])
    assert _draw(rid, lambda: llm) == "svg"
    assert len(llm.calls) == 1
    after = env["store"].get(rid)["storyboard"]
    assert after["board_source"] == "svg"
    assert all(_is_mannequin(s["svg"]) for s in after["shots"])
    assert not any("칸1" in s["svg"] for s in after["shots"])
    sent = llm.calls[0]
    assert "SVG·그림 원문은 쓰지 않는다" in sent and "단순 도형" not in sent
    assert '"shot_size"' in sent and '"camera"' in sent
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
    # 포즈가 온 칸만 그린다 (포즈 없음·모르는 번호는 빈 칸 = 배치판)
    llm = _llm([{"no": 1, "pose": POSE}, {"no": 2, "pose": None}, {"no": 9, "pose": POSE}])
    assert _draw(rid, lambda: llm) == "svg"
    shots = env["store"].get(rid)["storyboard"]["shots"]
    assert _is_mannequin(shots[0]["svg"]) and not shots[1].get("svg") and not shots[2].get("svg")


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
    # LLM이 SVG만 그려 보내고 포즈가 없으면 납작한 SVG를 쓰지 않고 배치판
    dropped = _llm([{"no": n, "svg": _svg("납작")} for n in (1, 2, 3)])
    assert _draw(rid2, lambda: dropped) == "layout"
    assert not any(s.get("svg") for s in env["store"].get(rid2)["storyboard"]["shots"])


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

    llm = _llm([{"no": 1, "pose": POSE}])
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


def _plan_with(**scene3: Any) -> Any:
    scenes = [s.model_copy(update=scene3) if s.n == 3 else s for s in Q.PLAN.scenes]
    return Q.PLAN.model_copy(update={"scenes": scenes})


def test_end_card_panel_is_background_only_and_not_sent_to_the_llm(env: dict[str, Any]) -> None:
    plan = _plan_with(visual="마무리: 채널 이름 엔드 카드")
    rid, why = request_creative.prepare_storyboard(Q._brand(env), Q.CTX, plan)  # noqa: SLF001
    assert why is None and rid
    llm = _llm([{"no": n, "pose": POSE} for n in (1, 2, 3)])
    assert _draw(rid, lambda: llm) == "svg"
    assert "엔드 카드" not in llm.calls[0]  # 엔드카드 칸은 포즈를 묻지 않는다
    shots = env["store"].get(rid)["storyboard"]["shots"]
    assert all(_is_mannequin(s["svg"]) for s in shots[:2])
    end = shots[2]["svg"]
    assert 'fill="url(#bg)"' in end
    for gone in ("<text", "<circle", "url(#sk)", "채널", '<rect x="4"'):
        assert gone not in end


def test_svg_model_env_changes_only_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from ax_agent_template.llm import LLMSettings
    from marketing_agent import pose_board

    base = LLMSettings(model="mk-model", base_url="https://llm.example/v1", api_key="k-test")
    made: list[LLMSettings] = []
    monkeypatch.setattr(pose_board.company_ai, "settings_for", lambda dept: base)
    monkeypatch.setattr(pose_board, "LLMClient", lambda dept, s: made.append(s) or s)
    monkeypatch.delenv(pose_board.MODEL_ENV, raising=False)
    bs.company_llm()
    monkeypatch.setenv(pose_board.MODEL_ENV, "svg-model")
    bs.company_llm()
    assert [m.model for m in made] == ["mk-model", "svg-model"]
    assert all((m.base_url, m.api_key) == (base.base_url, base.api_key) for m in made)


def test_planning_svg_means_zero_marketing_llm_calls(env: dict[str, Any]) -> None:
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320">'
           '<rect x="0" y="0" width="180" height="320" fill="#eee"/></svg>')  # fmt: skip
    scenes = [s.model_copy(update={"start_svg": svg}) for s in Q.PLAN.scenes]
    plan = Q.PLAN.model_copy(update={"scenes": scenes})
    rid, why = request_creative.prepare_storyboard(Q._brand(env), Q.CTX, plan)  # noqa: SLF001
    assert why is None and rid
    llm = _llm([{"no": 1, "pose": POSE}])
    assert _draw(rid, lambda: llm) == "skipped" and llm.calls == []


@pytest.mark.parametrize(
    ("extra", "scene", "want"),
    [
        ({"fallback_reason": "timeout"}, {}, "fallback_reason=timeout"),
        ({"svg_reason": "bad_format"}, {}, "svg_reason=bad_format"),
        ({}, {"start_svg": "<script>alert(1)</script>"}, "sanitize_dropped"),
        ({}, {}, "unknown"),
    ],
)
def test_missing_planning_svg_logs_only_the_record_and_reason(
    env: dict[str, Any], caplog: pytest.LogCaptureFixture, extra: dict[str, Any],
    scene: dict[str, Any], want: str,
) -> None:  # fmt: skip
    scenes = [s.model_copy(update=scene) for s in Q.PLAN.scenes]
    plan = Q.PLAN.model_copy(update={"scenes": scenes, **extra})
    assert request_creative.plan_svg_missing(plan) == want
    with caplog.at_level("INFO", logger="marketing.request_creative"):
        rid, _ = request_creative.prepare_storyboard(Q._brand(env), Q.CTX, plan)  # noqa: SLF001
    lines = [r.getMessage() for r in caplog.records]
    line = " ".join(m for m in lines if "기획 콘티 SVG 없음" in m)
    assert rid and rid in line and want in line
    assert "책방" not in line and "script" not in line  # 장면 글·SVG 값은 남기지 않는다


def test_code_only_storyboard_is_still_layout_before_svg() -> None:
    board = request_creative.plan_storyboard(Q.PLAN, Q.SHEET_FULL)
    assert board is not None and board["board_source"] == "layout"


def test_planning_svg_becomes_the_panel_svg() -> None:
    """기획이 그린 SVG 콘티(장면 첫 화면)는 정리를 거쳐 칸 SVG가 된다 (11/25, LLM 없이 svg)."""
    svg = ('<svg viewBox="0 0 90 160" onload="x()"><text x="45" y="12">지하철</text>'
           '<rect x="4" y="20" width="82" height="22"/></svg>')  # fmt: skip
    scenes = [s.model_copy(update={"start_svg": svg}) if s.n == 1 else s for s in Q.PLAN.scenes]
    plan = Q.PLAN.model_copy(update={"scenes": scenes})
    board = request_creative.plan_storyboard(plan, Q.SHEET_FULL)
    assert board is not None and board["board_source"] == "svg"
    first = board["shots"][0]["svg"]
    assert "<rect" in first and "onload" not in first
    assert all(not s["svg"] for s in board["shots"][1:])


def test_panel_labels_use_the_panel_time_and_scene_words_add_props(env: dict[str, Any]) -> None:
    """11/26 운영 콘티 4칸 라벨이 전부 "0초 · …"였고 마이크·무대 조명이 없었다."""
    times = [(0, 4), (4, 8), (8, 15)]
    scenes = [
        s.model_copy(update={"start_s": a, "end_s": b})
        for s, (a, b) in zip(Q.PLAN.scenes, times, strict=True)
    ]
    scenes[1] = scenes[1].model_copy(update={"visual": "무대 조명 아래 마이크를 들고 노래"})
    plan = Q.PLAN.model_copy(update={"scenes": scenes})
    rid, why = request_creative.prepare_storyboard(Q._brand(env), Q.CTX, plan)  # noqa: SLF001
    assert why is None and rid
    copied = POSE | {"label_ko": "0초 · 돌아봄"}  # LLM이 지시문 예시 시각을 베낀 답
    assert _draw(rid, lambda: _llm([{"no": n, "pose": copied} for n in (1, 2, 3)])) == "svg"
    shots = env["store"].get(rid)["storyboard"]["shots"]
    labels = [re.search(r"<text[^>]*>([^<]*)</text>", s["svg"]).group(1) for s in shots]  # type: ignore[union-attr]
    assert labels == ["0초 · 돌아봄", "4초 · 돌아봄", "8초 · 돌아봄"]
    assert "#8a8f99" in shots[1]["svg"] and "rgba(255,236,170" in shots[1]["svg"]  # 마이크·조명
    assert "#8a8f99" not in shots[0]["svg"]


def test_panel_starts_fall_back_to_an_even_split_of_the_plan_length() -> None:
    board = {"shots": [{"no": 1}, {"no": 2}, {"no": 3}], rr.PLAN_DURATION_KEY: 15,
             "plan_scenes": [{"start_s": 0}, {"start_s": 0}, {"start_s": 0}]}  # fmt: skip
    assert bs.panel_starts(board) == {1: 0.0, 2: 5.0, 3: 10.0}  # 기획 시각이 모두 0이면 고르게
    assert bs.panel_starts({"shots": [{"no": 1, "start_s": 0}, {"no": 2, "start_s": 3}]}) == {
        1: 0.0, 2: 3.0,
    }  # fmt: skip
    assert bs.panel_starts({"shots": [{"no": 1}, {"no": 2}]}) == {}


def test_planning_mannequin_svg_keeps_gradients_in_the_panel() -> None:
    """11/25 기획 3D 마네킹 콘티(그라데이션 구 음영·url(#id), 8KB 넘음)도 칸 SVG로 그대로 쓴다."""
    defs = ('<defs><radialGradient id="sk" cx="35%" cy="30%" r="75%"><stop offset="0%" '
            'stop-color="#ffe6c7"/><stop offset="100%" stop-color="#d9a77a"/>'
            "</radialGradient></defs>")  # fmt: skip
    grid = "".join(f'<line x1="{i}.5" y1="200" x2="{i}.5" y2="320"/>' for i in range(220))
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320">{defs}'
           f'<g stroke="#e6dccd" stroke-width="0.6">{grid}</g>'
           '<circle cx="90" cy="120" r="12" fill="url(#sk)"/>'
           '<text x="8" y="16" font-size="9" fill="#6b6256">0초 · 놀라 돌아봄</text>'
           "</svg>")  # fmt: skip
    assert 8192 < len(svg.encode()) <= 16384
    scenes = [s.model_copy(update={"start_svg": svg}) if s.n == 1 else s for s in Q.PLAN.scenes]
    plan = Q.PLAN.model_copy(update={"scenes": scenes})
    board = request_creative.plan_storyboard(plan, Q.SHEET_FULL)
    assert board is not None and board["board_source"] == "svg"
    first = board["shots"][0]["svg"]
    assert "<radialGradient" in first and 'fill="url(#sk)"' in first and "놀라 돌아봄" in first


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
