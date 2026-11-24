"""SVG 콘티 칸 + 썸네일은 영상에서 자른다 (11/25). 가짜 LLM·가짜 Higgsfield·가짜 ffmpeg만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.marketing_storyboard import (
    MAX_SVG_BYTES,
    StoryboardPromptOutput,
    StoryboardShot,
)
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import creative_thumbnails as ct
from marketing_agent import storyboard as sb
from marketing_agent.svg_sanitize import sanitize_svg


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


M = _load("creative_render_mcp_helpers_svg", "test_creative_render_mcp.py")
T = _load("creative_thumbnails_helpers_svg", "test_creative_thumbnails.py")
S = _load("storyboard_helpers_svg", "test_storyboard.py")
thumb_env = T.env
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(64)


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


GOOD = (
    '<svg viewBox="0 0 90 160"><rect x="1" y="1" width="88" height="158" fill="#eee"/>'
    '<circle cx="45" cy="80" r="10" fill="orange"/><text x="45" y="12" text-anchor="middle">'
    "출근</text></svg>"
)

# ---------------------------------------------------------------- 정리 함수


def test_good_svg_keeps_shapes_and_text() -> None:
    out = sanitize_svg(GOOD)
    assert out is not None and out.startswith("<svg")
    assert "<rect" in out and "<circle" in out and "출근" in out and 'viewBox="0 0 90 160"' in out


def test_namespaced_tags_are_judged_by_local_name() -> None:
    ns = "http://www.w3.org/2000/svg"
    out = sanitize_svg(f'<svg xmlns="{ns}" viewBox="0 0 90 160"><rect width="5" height="5"/></svg>')
    assert out is not None and "<rect" in out and "ns0" not in out
    foreign = '<svg xmlns:x="urn:evil"><x:rect width="1"/><rect width="2"/></svg>'
    out = sanitize_svg(foreign)
    assert out is not None and 'width="1"' not in out and 'width="2"' in out


@pytest.mark.parametrize(
    "bad",
    [
        '<svg><script>alert(1)</script><rect width="1"/></svg>',
        "<svg><style>rect{fill:red}</style></svg>",
        "<svg><foreignObject><div>x</div></foreignObject></svg>",
        '<svg><image href="http://evil/x.png"/></svg>',
        '<svg><use href="#a"/><a href="http://x"><rect width="1"/></a></svg>',
    ],
)
def test_disallowed_elements_are_dropped_with_their_children(bad: str) -> None:
    out = sanitize_svg(bad)
    assert out is not None
    for word in ("script", "style", "foreignObject", "image", "<use", "<a", "<div", "evil"):
        assert word not in out
    assert "<rect" not in out or "<a" not in bad  # 버린 요소 안의 자식도 같이 버린다


def test_disallowed_attributes_and_dangerous_values_are_dropped() -> None:
    svg = (
        '<svg xmlns:xlink="http://www.w3.org/1999/xlink" onload="alert(1)">'
        '<rect width="1" onclick="x()" style="fill:red" href="http://a" xlink:href="#b" '
        'fill="url(#g)" stroke="  JaVa Script:alert(1)" id="z" opacity="0.5"/>'
        '<path d="M0 0" fill="URL (http://evil)"/></svg>'
    )
    out = sanitize_svg(svg)
    assert out is not None
    for word in ("onload", "onclick", "style", "href", "url", "javascript", 'id="z"', "evil"):
        assert word.lower() not in out.lower()
    assert 'opacity="0.5"' in out and 'width="1"' in out


def test_text_is_escaped() -> None:
    out = sanitize_svg("<svg><text>&lt;script&gt;a&amp;b</text></svg>")
    assert out is not None and "&lt;script&gt;a&amp;b" in out and "<script" not in out


@pytest.mark.parametrize(
    "bad",
    [
        '<!DOCTYPE svg [<!ENTITY a "b">]><svg/>',
        '<?xml version="1.0"?><!doctype svg><svg/>',
        '<svg><!ENTITY x "y"></svg>',
        "<div><rect/></div>",
        "<svg><rect",
        "",
        None,
    ],
)
def test_unusable_input_is_rejected(bad: Any) -> None:
    assert sanitize_svg(bad) is None


def test_size_limit_is_8kb() -> None:
    rects = "".join('<rect x="1" y="1" width="2" height="2"/>' for _ in range(300))
    assert sanitize_svg(f"<svg>{rects}</svg>") is None
    small = "".join('<rect x="1" y="1" width="2" height="2"/>' for _ in range(20))
    out = sanitize_svg(f"<svg>{small}</svg>")
    assert out is not None and len(out.encode()) <= MAX_SVG_BYTES


# ---------------------------------------------------------------- 스토리보드 계약·LLM


def _read_with(svgs: list[str]) -> sb.StoryRead:
    return S._read(  # noqa: SLF001
        6,
        shots=[S._shot(i + 1, svg=svg) for i, svg in enumerate(svgs)],  # noqa: SLF001
    )


def _make(svgs: list[str]) -> tuple[StoryboardPromptOutput, MockLLMClient]:
    llm = MockLLMClient(responses={sb.StoryRead: _read_with(svgs)})
    out = asyncio.run(sb.make_storyboard({"story": S.STORY, "shots": 6}, lambda: llm))
    return out, llm


def test_mixed_cells_keep_svg_where_clean_and_mark_the_board_svg() -> None:
    out, llm = _make([GOOD, "<svg><script/></svg>x", GOOD, "", "<div/>", GOOD])
    assert out.board_source == "svg"
    assert [bool(s.svg) for s in out.shots] == [True, False, True, False, False, True]
    assert len(llm.calls) == 1  # 호출 수는 그대로 1회


def test_all_empty_cells_fall_back_to_layout() -> None:
    out, _ = _make([""] * 6)
    assert out.board_source == "layout" and all(s.svg is None for s in out.shots)


def test_old_records_read_with_defaults() -> None:
    old = {k: v for k, v in S._shot(1).items() if k in ("scene_ko",)}  # noqa: SLF001
    shot = StoryboardShot(
        no=1, scene_ko=old["scene_ko"], shot_size="full", shot_size_ko="전신", angle="eye_level",
        angle_ko="눈높이", composition_ko="c", has_character=True, prompt_en="p",
    )  # fmt: skip
    assert shot.svg is None
    out, _ = _make([GOOD] * 6)
    dumped = out.model_dump(mode="json")
    dumped.pop("board_source")
    for s in dumped["shots"]:
        s.pop("svg")
    assert StoryboardPromptOutput.model_validate(dumped).board_source == "higgsfield"


# ---------------------------------------------------------------- 영상 입력: 그림 크레딧


@pytest.fixture
def mcp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)  # MCP 가짜 환경 (회사 계정 연결)
    yield next(gen)
    gen.close()


def _virtual(e: dict[str, Any], kind: str = "virtual_human") -> str:
    with company_scope.using("catbooks"):
        rid = cr.store().add_sheet(M.SHEET | {"kind": kind}, None, None)
        rr.save_original(rid, JPEG, "image/jpeg")
    e["store"].attach_storyboard(rid, "들판 이야기", 6, M.BOARD)
    return rid


def _board(e: dict[str, Any], rid: str, **kw: Any) -> None:
    async def go() -> None:
        rr.maybe_start_board(e["ledger"], M.META, rid, **kw)
        await rr.wait_idle()

    asyncio.run(go())


def test_image_references_model_with_virtual_human_makes_no_board(
    mcp: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    rid = _virtual(mcp)
    _board(mcp, rid)
    assert "board_image" not in mcp["store"].get_render(rid)
    assert mcp["ledger"].month_usage(_month(), "image") == 0  # noqa: SLF001 - 예약 0
    # 스토리보드 그림 없이도 영상을 승인에 올린다
    M._run(lambda: rr.request_video(mcp["ledger"], M.META, rid, "gaon"))  # noqa: SLF001
    assert mcp["store"].get_render(rid)["video"]["status"] == "awaiting_approval"
    assert mcp["ledger"].month_usage(_month(), "image") == 0  # noqa: SLF001


def test_redraw_with_higgsfield_is_only_by_a_person_choice(
    mcp: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling_4_0_flash")
    rid = _virtual(mcp)
    _board(mcp, rid, force=True)
    assert mcp["store"].get_render(rid)["board_image"]["status"] == "done"
    assert mcp["store"].get(rid)["storyboard"]["board_source"] == "higgsfield"


@pytest.mark.parametrize(
    ("model", "kind"),
    [("kling3_0_turbo", "virtual_human"), ("kling_4_0_flash", "real_photo"), ("", "virtual_human")],
)
def test_other_cases_make_the_board_like_before(
    mcp: dict[str, Any], monkeypatch: pytest.MonkeyPatch, model: str, kind: str
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", model)
    with company_scope.using("catbooks"):
        rid = cr.store().add_sheet(M.SHEET | {"kind": kind}, None, None)
        if kind == "real_photo":
            path = rr.asset_path(rid, "sheet_image", "catbooks")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(JPEG)
            rr._set(rid, "sheet_image", status="done", asset="sheet.jpg", sha256="a")  # noqa: SLF001
        else:
            rr.save_original(rid, JPEG, "image/jpeg")
    mcp["store"].attach_storyboard(rid, "이야기", 6, M.BOARD)
    _board(mcp, rid)
    assert mcp["store"].get_render(rid)["board_image"]["status"] == "done"
    assert mcp["store"].get(rid)["storyboard"]["board_source"] == "higgsfield"
    assert cm.takes_image_references("kling_4_0_flash")


# ---------------------------------------------------------------- 썸네일은 영상에서


def _fake_frames(monkeypatch: pytest.MonkeyPatch, duration: float | None = 10.0,
                 ok: bool = True) -> list[float]:  # fmt: skip
    seen: list[float] = []

    def frame_at(_video: Path, at: float, out: Path) -> bool:
        seen.append(at)
        out.write_bytes(T._jpeg(f"frame{at}"))  # noqa: SLF001
        return ok

    monkeypatch.setattr(ct, "FRAME_AT", frame_at)
    monkeypatch.setattr(ct, "DURATION", lambda _p: duration)
    return seen


def _timed_board(e: dict[str, Any]) -> None:
    shots = [
        {"no": 1, "prompt_en": "a", "has_character": True, "start_s": 0, "end_s": 3},
        {"no": 2, "prompt_en": "b", "has_character": True, "start_s": 3, "end_s": 7},
        {"no": 3, "prompt_en": "c", "has_character": True, "start_s": 7, "end_s": 10},
    ]
    e["store"].attach_storyboard(e["rid"], "s", 3, T.BOARD | {"shots": shots})


def test_three_scene_frames_become_candidates_without_credit(
    thumb_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _fake_frames(monkeypatch)
    _timed_board(thumb_env)
    T._with_video(thumb_env)  # noqa: SLF001
    state = T._run(  # noqa: SLF001
        lambda: ct.request(thumb_env["ledger"], T.META, thumb_env["rid"], None, auto=True)
    )
    assert state["status"] == "done"
    assert seen == [1.5, 5.0, 7.8]  # 훅 칸 가운데, 중간 칸 가운데, 엔드카드(마지막 2초) 직전
    cands = state["candidates"]
    assert [c["kind"] for c in cands] == list(ct.KINDS)
    assert all(c["source"] == "video_frame" and c["is_ai_generated"] for c in cands)
    assert thumb_env["gate"].calls == []  # 안전 관문도 크레딧 예약도 없다
    assert thumb_env["ledger"].month_usage(_month(), "image") == 0  # noqa: SLF001
    cover = ct.current_cover(thumb_env["rid"])
    assert cover is not None and cover["ok"]


def test_frames_are_cut_from_the_latest_video(
    thumb_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_frames(monkeypatch)
    T._with_video(thumb_env)  # noqa: SLF001
    T._with_video(thumb_env, "final_video")  # noqa: SLF001
    rec = thumb_env["store"].get(thumb_env["rid"])
    assert ct.source_video(rec)[0] == "final_video"  # type: ignore[index]
    state = T._run(  # noqa: SLF001
        lambda: ct.request(thumb_env["ledger"], T.META, thumb_env["rid"], None, auto=True)
    )
    assert state["source_part"] == "final_video"


def test_frame_times_split_evenly_without_shot_times() -> None:
    shots = [{"no": i} for i in range(1, 5)]
    assert ct.frame_times(shots, 8.0) == [1.0, 5.0, 5.8]
    assert ct.frame_times([], 9.0) == [1.5, 4.5, 6.8]  # 칸이 없으면 3등분
    assert ct.frame_times(shots, 1.0) == [0.125, 0.625, 0.0]  # 짧은 영상도 범위 안


def test_no_ffmpeg_falls_back_to_generation(
    thumb_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_frames(monkeypatch, ok=False)
    T._with_video(thumb_env)  # noqa: SLF001
    state = T._run(  # noqa: SLF001
        lambda: ct.request(thumb_env["ledger"], T.META, thumb_env["rid"], None, auto=True)
    )
    assert state["status"] == "awaiting_approval"  # 지금처럼 그림 생성 경로(사람 결정)
    assert [c for c in thumb_env["gate"].calls if c[0] == ct.ACTION]
    folder = thumb_env["dir"] / "creative" / thumb_env["rid"] / "thumbs"
    assert not folder.exists() or list(folder.glob("thm_*")) == []


def test_regenerate_goes_straight_to_generation(
    thumb_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _fake_frames(monkeypatch)
    T._with_video(thumb_env)  # noqa: SLF001
    state = T._run(  # noqa: SLF001
        lambda: ct.request(thumb_env["ledger"], T.META, thumb_env["rid"], "gaon", regenerate=True)
    )
    assert state["status"] == "awaiting_approval" and seen == []


def test_old_candidates_read_as_generated(thumb_env: dict[str, Any]) -> None:
    rid = thumb_env["rid"]
    render = thumb_env["store"].get_render(rid)
    render["thumbnails"] = {
        "status": "done",
        "candidates": [{"media_id": "thm_000000000001", "kind": "closeup", "sha256": "x"}],
    }
    thumb_env["store"].set_render(rid, render)
    assert ct.get(rid)["candidates"][0]["source"] == "generated"


def test_frame_command_is_an_argument_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(args: list[str], **kw: Any) -> subprocess.CompletedProcess[bytes]:
        calls.append((args, kw))
        Path(args[-1]).write_bytes(b"x")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setenv("MARKETING_FFMPEG_BIN", "/opt/ff/ffmpeg")
    monkeypatch.setattr(ct.subprocess, "run", fake_run)
    out = tmp_path / "f.jpg"
    assert ct.ffmpeg_frame_at(tmp_path / "v.mp4", 2.5, out)
    args, kw = calls[0]
    assert args[0] == "/opt/ff/ffmpeg" and "-ss" in args and args[args.index("-ss") + 1] == "2.500"
    assert args[-1] == str(out) and kw.get("timeout") and "shell" not in kw
