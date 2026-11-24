"""회사 캐릭터 "AI 가상 인물"은 그림체로 바꾸지 않고 그대로 참조로 쓴다 (11/24).

운영 wr_cd05b82711(CR-7bd1e3234f97): 실사풍 AI 가상 인물이 업로드 규칙("사람 실사 사진은 그림체
캐릭터로")과 회사 style_en(flat illustration) 때문에 시트·스토리보드·영상 모두 애니메이션 그림체가
됐고, 시트를 다시 그리느라 그림 크레딧도 썼다.

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
from ax_contracts.schemas.marketing_character import VIRTUAL_HUMAN_LABEL
from marketing_agent import character_sheet as cs
from marketing_agent import creative_mcp as cm
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import handlers, request_video, sns_label, sns_prepare


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


C = _load("character_sheet_helpers_virtual", "test_character_sheet.py")
Q = _load("request_creative_helpers_virtual", "test_request_creative.py")
M = _load("creative_render_mcp_helpers_virtual", "test_creative_render_mcp.py")
env = Q.env
JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(64)
IMAGE = {"media_type": "image/jpeg", "data_base64": base64.b64encode(JPEG).decode()}
FLAT = "flat illustration"


def _sheet_request(kind: str | None, confirmed: bool = False, company: str | None = None
                   ) -> AgentRequest:  # fmt: skip
    extra = {"tenant_id": company} if company else {}
    given: dict[str, Any] = {} if kind is None else {"kind": kind, "virtual_confirmed": confirmed}
    return AgentRequest(
        meta=RequestMeta(context_id="ctx_vh_sheet", token="t", idempotency_key="vh1",
                         deadline=datetime.now(UTC) + timedelta(seconds=60), **extra),
        skill="character_sheet_prompt",
        input=given,
        untrusted={"image": IMAGE},
    )  # fmt: skip


def _upload(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kind: str | None,
            confirmed: bool = False, company: str | None = None) -> Any:  # fmt: skip
    # 실사풍 가상 인물: LLM이 실제 사람 사진으로 읽고, 그림체도 flat illustration이라고 답한다
    llm = C._llm(C._read(real_person_photo=True, art_style_en=FLAT))  # noqa: SLF001
    monkeypatch.setattr(handlers, "CHARACTER_SHEET_LLM", lambda: llm)
    monkeypatch.setattr(handlers.STATE, "media_ledger", env["ledger"])

    async def go() -> Any:
        with company_scope.using(company):
            out = await handlers.HANDLERS["character_sheet_prompt"](
                _sheet_request(kind, confirmed, company))
            await rr.wait_idle()
            return out

    return asyncio.run(go())


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


# ---------------------------------------------------------------- 종류별 업로드


def test_real_photo_still_makes_the_sheet_image(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, None)  # 옛 화면(종류 없음) = real_photo
    assert out.status == TaskStatus.COMPLETED and out.output["kind"] == "real_photo"
    assert out.output["stylized_from_photo"] is True  # 지금처럼 그림체로 바꾼다
    rid = out.output["record_id"]
    assert [a for a, _p, _k in env["gate"].calls] == ["generate_media"]  # 시트 그림 1장
    assert env["ledger"].month_usage(_month(), "image") == 1
    sheet = env["store"].get_render(rid)["sheet_image"]
    assert sheet.get("source") != rr.ORIGINAL_SOURCE


@pytest.mark.parametrize("kind", ["virtual_human", "illustration"])
def test_reference_kinds_keep_the_original_and_spend_no_credit(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    out = _upload(env, monkeypatch, kind, confirmed=True)
    assert out.status == TaskStatus.COMPLETED and out.output["kind"] == kind
    assert out.output["stylized_from_photo"] is False  # 그림체로 바꾸지 않는다
    rid = out.output["record_id"]
    assert env["gate"].calls == [] and env["hf"].submits == []  # 시트 그림을 만들지 않는다
    assert env["ledger"].month_usage(_month(), "image") == 0  # 크레딧 예약 없음
    sheet = env["store"].get_render(rid)["sheet_image"]
    assert sheet["status"] == "done" and sheet["source"] == rr.ORIGINAL_SOURCE
    assert (env["dir"] / "creative" / rid / "sheet.jpg").read_bytes() == JPEG  # 원본 그대로
    assert env["store"].get(rid)["sheet"]["kind"] == kind


def test_virtual_human_prompt_has_no_flat_illustration(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _upload(env, monkeypatch, "virtual_human", confirmed=True)
    text = " ".join([out.output["prompt_en"], out.output["consistency_en"],
                     out.output["negative_prompt_en"]])  # fmt: skip
    assert FLAT not in text and "stylized illustrated" not in text
    assert cs.VIRTUAL_STYLE_EN in out.output["prompt_en"]
    assert "photorealistic" not in out.output["negative_prompt_en"]  # 실사를 피하지 않는다
    # 의뢰 스토리보드 그림체도 회사·기획 그림체(soft watercolor) 대신 실사·같은 사람
    rid = out.output["record_id"]
    env["store"].mark_brand(rid, "재현")
    made, why = Q._prepare(rid)  # noqa: SLF001
    assert why is None and made
    board = env["store"].get(made)["storyboard"]
    assert board["style_en"] == cs.VIRTUAL_STYLE_EN and "watercolor" not in board["sheet_prompt_en"]


def test_virtual_human_without_the_check_is_refused(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _upload(env, monkeypatch, "virtual_human", confirmed=False)
    assert out.status == TaskStatus.FAILED and out.outcome == "virtual_unconfirmed"
    assert env["store"].list() == []  # 기록도 남기지 않는다
    bad = _upload(env, monkeypatch, "celebrity")
    assert bad.outcome == "invalid_input"


def test_old_records_read_as_real_photo(env: dict[str, Any]) -> None:
    rid = env["store"].add_sheet(Q.SHEET_FULL, "옛 기록", None)  # kind 칸이 없는 옛 시트
    env["store"].mark_brand(rid, "x")
    assert cr.upload_kind(env["store"].get(rid)["sheet"]) == "real_photo"
    assert env["store"].brand_characters()[0]["kind"] == "real_photo"
    assert env["store"].list()[0]["kind"] == "real_photo"
    assert not rr.is_reference_kind(env["store"].get(rid))


def test_other_company_original_stays_in_its_own_folder(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    out = _upload(env, monkeypatch, "virtual_human", confirmed=True, company="nubitour")
    rid = out.output["record_id"]
    assert (env["dir"] / "companies" / "nubitour" / "creative" / rid / "sheet.jpg").is_file()
    assert not (env["dir"] / "creative" / rid).exists()
    assert env["store"].get(rid) is None  # 캣북스 칸에서는 보이지 않는다
    with company_scope.using("nubitour"):
        assert cr.store().get(rid)["sheet"]["kind"] == "virtual_human"


# ---------------------------------------------------------------- 영상 참조·길이


def _rec(kind: str, source: str | None = rr.ORIGINAL_SOURCE) -> dict[str, Any]:
    sheet = {"status": "done", "source": source}
    return {"sheet": {"kind": kind}, "render": {"sheet_image": sheet}}


def test_video_reference_depends_on_the_model() -> None:
    virtual = _rec("virtual_human")
    board = ("board_image", "start_image")
    assert rr.video_reference(virtual, "kling_4_0_flash") == ("sheet_image", "image_references")
    assert rr.video_reference(virtual, "kling3_0_turbo") == board
    assert rr.video_reference(virtual, "unknown_model") == board  # 모르는 모델은 지금 동작
    assert rr.video_reference(_rec("illustration"), "kling_4_0_flash")[1] == "image_references"
    assert rr.video_reference(_rec("real_photo"), "kling_4_0_flash") == board
    assert rr.video_reference(_rec("virtual_human", None), "kling_4_0_flash")[0] == "board_image"


@pytest.mark.parametrize(("model", "role"), [("kling_4_0_flash", "image_references"),
                                             ("kling3_0_turbo", "start_image")])  # fmt: skip
def test_video_params_send_the_original_or_the_storyboard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model: str, role: str
) -> None:
    gen = M.env.__wrapped__(tmp_path, monkeypatch)  # MCP 가짜 환경 (회사 계정 연결)
    e = next(gen)
    sent: list[bytes] = []
    real_upload = cm.upload

    async def upload(hf: Any, data: bytes, filename: str, content_type: str) -> str:
        sent.append(data)
        return await real_upload(hf, data, filename, content_type)

    monkeypatch.setattr(cm, "upload", upload)
    try:
        monkeypatch.setenv("MARKETING_HIGGSFIELD_MCP_VIDEO_MODEL", model)
        with company_scope.using("catbooks"):
            rid = cr.store().add_sheet(M.SHEET | {"kind": "virtual_human"}, None, None)
            rr.save_original(rid, JPEG, "image/jpeg")
        e["store"].attach_storyboard(rid, "들판 이야기", 6, M.BOARD)
        M._run(lambda: _start_board(e, rid))  # noqa: SLF001
        M._run(lambda: rr.request_video(e["ledger"], M.META, rid, "gaon"))  # noqa: SLF001
        e["gate"].status["generate_creative_video"] = "executed"
        e["hf"].waits = 0
        M._run(lambda: rr.check_video(e["ledger"], M.META, rid))  # noqa: SLF001
        assert e["store"].get_render(rid)["video"]["status"] == "done"
        batch = dict(e["hf"].tools)["generate_video_batch"]["requests"][0]["params"]
        assert batch["model"] == model and batch["medias"][0]["role"] == role
        # 영상 참조로 올린 파일: 가상 인물 원본(시트 그림 자리) 또는 스토리보드 그림
        board = rr.asset_path(rid, "board_image", "catbooks").read_bytes()
        assert sent[-1] == (JPEG if role == "image_references" else board)
        assert board != JPEG
    finally:
        gen.close()


async def _start_board(e: dict[str, Any], rid: str) -> None:
    rr.maybe_start_board(e["ledger"], M.META, rid)


@pytest.mark.parametrize(("cap", "want", "clamped"), [("20", 15, None), ("10", 10, 15)])
def test_video_length_follows_the_plan_within_the_cap(
    monkeypatch: pytest.MonkeyPatch, cap: str, want: int, clamped: int | None
) -> None:
    monkeypatch.setenv(rr.VIDEO_DURATION_ENV, cap)
    assert rr.plan_seconds(15, "kling_4_0_flash") == (want, clamped)
    monkeypatch.setenv(rr.VIDEO_DURATION_ENV, "30")
    assert rr.plan_seconds(25, "kling_4_0_flash") == (20, 25)  # 모델 상한 20
    assert rr.plan_seconds(None, "kling_4_0_flash") == (rr.video_seconds(), None)  # 기획 길이 없음


def test_request_video_records_the_clamped_length(env: dict[str, Any]) -> None:
    rid, _ = Q._prepare(Q._brand(env))  # noqa: SLF001 - 기획 15초, 서버 상한 10초
    assert env["store"].get(rid)["storyboard"][rr.PLAN_DURATION_KEY] == 15
    Q._make(env, rid)  # noqa: SLF001
    video = env["store"].get_render(rid)["video"]
    assert video["seconds"] == 10 and video["seconds_clamped_from"] == 15
    assert video["params"]["duration_s"] == 10


# ---------------------------------------------------------------- AI 가상 인물 표시


def test_virtual_human_video_carries_the_mark(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    out = _upload(env, monkeypatch, "virtual_human", confirmed=True)
    brand = out.output["record_id"]
    env["store"].mark_brand(brand, "재현")
    rid, _ = Q._prepare(brand)  # noqa: SLF001
    Q._make(env, rid)  # noqa: SLF001
    env["gate"].status["generate_creative_video"] = "executed"
    done = Q._make(env, rid)  # noqa: SLF001
    assert done.outcome == "generated"
    assert done.output["ai_virtual_human"] is True
    assert done.output["ai_disclosure"] == VIRTUAL_HUMAN_LABEL
    assert request_video.disclosure(done.output) == VIRTUAL_HUMAN_LABEL
    # 그림 크레딧: 시트 0장 + 스토리보드 1장(지금처럼)
    assert env["ledger"].month_usage(_month(), "image") == 1
    # 캐릭터가 실제 사람 사진이면 표시하지 않는다
    assert request_video.disclosure({"ai_virtual_human": False}) is None


def test_mark_goes_on_the_label_and_the_caption() -> None:
    base = sns_label.load_config()
    marked = sns_prepare.with_note(base, VIRTUAL_HUMAN_LABEL)
    assert marked.text_video == f"{base.text_video} · {VIRTUAL_HUMAN_LABEL}"
    assert marked.text_image.endswith(VIRTUAL_HUMAN_LABEL)
    assert marked.sha256 != base.sha256  # 예전 라벨(표시 없음)을 다시 쓰지 않는다
    assert sns_prepare.with_note(base, None) is base
    caption = request_video.with_disclosure("가을 신간 소개 #책추천", VIRTUAL_HUMAN_LABEL)
    assert caption.startswith("가을 신간 소개")
    assert caption.splitlines()[-1].startswith("※ AI 가상 인물")
    # 두 번 붙이지 않는다
    assert request_video.with_disclosure(caption, VIRTUAL_HUMAN_LABEL) == caption


def test_model_table_only_knows_checked_models() -> None:
    assert cm.takes_image_references("kling_4_0_flash") is True
    assert cm.takes_image_references("kling3_0_turbo") is False
    assert cm.video_max_seconds("kling_4_0_flash") == 20 and cm.video_max_seconds("x") is None
