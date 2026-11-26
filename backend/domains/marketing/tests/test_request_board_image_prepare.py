"""영상 초안 준비 때 스토리보드 그림 자동 1장 (11/26 C). 가짜 Higgsfield·가짜 관문.

- 준비가 [다시 그리기]와 같은 길로 한 번 시작한다. 첫 참조 = 칸 SVG 구도판(격자 PNG),
  다음 = 캐릭터 시트(없으면 원본)·추가 참조. 같은 이야기면 다시 안 그린다
- 키프레임 모델이면 그리지 않는다. 그림이 실패해도 영상 승인 요청은 막지 않는다
"""

from __future__ import annotations

import importlib.util
import struct
import sys
import zlib
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent.media import svg_raster


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


S = _load("request_board_svg_helpers_prepare", "test_request_board_svg.py")
M = S.M
mcp = S.mcp  # 시험 도구(fixture): 회사 계정(MCP)·가짜 관문·가짜 Higgsfield
JPEG = S.JPEG


def _shots(n: int, end_card: bool = True) -> list[dict[str, Any]]:
    shots = [
        {"no": i, "scene_ko": f"장면 {i}", "shot_size": "medium", "shot_size_ko": "허리 위",
         "angle": "eye_level", "angle_ko": "눈높이", "composition_ko": f"장면 {i}",
         "has_character": True, "prompt_en": f"panel {i}", "svg": S._svg(str(i)),  # noqa: SLF001
         "start_s": (i - 1) * 4, "end_s": i * 4}
        for i in range(1, n + 1)
    ]  # fmt: skip
    if end_card:
        shots[-1] |= {"has_character": False, "scene_ko": "엔드카드 · 채널 이름", "svg": None}
    return shots


def _record(shots: int = 4) -> str:
    with company_scope.using("catbooks"):
        rid = cr.store().add_sheet(M.SHEET | {"kind": "virtual_human"}, None, None)
        rr.save_original(rid, JPEG, "image/jpeg")
        board = M.BOARD | {"board_source": "svg", "shots": _shots(shots)}
        cr.store().attach_storyboard(rid, "누비 이야기", shots, board)
        # 시트 그림(A) 자리: 만든 시트
        (rr._dir(rid) / "sheet.jpg").write_bytes(JPEG)  # noqa: SLF001
        render = cr.store().get_render(rid)
        render["sheet_image"] = {"status": "done", "asset": "sheet.jpg", "sha256": "s"}
        cr.store().set_render(rid, render)
    return rid


def _prepare(e: dict[str, Any], rid: str) -> None:
    async def go() -> None:
        rr.start_prepared_board(e["ledger"], M.META, rid)
        await rr.wait_idle()

    with company_scope.using("catbooks"):
        M._run(go)  # noqa: SLF001


def _board_calls(e: dict[str, Any]) -> list[Any]:
    return [p for a, p in e["gate"].calls if a == "generate_media"]


def test_prepare_starts_one_board_with_the_layout_first(mcp: dict[str, Any]) -> None:
    rid = _record()
    _prepare(mcp, rid)
    assert len(_board_calls(mcp)) == 1
    with company_scope.using("catbooks"):
        board = cr.store().get_render(rid)["board_image"]
        layout = (rr._dir(rid) / rr.LAYOUT_FILE).read_bytes()  # noqa: SLF001
    assert board["status"] == "done"
    assert layout.startswith(b"\x89PNG")
    gen = [str(args) for name, args in mcp["hf"].tools if name.startswith("generate_image")]
    assert len(gen) == 1 and rr.BOARD_LAYOUT_EN in gen[0]  # 지시문 + 칸 프롬프트
    # 참조 = 구도판 + 시트 (추가 참조 없음): 올린 파일 2개
    assert mcp["hf"].names().count("media_upload") == 2


def test_same_story_prepared_again_draws_nothing(mcp: dict[str, Any]) -> None:
    rid = _record()
    _prepare(mcp, rid)
    mcp["gate"].calls.clear()
    _prepare(mcp, rid)
    assert _board_calls(mcp) == []


def test_keyframe_model_draws_no_board(
    mcp: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", "kling4_0")
    rid = _record()
    with company_scope.using("catbooks"):
        render = cr.store().get_render(rid)
        render["keyframes"] = {"status": "pending",
                               "frames": [{"index": 0, "status": "pending"},
                                          {"index": 1, "status": "pending"}]}  # fmt: skip
        cr.store().set_render(rid, render)
    _prepare(mcp, rid)
    assert _board_calls(mcp) == []
    with company_scope.using("catbooks"):
        assert "board_image" not in cr.store().get_render(rid)


def test_board_failure_does_not_block_video_approval(mcp: dict[str, Any]) -> None:
    mcp["hf"].job_status = "failed"
    rid = _record()
    _prepare(mcp, rid)
    with company_scope.using("catbooks"):
        assert cr.store().get_render(rid)["board_image"]["status"] == "failed"
        video = M._run(lambda: rr.request_video(mcp["ledger"], M.META, rid, "사람"))  # noqa: SLF001
    assert video["status"] == "awaiting_approval"


def _pixels(png: bytes) -> tuple[int, int, bytes]:
    width, height = struct.unpack(">II", png[16:24])
    at, data = 8, b""
    while at < len(png):
        (size,) = struct.unpack(">I", png[at : at + 4])
        if png[at + 4 : at + 8] == b"IDAT":
            data += png[at + 8 : at + 8 + size]
        at += 12 + size
    raw = zlib.decompress(data)
    stride = width * 3
    rows = b"".join(raw[r * (stride + 1) + 1 : (r + 1) * (stride + 1)] for r in range(height))
    return width, height, rows


@pytest.mark.parametrize(("count", "layout"), [(1, (1, 1)), (4, (4, 1)), (5, (4, 2)), (8, (4, 2))])
def test_layout_grid_has_one_cell_per_panel(count: int, layout: tuple[int, int]) -> None:
    assert svg_raster.grid_layout(count) == layout
    panels: list[str | None] = [S._svg(str(i)) for i in range(count - 1)] + [None]  # noqa: SLF001
    width, height, rgb = _pixels(svg_raster.grid_png(panels, "9:16"))
    assert (width, height) == svg_raster.GRID_SIZE  # 16:9 한 장
    cols, rows = layout
    cw = (width - svg_raster.GRID_GUTTER * (cols + 1)) // cols
    ch = (height - svg_raster.GRID_GUTTER * (rows + 1)) // rows
    cw, ch = (min(cw, ch * 9 // 16), min(ch, cw * 16 // 9))
    left = (width - cols * cw - (cols - 1) * svg_raster.GRID_GUTTER) // 2
    top = (height - rows * ch - (rows - 1) * svg_raster.GRID_GUTTER) // 2

    def at(i: int, fx: float = 0.5, fy: float = 0.1) -> tuple[int, ...]:
        x = left + (i % cols) * (cw + svg_raster.GRID_GUTTER) + int(cw * fx)
        y = top + (i // cols) * (ch + svg_raster.GRID_GUTTER) + int(ch * fy)
        return tuple(rgb[(y * width + x) * 3 : (y * width + x) * 3 + 3])

    assert at(count - 1) == svg_raster.GRID_DARK  # 엔드카드 칸 = 어두운 빈 판
    for i in range(count - 1):
        assert at(i) != svg_raster.GRID_DARK and at(i) != svg_raster.GRID_BACK
        assert at(i, 0.5, 0.5) != at(i)  # 칸 가운데에 그린 도형(주황 원)


def test_more_than_eight_panels_is_refused() -> None:
    with pytest.raises(svg_raster.RasterError):
        svg_raster.grid_png([None] * 9)
