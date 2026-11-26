"""가상 인물·일러스트도 진짜 캐릭터 시트 (11/26 A).

운영 CR-cb113dca245a(누비투어): "이미지 시트"가 올린 원본 사진 복사본이었다(정면·옆·뒤·표정·색표
없음). 원본은 원본 칸에, 시트 그림은 일반 캐릭터처럼 Higgsfield로. 영상 참조는 지금처럼 원본.
가짜 LLM·가짜 Higgsfield·가짜 관문·임시 저장소만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import creative_render as rr
from marketing_agent import handlers, request_creative


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("virtual_human_helpers_real_sheet", "test_virtual_human_character.py")
Q = V.Q
env = V.env
JPEG = V.JPEG


def _redraw(env: dict[str, Any], rid: str) -> Any:
    request = AgentRequest(
        meta=RequestMeta(context_id="ctx_sheet_btn", token="t", idempotency_key="btn1",
                         deadline=datetime.now(UTC) + timedelta(seconds=60)),
        skill="creative_board_redraw", input={"record_id": rid},
    )  # fmt: skip

    async def go() -> Any:
        out = await handlers.HANDLERS["creative_board_redraw"](request)
        await rr.wait_idle()
        return out

    return asyncio.run(go())


def _legacy_brand(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> str:
    """11/26 전 등록: 원본이 시트 그림 자리(source upload_original), 원본 칸 없음."""
    out = V._upload(env, monkeypatch, "virtual_human", confirmed=True)  # noqa: SLF001
    rid = out.output["record_id"]
    folder = env["dir"] / "creative" / rid
    (folder / "sheet.jpg").write_bytes(JPEG)
    (folder / "original.jpg").unlink()
    render = env["store"].get_render(rid)
    render.pop("original_image")
    render["sheet_image"] = {"status": "done", "asset": "sheet.jpg", "sha256": "x",
                             "source": rr.ORIGINAL_SOURCE}  # fmt: skip
    env["store"].set_render(rid, render)
    env["gate"].calls.clear()
    return rid


def test_registration_keeps_the_original_for_video_and_draws_a_sheet(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = V._upload(env, monkeypatch, "virtual_human", confirmed=True)  # noqa: SLF001
    rid = out.output["record_id"]
    rec = env["store"].get(rid)
    assert rr.original_part(rec) == rr.ORIGINAL_PART
    # 영상 참조는 지금처럼 올린 원본 파일 (image_references 모델)
    assert rr.video_reference(rec, "kling4_0")[0] == rr.ORIGINAL_PART
    assert rr._ref_bytes(rid, rr.ORIGINAL_PART, None, rec) == JPEG  # noqa: SLF001
    assert rr.sheet_ready(rec) or rec["render"]["sheet_image"]["status"] != "done"


def test_legacy_record_gets_the_sheet_button_and_keeps_the_original(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _legacy_brand(env, monkeypatch)
    env["store"].mark_brand(rid, "재현")
    rec = env["store"].get(rid)
    assert rr.sheet_missing(rec) and rr.original_part(rec) == "sheet_image"
    # 회사 캐릭터 카드: 시트가 아니라 원본 → 화면 "원본 사진 (시트 아직 없음)" + 버튼
    card = next(c for c in env["store"].brand_characters() if c["record_id"] == rid)
    assert card["preview_part"] == "original_image"
    folder = env["dir"] / "creative" / rid
    assert (folder / "original.jpg").read_bytes() == JPEG  # 원본 칸으로 복사 (지우지 않음)
    out = _redraw(env, rid)  # [캐릭터 시트 만들기] = 기존 다시 그리기 길
    assert out.status == TaskStatus.COMPLETED and out.output["part"] == "sheet_image"
    assert [c[1]["scene_template_id"] for c in env["gate"].calls] == ["creative_sheet_image"]
    rec = env["store"].get(rid)
    assert rec["render"]["original_image"]["source"] == rr.ORIGINAL_SOURCE
    assert (folder / "original.jpg").read_bytes() == JPEG
    assert rr.video_reference(rec, "kling4_0")[0] == rr.ORIGINAL_PART  # 영상 참조는 원본 그대로


def test_request_prepare_copies_the_sheet_and_the_original(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = V._upload(env, monkeypatch, "virtual_human", confirmed=True)  # noqa: SLF001
    brand = out.output["record_id"]
    render = env["store"].get_render(brand)
    (env["dir"] / "creative" / brand / "sheet.jpg").write_bytes(b"\xff\xd8SHEET")
    render["sheet_image"] = {"status": "done", "asset": "sheet.jpg", "sha256": "s"}  # 만든 시트
    env["store"].set_render(brand, render)
    env["store"].mark_brand(brand, "재현")
    rid, why = Q._prepare(brand)  # noqa: SLF001
    assert why is None and rid
    rec = env["store"].get(rid)
    folder = env["dir"] / "creative" / rid
    assert (folder / "sheet.jpg").read_bytes() == b"\xff\xd8SHEET"  # 시트는 만든 시트
    assert (folder / "original.jpg").read_bytes() == JPEG  # 원본도 같이 (영상 참조)
    assert rr.sheet_ready(rec) and not rec["render"]["sheet_image"].get(
        request_creative.SHEET_MISSING)  # fmt: skip
    assert rr.video_reference(rec, "kling4_0")[0] == rr.ORIGINAL_PART


def test_request_prepare_without_a_sheet_marks_sheet_missing(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    brand = _legacy_brand(env, monkeypatch)
    env["store"].mark_brand(brand, "재현")
    rid, why = Q._prepare(brand)  # noqa: SLF001
    assert why is None and rid
    rec = env["store"].get(rid)
    sheet = rec["render"]["sheet_image"]
    assert sheet[request_creative.SHEET_MISSING] is True and sheet["original"] is True
    assert (env["dir"] / "creative" / rid / "original.jpg").read_bytes() == JPEG
    assert rr.video_reference(rec, "kling4_0")[0] == rr.ORIGINAL_PART  # 전과 같은 원본 파일
    assert env["gate"].calls == []  # 의뢰 준비에서 시트 그림을 새로 그리지 않는다
