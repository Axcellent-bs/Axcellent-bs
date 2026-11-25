"""키프레임 영상 (11/25 #1780 뒤): 기획 SVG 구도 → 실사 키프레임 → Kling 4.0 한 번에.

가짜 Higgsfield(MCP)·가짜 관문·임시 저장소만 쓴다(실제로 부르지 않는다). LLM은 부르지 않는다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from ax_agent_template import render_settings as settings_store
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import board_svg, handlers, request_creative
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import keyframes as kf
from marketing_agent import render_settings as rs
from marketing_agent.higgsfield_mcp import McpError
from marketing_agent.video_draft import VideoPlanIn


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("creative_render_mcp_helpers_keyframes", "test_creative_render_mcp.py")
env = M.env
NS = 'xmlns="http://www.w3.org/2000/svg"'
FRONT = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + b"front" + bytes(27)


def _svg(color: str) -> str:
    rect = f'<rect x="10" y="20" width="40" height="60" fill="{color}"/>'
    return f'<svg {NS} viewBox="0 0 90 160">{rect}</svg>'


S1, E1, S2, S3 = _svg("#111111"), _svg("#222222"), _svg("#333333"), _svg("#444444")


def _scene(n: int, start: int, end: int, **over: Any) -> dict[str, Any]:
    base = {
        "n": n,
        "start_s": start,
        "end_s": end,
        "visual": f"장면 {n}",
        "prompt": f"scene {n} prompt",
        "start_frame": f"{n}번 첫 화면",
        "end_frame": f"{n}번 끝 화면",
        "action_en": f"walks forward {n}",
        "camera_en": "slow push-in",
        "start_svg": "",
        "end_svg": "",
    }
    return base | over


SCENES = [
    _scene(1, 0, 5, start_svg=S1, end_svg=E1),
    _scene(2, 5, 10, start_svg=S2, end_frame=""),  # 끝 글이 비면 3번 첫 글, end_svg 비면 3번 start
    _scene(3, 10, 15, start_svg=S3),  # 마지막 end_svg 없음 → 프롬프트만
]


def _plan(scenes: list[dict[str, Any]]) -> VideoPlanIn:
    return VideoPlanIn.model_validate({
        "concept": "책방 홍보", "duration_s": 15, "script": "문을 연다. 고른다. 읽는다.",
        "style": "soft watercolor", "scenes": scenes,
    })  # fmt: skip


# ---------------------------------------------------------------- 순수 함수
def test_frames_are_scene_boundaries_and_end_svg_wins() -> None:
    frames = kf.plan_frames(kf.plan_scenes(_plan(SCENES)))
    assert [f["index"] for f in frames] == [0, 1, 2, 3]  # 장면 3 → 4장
    assert [f["at_s"] for f in frames] == [0, 5, 10, 15]
    texts = [f["text_ko"] for f in frames]
    assert texts == ["1번 첫 화면", "1번 끝 화면", "3번 첫 화면", "3번 끝 화면"]
    assert frames[0]["svg"] and "#111111" in frames[0]["svg"]
    assert "#222222" in frames[1]["svg"]  # 장면 1 end_svg와 장면 2 start_svg가 겹치면 end_svg
    assert "#444444" in frames[2]["svg"]  # 장면 2 end_svg가 비면 장면 3 start_svg
    assert frames[3]["svg"] == ""  # SVG 빈 프레임 = 구도 없이 프롬프트만


def test_empty_keyframe_text_drops_the_boundary_and_all_empty_means_none() -> None:
    blank = [_scene(i, (i - 1) * 5, i * 5, start_frame="", end_frame="") for i in (1, 2, 3)]
    assert kf.plan_frames(kf.plan_scenes(_plan(blank))) == []
    one = [_scene(1, 0, 5, end_frame=""), _scene(2, 5, 10, start_frame="", end_frame="")]
    frames = kf.plan_frames(kf.plan_scenes(_plan(one)))
    assert [f["text_ko"] for f in frames] == ["1번 첫 화면"]  # 2장 미만 → 키프레임 방식 아님


def test_at_most_ten_frames_keep_first_and_last() -> None:
    rows = [_scene(i, (i - 1) * 2, i * 2) for i in range(1, 13)]  # 경계 13개
    frames = kf.plan_frames(rows)
    assert len(frames) == kf.MAX_FRAMES == 10
    assert frames[0]["text_ko"] == "1번 첫 화면" and frames[-1]["text_ko"] == "12번 끝 화면"


def test_bad_svg_from_plan_is_dropped() -> None:
    bad = [_scene(1, 0, 5, start_svg="<script>alert(1)</script>"), _scene(2, 5, 10)]
    assert kf.plan_scenes(_plan(bad))[0]["start_svg"] == ""


def test_frame_prompt_follows_the_layout_and_asks_for_no_text() -> None:
    frame = {"index": 1, "text_ko": "창가의 소녀", "camera_en": "close-up"}
    with_layout = kf.frame_prompt(frame, 4, style_en="photo", character_en="a girl", aspect="9:16",
                                  layout=True, person=True)  # fmt: skip
    assert "Still keyframe 2 of 4" in with_layout and "Shows: 창가의 소녀." in with_layout
    assert kf.FOLLOW_LAYOUT in with_layout and kf.SAME_PERSON_AFTER_LAYOUT in with_layout
    assert with_layout.endswith(kf.NO_TEXT_IMAGE)
    alone = kf.frame_prompt(frame, 4, style_en="photo", character_en=None, aspect="9:16",
                            layout=False, person=False)  # fmt: skip
    assert kf.FOLLOW_LAYOUT not in alone and kf.SAME_PERSON not in alone


def test_video_prompt_is_scene_actions_times_speed_and_no_text() -> None:
    scenes = kf.plan_scenes(_plan(SCENES))
    p = kf.video_prompt(scenes, "photo", 1.2)
    assert p.startswith(kf.OPENING)
    assert "0-6s: walks forward 1 slow push-in." in p
    assert "6-12s: walks forward 2 slow push-in." in p and "12-18s:" in p
    assert p.endswith(rr.NO_TEXT) and '"' not in p
    long = [_scene(i, (i - 1) * 2, i * 2, action_en="x " * 199) for i in range(1, 9)]
    q = kf.video_prompt(kf.plan_scenes(_plan(long)), "photo", 1.5)
    assert len(q) <= rr.VIDEO_PROMPT_MAX and q.endswith(rr.NO_TEXT)


def test_keyframe_medias_order_mode_and_cap() -> None:
    params = cm.video_params("kling4_0", "p", "9:16", 18)
    got = cm.with_keyframes(params, ["a", "b", "c", "d"])
    assert got["mode"] == "image-to-video"
    assert got["medias"] == [
        {"value": "a", "role": "start_image"},
        {"value": "b", "role": "image_references"},
        {"value": "c", "role": "image_references"},
        {"value": "d", "role": "end_image"},
    ]
    many = cm.with_keyframes(params, [f"m{i}" for i in range(12)])
    assert len(many["medias"]) == 10 and many["medias"][-1] == {"value": "m11", "role": "end_image"}
    with pytest.raises(ValueError):
        cm.with_keyframes(params, ["only"])
    assert cm.takes_keyframes("kling4_0") and not cm.takes_keyframes("kling_4_0_flash")


# ---------------------------------------------------------------- 흐름 (가짜 MCP)
@pytest.fixture
def kenv(env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling4_0")
    monkeypatch.setenv(settings_store.FILE_ENV, str(tmp_path / "render_settings.json"))
    rs.reset_cache()
    monkeypatch.setattr(handlers.STATE, "media_ledger", env["ledger"])
    uploads: list[tuple[bytes, str]] = []

    async def upload(hf: Any, data: bytes, filename: str, content_type: str) -> str:
        uploads.append((data, filename))
        return f"m{len(uploads)}"

    monkeypatch.setattr(cm, "upload", upload)

    def boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("마케팅은 SVG용 LLM을 부르지 않는다")

    env["real_draw"] = board_svg.draw
    monkeypatch.setattr(board_svg, "draw", boom)
    env["uploads"] = uploads
    yield env
    rs.reset_cache()


def _record(env: dict[str, Any], scenes: list[dict[str, Any]], kind: str = "virtual_human",
            company: str = "catbooks") -> str:  # fmt: skip
    with company_scope.using(company):
        store = cr.store()
        sheet = M.SHEET | {"kind": kind, "consistency_en": "a young woman"}
        rid = store.add_sheet(sheet, None, None)
        if kind != "real_photo":
            rr.save_original(rid, FRONT, "image/jpeg")
        board = request_creative.plan_storyboard(_plan(scenes), store.get(rid)["sheet"])
        store.attach_storyboard(rid, "대본", len(board["shots"]), board)
    return rid


def _request(rid: str, company: str = "catbooks") -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(context_id="ctx_kf", token="t", idempotency_key="kf1",
                         deadline=datetime.now(UTC) + timedelta(seconds=60),
                         requester="orchestrator", tenant_id=company),
        skill="generate_media",
        input={"post_draft_id": "spd_kf", "media_kind": "video",
               request_creative.INPUT_KEY: rid},
    )  # fmt: skip


def _make(env: dict[str, Any], rid: str, company: str = "catbooks") -> Any:
    return M._run(lambda: request_creative.make_video(_request(rid, company), env["ledger"]),  # noqa: SLF001
                  company)  # fmt: skip


def _image_gates(env: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for a, p in env["gate"].calls if a == "generate_media"]


def _approve(env: dict[str, Any], rid: str) -> dict[str, Any]:
    env["gate"].status["generate_creative_video"] = "executed"
    M._run(lambda: rr.check_video(env["ledger"], M.META, rid))  # noqa: SLF001
    return env["store"].get_render(rid)


def test_keyframes_then_one_kling_video(kenv: dict[str, Any]) -> None:
    rid = _record(kenv, SCENES)
    out = _make(kenv, rid)
    assert out.outcome == request_creative.VIDEO_HELD  # 키프레임 4장 → 영상 승인 대기
    state = kenv["store"].get_render(rid)
    frames = state["keyframes"]["frames"]
    assert state["keyframes"]["status"] == "done" and len(frames) == 4
    assert all(f["status"] == "done" and f["asset"] == f"keyframe_{f['index']}.jpg" for f in frames)
    assert "board_image" not in state  # 스토리보드 그림은 만들지 않는다
    gates = _image_gates(kenv)
    assert len(gates) == 4 and all("키프레임" in g["summary"] for g in gates)
    # 그림 참조는 구도 PNG가 먼저, 그다음 가상 인물 원본. SVG 없는 마지막 장은 원본만
    up = [d for d, _ in kenv["uploads"]]
    assert up[0].startswith(b"\x89PNG") and up[1] == FRONT
    assert up[-1] == FRONT and up[-2] == FRONT  # 3번째 장: 구도·원본, 4번째 장: 원본만
    images = [args["requests"][0]["params"] for n, args in kenv["hf"].tools
              if n == "generate_image_batch"]  # fmt: skip
    assert [len(p["medias"]) for p in images] == [2, 2, 2, 1]
    assert kf.FOLLOW_LAYOUT in images[0]["prompt"] and kf.FOLLOW_LAYOUT not in images[3]["prompt"]
    assert frames[0]["layout_asset"] == "layout_0.png" and frames[3]["layout_asset"] is None
    assert frames[0]["svg_source"] == "planning" and frames[3]["svg_source"] == "none"
    assert frames[0]["layout_png_media_id"] == "m1" and frames[3]["layout_png_media_id"] is None
    assert (rr._dir(rid, "catbooks") / "layout_0.png").read_bytes() == up[0]  # noqa: SLF001
    video = state["video"]
    assert video["keyframes"] is True
    assert video["summary"]["reference"] == "keyframes" and video["summary"]["keyframes"] == 4
    assert video["summary"]["keyframe_credits"] == pytest.approx(8.0)  # 장당 2.0 (가짜 견적)
    assert "키프레임 4장" in video["params"]["summary"]
    # 승인 → start·중간·end 순서로 한 번에
    before = len(kenv["uploads"])
    after = _approve(kenv, rid)
    assert after["video"]["status"] == "done" and after["keyframes"]["approved_at"]
    params = dict(kenv["hf"].tools)["generate_video_batch"]["requests"][0]["params"]
    assert params["mode"] == "image-to-video" and params["model"] == "kling4_0"
    ids = [f"m{i}" for i in range(before + 1, before + 5)]
    assert params["medias"] == [
        {"value": ids[0], "role": "start_image"},
        {"value": ids[1], "role": "image_references"},
        {"value": ids[2], "role": "image_references"},
        {"value": ids[3], "role": "end_image"},
    ]
    assert [f["media_id"] for f in after["keyframes"]["frames"]] == ids
    sent = [d for d, _ in kenv["uploads"][before:]]
    folder = rr._dir(rid, "catbooks")  # noqa: SLF001
    assert sent == [(folder / f"keyframe_{i}.jpg").read_bytes() for i in range(4)]
    speed = rr.video_speed(kenv["store"].get(rid))
    assert params["duration"] == video["seconds"]
    assert f"0-{rr._secs(5 * speed)}s: walks forward 1 slow push-in." in params["prompt"]  # noqa: SLF001
    assert params["prompt"].endswith(rr.NO_TEXT)


def test_one_failed_keyframe_stops_the_video_and_redraw_fixes_it(
    kenv: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _record(kenv, SCENES)
    real = cm.submit
    seen = {"image": 0}

    async def submit(hf: Any, kind: str, params: dict[str, Any]) -> str:
        if kind == "image":
            seen["image"] += 1
            if seen["image"] == 2:
                raise McpError("tool_error", stage="submit", tool="generate_image_batch")
        return await real(hf, kind, params)

    monkeypatch.setattr(cm, "submit", submit)
    out = _make(kenv, rid)
    assert out.status == TaskStatus.FAILED and out.outcome == "keyframe_failed"
    state = kenv["store"].get_render(rid)
    assert [f["status"] for f in state["keyframes"]["frames"]] == ["done", "failed", "done", "done"]
    assert state["keyframes"]["status"] == "failed"
    assert "video" not in state  # 영상은 요청하지 않는다
    assert not [a for a, _ in kenv["gate"].calls if a == "generate_creative_video"]
    with pytest.raises(rr.RenderError, match="keyframes_not_ready"):
        M._run(lambda: rr.request_video(kenv["ledger"], M.META, rid, "gaon"))  # noqa: SLF001
    # [다시 그리기] 그 장만 (그림 1장, 새 멱등 키)
    req = AgentRequest(meta=M.META, skill="creative_keyframe_redraw",
                       input={"record_id": rid, "index": 1})  # fmt: skip
    res = M._run(lambda: handlers.HANDLERS["creative_keyframe_redraw"](req))  # noqa: SLF001
    assert res.status == TaskStatus.COMPLETED and res.output["part"] == "keyframes"
    frames = kenv["store"].get_render(rid)["keyframes"]["frames"]
    assert [f["status"] for f in frames] == ["done"] * 4 and frames[1]["attempt"] == 2
    assert len(_image_gates(kenv)) == 5
    again = _make(kenv, rid)
    assert again.outcome == request_creative.VIDEO_HELD
    assert len(_image_gates(kenv)) == 5  # 이미 그린 장은 다시 그리지 않는다


def test_image_credits_off_stops_before_any_reservation(
    kenv: dict[str, Any], tmp_path: Path
) -> None:
    path = tmp_path / "render_settings.json"
    path.write_text(json.dumps({"companies": {"catbooks": {"image_credits_ok": False}}}),
                    encoding="utf-8")  # fmt: skip
    rs.reset_cache()
    rid = _record(kenv, SCENES)
    out = _make(kenv, rid)
    assert out.outcome == "keyframe_blocked"
    frames = kenv["store"].get_render(rid)["keyframes"]["frames"]
    assert {f["error"] for f in frames} == {rr.IMAGE_CREDITS_DISABLED}
    assert _image_gates(kenv) == [] and "generate_image_batch" not in kenv["hf"].names()
    month = datetime.now(UTC).strftime("%Y-%m")
    assert kenv["ledger"].month_usage(month, "image") == 0  # 예약 0
    assert "video" not in kenv["store"].get_render(rid)


def test_non_keyframe_model_keeps_todays_flow(
    kenv: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    rid = _record(kenv, SCENES)
    out = _make(kenv, rid)
    assert out.outcome == request_creative.VIDEO_HELD
    state = kenv["store"].get_render(rid)
    assert "keyframes" not in state
    assert state["video"]["summary"]["reference"] == "character_original"
    assert "keyframes" not in state["video"]


def test_svg_only_scene_is_still_a_keyframe_with_the_visual_text() -> None:
    only = [_scene(i, (i - 1) * 5, i * 5, start_frame="", end_frame="", start_svg=S1, end_svg=E1)
            for i in (1, 2)]  # fmt: skip
    frames = kf.plan_frames(kf.plan_scenes(_plan(only)))
    assert len(frames) == 3 and {f["svg_source"] for f in frames} == {"planning"}
    assert frames[0]["text_ko"] == "장면 1"  # 글이 없으면 장면 화면 구성 글
    plain = kf.plan_frames(kf.plan_scenes(_plan(SCENES)))
    assert [f["svg_source"] for f in plain] == ["planning", "planning", "planning", "none"]


def test_all_keyframe_fields_empty_keeps_todays_flow(kenv: dict[str, Any]) -> None:
    blank = [_scene(i, (i - 1) * 5, i * 5, start_frame="", end_frame="")
             for i in (1, 2, 3)]  # fmt: skip
    rid = _record(kenv, blank)
    out = _make(kenv, rid)
    assert out.outcome == request_creative.VIDEO_HELD
    state = kenv["store"].get_render(rid)
    assert "keyframes" not in state and state["video"]["summary"]["reference"] != "keyframes"


def test_old_record_without_plan_scenes_reads_as_no_keyframes(kenv: dict[str, Any]) -> None:
    rid = M._board(kenv)  # noqa: SLF001 - 예전 기록(기획 장면 칸 없음)
    rec = kenv["store"].get(rid)
    assert kf.frames_of(rec) == [] and not kf.active(rec, rr.MCP, "catbooks")
    req = AgentRequest(meta=M.META, skill="creative_keyframe_redraw",
                       input={"record_id": rid, "index": 0})  # fmt: skip
    res = M._run(lambda: handlers.HANDLERS["creative_keyframe_redraw"](req))  # noqa: SLF001
    assert res.status == TaskStatus.FAILED and res.outcome == "no_keyframes"


def test_redraw_is_company_scoped_and_checks_the_index(kenv: dict[str, Any]) -> None:
    rid = _record(kenv, SCENES)
    _make(kenv, rid)

    def redraw(index: int, company: str) -> Any:
        meta = M.META.model_copy(update={"tenant_id": company})
        req = AgentRequest(meta=meta, skill="creative_keyframe_redraw",
                           input={"record_id": rid, "index": index})  # fmt: skip
        return M._run(lambda: handlers.HANDLERS["creative_keyframe_redraw"](req), company)  # noqa: SLF001

    assert redraw(0, "nubitour").outcome == "not_found"  # 다른 회사 기록은 안 보인다
    assert redraw(7, "catbooks").outcome == "bad_index"
    gates = len(_image_gates(kenv))
    _approve(kenv, rid)  # 영상을 만들었으면 키프레임은 바꾸지 않는다
    assert redraw(0, "catbooks").outcome == "video_started"
    assert len(_image_gates(kenv)) == gates


def test_real_photo_uses_the_layout_only(kenv: dict[str, Any]) -> None:
    rid = _record(kenv, SCENES, kind="real_photo")
    with company_scope.using("catbooks"):  # 실제 사진은 원본 대신 AI 시트 그림이 있다
        rr._set(rid, "sheet_image", status="done", asset="sheet.jpg", sha256="x")  # noqa: SLF001
    _make(kenv, rid)
    images = [args["requests"][0]["params"] for n, args in kenv["hf"].tools
              if n == "generate_image_batch"]  # fmt: skip
    assert [len(p.get("medias") or []) for p in images] == [1, 1, 1, 0]  # 구도만, 마지막은 없음
    assert all(d.startswith(b"\x89PNG") for d, _ in kenv["uploads"])


def test_plan_svg_is_used_as_is_and_board_svg_llm_is_not_called(kenv: dict[str, Any]) -> None:
    rid = _record(kenv, SCENES)
    board = kenv["store"].get(rid)["storyboard"]
    assert all(s.get("svg") for s in board["shots"])  # 기획 첫 화면 SVG가 칸 SVG

    def no_llm() -> Any:
        raise AssertionError("LLM을 부르면 안 된다")

    # 의뢰 흐름의 SVG 콘티(board_svg.draw)도 칸에 기획 SVG가 있으면 LLM을 부르지 않는다
    with company_scope.using("catbooks"):
        got = asyncio.run(kenv["real_draw"](rid, "ctx_kf", no_llm))
    assert got == board_svg.SKIPPED
    # 키프레임 흐름 전체(그림 4장 + 영상 요청)도 LLM 없이 돈다 (draw를 부르면 fixture가 막는다)
    assert _make(kenv, rid).outcome == request_creative.VIDEO_HELD
