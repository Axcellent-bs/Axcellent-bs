"""시트·스토리보드 그림·영상 참조를 캐릭터별로 (11/27 D · 등장 캐릭터 여럿).

의뢰 준비가 주인공 말고 캐릭터마다 시트(없으면 원본, #1968 판정)를 의뢰 기록 cast_<i> 칸으로
복사하고(크레딧 0), 스토리보드 그림은 구도판 + 캐릭터별 시트, 영상은 캐릭터별 대표 1장씩 참조한다.
가짜 Higgsfield·가짜 관문·임시 저장소만 쓴다."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

from ax_agent_template import company_scope
from ax_contracts.schemas.marketing_brand import CastMember
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import request_creative as rc


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


P = _load("board_prepare_helpers_cast", "test_request_board_image_prepare.py")
M, S = P.M, P.S
mcp = P.mcp
JPEG = P.JPEG
PNG = b"\x89PNG\r\n\x1a\n" + bytes(16)


def _brand(look: str, *, sheet: bool) -> str:
    """회사 캐릭터: 시트가 있으면 만든 시트(sheet.png), 없으면 원본만."""
    rid = cr.store().add_sheet(M.SHEET | {"kind": "virtual_human", "consistency_en": look},
                               None, None)  # fmt: skip
    rr.save_original(rid, JPEG, "image/jpeg")
    if sheet:
        (rr._dir(rid) / "sheet.png").write_bytes(PNG)  # noqa: SLF001
        render = cr.store().get_render(rid)
        render["sheet_image"] = {"status": "done", "asset": "sheet.png", "sha256": "s",
                                 "execution_id": "exe_1"}  # fmt: skip
        cr.store().set_render(rid, render)
    return rid


def _cast_record(mcp_env: dict[str, Any]) -> tuple[str, list[str]]:
    rid = P._record()  # noqa: SLF001 - 주인공 의뢰 기록 (시트 + 원본 + 칸 SVG)
    with company_scope.using("catbooks"):
        man = _brand("a man with short hair and a grey coat", sheet=True)
        cat = _brand("an orange cat mascot with a green scarf", sheet=False)
        cast = [CastMember(record_id=rid, role_ko="주인공"),
                CastMember(record_id=man, role_ko="동료"),
                CastMember(record_id=cat, role_ko="마스코트")]  # fmt: skip
        rc._copy_cast(rid, cast)  # noqa: SLF001
    return rid, [man, cat]


def test_each_character_picture_is_copied_to_the_request_record(mcp: dict[str, Any]) -> None:
    rid, (man, cat) = _cast_record(mcp)
    with company_scope.using("catbooks"):
        rec = cr.store().get(rid) or {}
        folder = rr._dir(rid)  # noqa: SLF001
    render = rec["render"]
    assert [m["role_ko"] for m in render["cast"]["members"]] == ["주인공", "동료", "마스코트"]
    one, two = render["cast_1"], render["cast_2"]
    assert (one["character_id"], one["picture"], one["asset"]) == (man, "sheet", "cast_1.png")
    assert (two["character_id"], two["picture"], two["asset"]) == (cat, "original", "cast_2.jpg")
    assert (folder / "cast_1.png").read_bytes() == PNG
    assert (folder / "cast_2.jpg").read_bytes() == JPEG
    assert "grey coat" in one["look_en"]
    assert mcp["gate"].calls == []  # 복사만 (새 그림·크레딧 없음)


def test_board_uses_the_layout_and_every_character_sheet(mcp: dict[str, Any]) -> None:
    rid, _ = _cast_record(mcp)
    P._prepare(mcp, rid)  # noqa: SLF001
    gen = [str(args) for name, args in mcp["hf"].tools if name.startswith("generate_image")]
    assert len(gen) == 1
    assert "Character B (동료): a man with short hair" in gen[0]
    assert "Character C (마스코트): an orange cat mascot" in gen[0]
    assert "keep each identical across panels; do not merge faces" in gen[0]
    # 참조 = 구도판 + 주인공 시트 + 캐릭터 2명: 올린 파일 4개
    assert mcp["hf"].names().count("media_upload") == 4


def test_video_refs_one_per_character_and_single_ref_models_take_only_the_lead(
    mcp: dict[str, Any],
) -> None:
    rid, _ = _cast_record(mcp)
    with company_scope.using("catbooks"):
        rec = cr.store().get(rid) or {}
        refs = rr.cast_ref_bytes(rid, rec, None)
    assert refs == [PNG, JPEG]
    assert rr.cast_video_refs(rec, "kling_4_0_flash") == 3  # 주인공 + 2명 (상한 10 안)
    assert rr.cast_video_refs(rec, "kling4_0") == 1  # 참조 1장 모델 → 주인공만
    assert rr.cast_video_refs(rec, "kling3_0_turbo") == 1  # 참조를 안 받는 모델
    with company_scope.using("catbooks"):
        params = rr._video_mcp_params(rec, 10, "catbooks")  # noqa: SLF001
    assert isinstance(params, dict)
    text = rr.cast_prompt_en(rec, board=False)
    assert "Person A (주인공)" in text and "Character B (동료)" in text
    assert "keep each identical across the video" in text


def test_no_cast_or_one_member_keeps_todays_result(mcp: dict[str, Any]) -> None:
    rid = P._record()  # noqa: SLF001
    with company_scope.using("catbooks"):
        rec = cr.store().get(rid) or {}
    assert rr.cast_members(rec) == [] and rr.cast_prompt_en(rec, board=True) == ""
    assert rr.cast_video_refs(rec, "kling_4_0_flash") == 0
    assert rr.cast_ref_bytes(rid, rec, None) == []
    P._prepare(mcp, rid)  # noqa: SLF001
    assert mcp["hf"].names().count("media_upload") == 2  # 지금처럼 구도판 + 시트
