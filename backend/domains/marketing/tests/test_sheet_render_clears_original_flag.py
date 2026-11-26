"""만든 캐릭터 시트가 "원본 사진"으로 보이던 버그 (11/27 운영 CR-b8a5525dda58, #1957 A 뒤 첫 사용).

시트 그림은 Higgsfield에서 정상으로 나왔는데(1.5 크레딧), `_set`이 칸을 합쳐 저장해 예전 기록의
`source: upload_original`·`original: true`가 남았다 → `legacy_original` → `sheet_ready` False → 화면
"원본 사진 (시트 아직 없음)" + [캐릭터 시트 만들기](다시 누르면 크레딧만 또 나감).
가짜 Higgsfield·가짜 관문·임시 저장소만 쓴다. 새 크레딧 호출 없음."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import TaskStatus
from marketing_agent import creative_render as rr


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


A = _load("real_sheet_helpers_clear_flag", "test_virtual_character_real_sheet.py")
V = A.V
env = A.env
JPEG = A.JPEG


def test_legacy_record_sheet_made_by_the_button_is_a_sheet(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = A._legacy_brand(env, monkeypatch)  # noqa: SLF001 - source upload_original, 원본 칸 없음
    env["store"].mark_brand(rid, "재현")
    out = A._redraw(env, rid)  # noqa: SLF001 - [캐릭터 시트 만들기] 가짜 완료
    assert out.status == TaskStatus.COMPLETED
    rec = env["store"].get(rid)
    sheet = rec["render"]["sheet_image"]
    assert sheet["status"] == "done" and sheet["execution_id"]
    assert sheet["source"] == rr.GENERATED_SOURCE and sheet["original"] is False
    assert sheet.get("moved_from") is None
    assert rr.sheet_ready(rec) and not rr.legacy_original(rec) and not rr.sheet_missing(rec)
    assert rr.original_part(rec) == rr.ORIGINAL_PART  # 원본은 원본 칸
    assert rr.video_reference(rec, "kling4_0")[0] == rr.ORIGINAL_PART  # 영상 참조는 원본 (#1957)
    card = next(c for c in env["store"].brand_characters() if c["record_id"] == rid)
    assert card["preview_part"] == "sheet_image"  # 회사 캐릭터 카드도 "캐릭터 시트"
    # 다시 그릴 때(running·실패)도 원본이 원본 칸에 있으니 시트 칸 표시를 지운다
    assert rr._sheet_flags(rid) == {"source": rr.GENERATED_SOURCE, "original": False,  # noqa: SLF001
                                    "moved_from": None}  # fmt: skip


def test_saved_merged_state_is_a_sheet_without_drawing_again(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """운영 상태 그대로: 예전 원본 표시 + 우리가 그린 그림(execution_id) → 다시 안 그려도 시트."""
    rid = A._legacy_brand(env, monkeypatch)  # noqa: SLF001
    env["store"].mark_brand(rid, "재현")
    assert rr.move_legacy_original(rid, env["store"].get(rid))  # [시트 만들기]가 먼저 옮긴 상태
    folder = env["dir"] / "creative" / rid
    (folder / "sheet.png").write_bytes(b"\x89PNGSHEET")
    render = env["store"].get_render(rid)
    render["sheet_image"] = {**render["sheet_image"], "status": "done", "asset": "sheet.png",
                             "sha256": "ab12cd34", "execution_id": "exe_live",
                             "original": True}  # fmt: skip
    env["store"].set_render(rid, render)
    rec = env["store"].get(rid)
    assert rec["render"]["sheet_image"]["source"] == rr.ORIGINAL_SOURCE  # 합쳐진 표시 그대로
    assert rr.sheet_ready(rec) and not rr.legacy_original(rec) and not rr.sheet_missing(rec)
    assert rr.original_part(rec) == rr.ORIGINAL_PART
    card = next(c for c in env["store"].brand_characters() if c["record_id"] == rid)
    assert card["preview_part"] == "sheet_image"
    assert env["gate"].calls == []  # 다시 그리지 않았다 (크레딧 0)


def test_failed_sheet_keeps_the_original_in_the_original_slot(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = A._legacy_brand(env, monkeypatch)  # noqa: SLF001
    env["store"].mark_brand(rid, "재현")

    async def boom(*_a: Any, **_k: Any) -> str:
        raise rr.RenderError("render_failed")

    monkeypatch.setattr(rr, "_api_image", boom)
    monkeypatch.setattr(rr, "_mcp_render", boom)
    A._redraw(env, rid)  # noqa: SLF001
    rec = env["store"].get(rid)
    assert rec["render"]["sheet_image"]["status"] == "failed"
    assert not rr.sheet_ready(rec)
    assert rr.original_part(rec) == rr.ORIGINAL_PART  # 원본 표시는 원본 칸에 그대로
    assert rec["render"]["original_image"]["source"] == rr.ORIGINAL_SOURCE
    assert (env["dir"] / "creative" / rid / "original.jpg").read_bytes() == JPEG
    assert rr.sheet_missing(rec)  # 화면은 다시 [캐릭터 시트 만들기]


def test_original_only_in_the_sheet_slot_keeps_its_flag_until_a_sheet_is_done(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """원본 칸으로 못 옮긴 예전 기록: 시작·실패 때 표시를 지우면 원본을 잃는다 → 남긴다."""
    rid = A._legacy_brand(env, monkeypatch)  # noqa: SLF001 - 원본 칸 없음
    assert rr._sheet_flags(rid) == {}  # noqa: SLF001
    assert rr._sheet_flags(rid, done=True)["original"] is False  # noqa: SLF001 - 그린 결과는 지운다
    assert rr._part_flags(rid, "board_image", done=True) == {}  # noqa: SLF001 - 다른 칸은 그대로
