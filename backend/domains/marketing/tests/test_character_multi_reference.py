"""회사 캐릭터 참조 여러 장 (11/25): 대표 + 추가 최대 3장, 영상 image_references에 순서대로.

가짜 LLM·가짜 Higgsfield·가짜 관문·임시 저장소만 쓴다(실제로 부르지 않는다).
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import character_sheet as cs
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import handlers


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


C = _load("character_sheet_helpers_multi", "test_character_sheet.py")
Q = _load("request_creative_helpers_multi", "test_request_creative.py")
M = _load("creative_render_mcp_helpers_multi", "test_creative_render_mcp.py")
env = Q.env


async def _start_board(e: dict[str, Any], rid: str) -> None:
    rr.maybe_start_board(e["ledger"], M.META, rid)


def _jpeg(width: int, height: int, tag: int) -> bytes:
    sof = b"\xff\xc0\x00\x11\x08" + height.to_bytes(2, "big") + width.to_bytes(2, "big")
    return b"\xff\xd8\xff\xe0\x00\x04\x00\x00" + sof + bytes([tag]) * 12 + bytes(20)


LLM_CALLS: list[int] = []
FRONT = _jpeg(640, 480, 1)
EXTRAS = [_jpeg(300, 200 + i, 10 + i) for i in range(3)]


def _b64(data: bytes, label: str | None = None) -> dict[str, Any]:
    encoded = base64.b64encode(data).decode()
    out: dict[str, Any] = {"media_type": "image/jpeg", "data_base64": encoded}
    if label:
        out["label"] = label
    return out


def _request(kind: str, extras: list[bytes], company: str | None = None) -> AgentRequest:
    extra = {"tenant_id": company} if company else {}
    labels = ["full", "side", "face"]
    return AgentRequest(
        meta=RequestMeta(context_id="ctx_multi", token="t", idempotency_key="multi1",
                         deadline=datetime.now(UTC) + timedelta(seconds=60), **extra),
        skill="character_sheet_prompt",
        input={"kind": kind, "virtual_confirmed": True},
        untrusted={"image": _b64(FRONT),
                   "extra_images": [_b64(d, labels[i % 3]) for i, d in enumerate(extras)]},
    )  # fmt: skip


def _upload(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kind: str, extras: list[bytes],
            company: str | None = None) -> Any:  # fmt: skip
    llm = C._llm(C._read())  # noqa: SLF001
    calls: list[int] = []
    monkeypatch.setattr(handlers, "CHARACTER_SHEET_LLM", lambda: (calls.append(1), llm)[1])
    monkeypatch.setattr(handlers.STATE, "media_ledger", env["ledger"])

    async def go() -> Any:
        with company_scope.using(company):
            out = await handlers.HANDLERS["character_sheet_prompt"](
                _request(kind, extras, company))
            await rr.wait_idle()
            return out

    result = asyncio.run(go())
    LLM_CALLS[:] = calls
    return result


def test_three_extras_are_saved_in_order(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, "virtual_human", EXTRAS)
    assert out.status == TaskStatus.COMPLETED
    rid = out.output["record_id"]
    folder = env["dir"] / "creative" / rid
    assert (folder / "sheet.jpg").read_bytes() == FRONT
    for i, data in enumerate(EXTRAS, start=1):
        assert (folder / f"ref_{i}.jpg").read_bytes() == data
    refs = out.output["references"]
    assert [(r["index"], r["label"]) for r in refs] == [
        (0, "front"), (1, "full"), (2, "side"), (3, "face")]  # fmt: skip
    assert (refs[0]["width"], refs[0]["height"]) == (640, 480)
    assert (refs[1]["width"], refs[1]["height"]) == (300, 200)
    assert env["store"].get(rid)["sheet"]["references"] == refs
    assert env["gate"].calls == []  # 그림 크레딧 0
    assert len(LLM_CALLS) == 1  # 시트 설명 LLM은 대표 한 장으로 1회


def test_fourth_extra_is_refused(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, "illustration", [*EXTRAS, EXTRAS[0]])
    assert out.status == TaskStatus.FAILED and out.outcome == "too_many_images"
    assert env["store"].list() == []


def test_real_photo_with_extras_is_refused(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, "real_photo", EXTRAS[:1])
    assert out.status == TaskStatus.FAILED and out.outcome == "extra_images_not_allowed"
    assert env["store"].list() == []


def test_bad_extra_is_refused(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, "virtual_human", [b"not an image"])
    assert out.status == TaskStatus.FAILED and out.outcome == "invalid_image"


def test_single_image_still_works_and_has_front_only(
    env: dict[str, Any], monkeypatch  # noqa: ANN001
) -> None:
    out = _upload(env, monkeypatch, "virtual_human", [])
    assert out.status == TaskStatus.COMPLETED
    assert [r["label"] for r in out.output["references"]] == ["front"]


def test_old_record_reads_as_front_only(env: dict[str, Any]) -> None:  # noqa: ANN001
    rid = env["store"].add_sheet(Q.SHEET_FULL, "옛 기록", None)  # references 칸이 없는 옛 시트
    env["store"].mark_brand(rid, "x")
    assert env["store"].list()[0]["references"] == []
    assert env["store"].brand_characters()[0]["references"] == []
    assert rr.extra_reference_bytes(rid, env["store"].get(rid), None) == []


def test_other_company_folder_is_separate(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, "virtual_human", EXTRAS, company="nubitour")
    rid = out.output["record_id"]
    folder = env["dir"] / "companies" / "nubitour" / "creative" / rid
    assert all((folder / f"ref_{i}.jpg").is_file() for i in (1, 2, 3))
    assert not (env["dir"] / "creative" / rid).exists()
    with company_scope.using("nubitour"):
        rec = cr.store().get(rid)
        assert rr.extra_reference_bytes(rid, rec, "nubitour") == EXTRAS
    assert env["store"].get(rid) is None  # 다른 회사 칸에서는 안 보인다


def test_image_size_reads_png_and_jpeg_headers() -> None:
    png = b"\x89PNG\r\n\x1a\n" + bytes(8) + (320).to_bytes(4, "big") + (240).to_bytes(4, "big")
    assert cs.image_size(png) == (320, 240)
    assert cs.image_size(_jpeg(11, 22, 1)) == (11, 22)
    assert cs.image_size(b"\xff\xd8\xff") is None


@pytest.mark.parametrize(("model", "count"), [("kling_4_0_flash", 4), ("kling3_0_turbo", 1)])
def test_video_params_carry_front_then_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, count: int
) -> None:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)
    e = next(gen)
    sent: list[bytes] = []
    real_upload = cm.upload

    async def upload(hf: Any, data: bytes, filename: str, content_type: str) -> str:
        sent.append(data)
        return await real_upload(hf, data, filename, content_type)

    monkeypatch.setattr(cm, "upload", upload)
    try:
        monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", model)
        refs = [{"index": i, "label": "front" if i == 0 else "other"} for i in range(4)]
        with company_scope.using("catbooks"):
            rid = cr.store().add_sheet(
                M.SHEET | {"kind": "virtual_human", "references": refs}, None, None)
            rr.save_original(rid, FRONT, "image/jpeg")
            for i, data in enumerate(EXTRAS, start=1):
                rr.save_extra(rid, i, data, "image/jpeg")
        e["store"].attach_storyboard(rid, "들판 이야기", 6, M.BOARD)
        M._run(lambda: _start_board(e, rid))  # noqa: SLF001
        M._run(lambda: rr.request_video(e["ledger"], M.META, rid, "gaon"))  # noqa: SLF001
        e["gate"].status["generate_creative_video"] = "executed"
        e["hf"].waits = 0
        M._run(lambda: rr.check_video(e["ledger"], M.META, rid))  # noqa: SLF001
        assert e["store"].get_render(rid)["video"]["status"] == "done"
        batch = dict(e["hf"].tools)["generate_video_batch"]["requests"][0]["params"]
        assert len(batch["medias"]) == count
        if count > 1:  # 대표 → 추가 1~3 순서, 모두 image_references
            assert {m["role"] for m in batch["medias"]} == {"image_references"}
            assert sent[-4:] == [FRONT, *EXTRAS]
        else:  # start_image 모델은 지금처럼 스토리보드 그림 한 장
            assert batch["medias"][0]["role"] == "start_image"
    finally:
        gen.close()


def test_reference_count_is_cut_to_the_model_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cm.VIDEO_MODELS, "tiny", {"image_references": True,
                                                   "max_image_references": 2})  # fmt: skip
    assert cm.max_image_references("tiny") == 2
    assert cm.max_image_references("kling_4_0_flash") == 10
    assert cm.max_image_references("unknown") == 1
