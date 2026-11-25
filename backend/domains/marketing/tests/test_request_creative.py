"""홍보 영상 의뢰에 회사 대표 캐릭터 붙이기 (11/23). 가짜 Higgsfield·가짜 관문.

- 대표 캐릭터: 회사당 3개(마지막이 기본), 권리 확인 기록, 다른 회사 안 보임
- 영상 준비: 대표 캐릭터 기록을 의뢰용으로 복사(시트 그림 재사용) + 기획 장면으로 스토리보드
- 영상 만들기: 그림은 generate_media(B), 영상은 generate_creative_video(C) 보류 → 승인 뒤 생성,
  거절이면 video_rejected. Higgsfield는 executed일 때만 부른다. 장부는 장 수·영상 초
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import handlers, request_creative
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.video_draft import VideoPlanIn


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


R = _load("creative_render_helpers_brand", "test_creative_render.py")
FakeHiggsfield, Gate, JPEG, SHEET = R.FakeHiggsfield, R.Gate, R.JPEG, R.SHEET

CTX = "ctx_wr_brand0001"
SHEET_FULL = SHEET | {"consistency_en": "a small orange cat with a green scarf"}
PLAN = VideoPlanIn.model_validate(
    {
        "concept": "책방 고양이의 신간 소개",
        "duration_s": 15,
        "script": "고양이가 책방 문을 연다. 책을 추천한다. 창가에서 읽는다.",
        "style": "soft watercolor",
        "scenes": [
            {"n": 1, "visual": "책방 문을 여는 고양이", "prompt": "a cat opens a bookstore door"},
            {"n": 2, "visual": "책 더미 사이 추천", "prompt": "the cat points at a stack of books"},
            {"n": 3, "visual": "창가에서 읽기", "prompt": "the cat reads by the window"},
        ],
    }
)


def _meta(company: str | None = None) -> RequestMeta:
    extra = {"tenant_id": company} if company else {}
    return RequestMeta(
        context_id=CTX,
        token="t",
        idempotency_key="wr_brand0001:generate_media:make",
        deadline=datetime.now(UTC) + timedelta(seconds=60),
        requester="orchestrator",
        **extra,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    monkeypatch.setenv("MARKETING_HIGGSFIELD_API_KEY", "kid:ksecret")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_IMAGE_MODEL", "google/nano-banana/edit")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_VIDEO_MODEL", "kling/video/image-to-video")
    monkeypatch.setenv("MARKETING_HIGGSFIELD_VIDEO_DURATION_S", "10")
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path))
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "10")
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "60")
    monkeypatch.setenv("MARKETING_QUOTA_AUDIO_COUNT_MONTHLY", "5")
    store = cr.CreativeRecords(str(tmp_path / "creative.db"))
    cr.set_store(store)
    hf, gate = FakeHiggsfield(), Gate()
    monkeypatch.setattr(
        rr, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(hf.handle))
    )
    monkeypatch.setattr(rr, "execute", gate)
    monkeypatch.setattr(rr, "SLEEP", lambda _s: asyncio.sleep(0))  # 기다림 없이 다른 일에 양보
    ledger = MediaLedger(":memory:")
    yield {"store": store, "hf": hf, "gate": gate, "ledger": ledger, "dir": tmp_path}
    cr.set_store(None)


def _run(fn):  # noqa: ANN001, ANN202
    async def go():  # noqa: ANN202
        out = await fn()
        await rr.wait_idle()
        return out

    return asyncio.run(go())


def _brand(env: dict[str, Any], *, with_image: bool = True, note: str = "책방 고양이") -> str:
    rid = env["store"].add_sheet(SHEET_FULL, note, "/9j/AAAA")
    if with_image:
        folder = env["dir"] / "creative" / rid
        folder.mkdir(parents=True)
        (folder / "sheet.jpg").write_bytes(JPEG)
        rr._set(rid, "sheet_image", status="done", asset="sheet.jpg", sha256="abc")  # noqa: SLF001
    env["store"].mark_brand(rid, "태윤")
    return rid


def _make(env: dict[str, Any], record_id: str, company: str | None = None) -> Any:
    request = AgentRequest(
        meta=_meta(company),
        skill="generate_media",
        input={
            "post_draft_id": "spd_x",
            "media_kind": "video",
            "scene_template_id": "plan_story_video",
            request_creative.INPUT_KEY: record_id,
        },
    )

    async def go():  # noqa: ANN202
        with company_scope.using(company):
            return await request_creative.make_video(request, env["ledger"])

    return _run(go)


def _prepare(brand: str) -> tuple[str | None, str | None]:
    return request_creative.prepare_storyboard(brand, CTX, PLAN)


def _month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


# ---------------------------------------------------------------- 대표 캐릭터


def test_brand_characters_keep_three_and_the_last_is_default(env: dict[str, Any]) -> None:
    ids = [_brand(env, with_image=False, note=f"캐릭터 {i}") for i in range(4)]
    items = env["store"].brand_characters()
    assert [i["record_id"] for i in items] == [ids[3], ids[2], ids[1]]  # 가장 오래된 것이 빠짐
    assert items[0]["is_default"] and not items[1]["is_default"]
    assert items[0]["rights_confirmed_by"] == "태윤" and items[0]["rights_confirmed_at"]
    assert env["store"].get(ids[0]) is not None  # 표시만 떨어지고 기록은 남는다
    assert env["store"].unmark_brand(ids[3]) and not env["store"].unmark_brand(ids[3])
    assert env["store"].brand_characters()[0]["record_id"] == ids[2]


def test_brand_records_survive_the_keep_limit(env: dict[str, Any], monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(cr, "KEEP", 2)
    brand = _brand(env, with_image=False)
    for _ in range(3):
        env["store"].add_sheet(SHEET, None, None)
    assert env["store"].get(brand) is not None


def test_other_company_does_not_see_the_brand_character(env: dict[str, Any]) -> None:
    brand = _brand(env)
    with company_scope.using("nubitour"):
        other = cr.store()
        assert other is not env["store"] and other.company_id == "nubitour"
        assert other.brand_characters() == [] and other.get(brand) is None
        assert other.mark_brand(brand, "x") is False
        assert _prepare(brand) == (None, "record_not_found")
    assert cr.store().brand_characters()[0]["record_id"] == brand


def _skill(skill: str, input_: dict[str, Any], company: str | None = None) -> Any:
    request = AgentRequest(meta=_meta(company), skill=skill, input=input_)

    async def go():  # noqa: ANN202
        with company_scope.using(company):
            return await handlers.HANDLERS[skill](request)

    return asyncio.run(go())


def test_mark_skill_needs_rights_and_records_who(env: dict[str, Any]) -> None:
    rid = env["store"].add_sheet(SHEET_FULL, "고양이", None)
    refused = _skill("brand_character_mark", {"record_id": rid, "who": "태윤"})
    assert refused.status == TaskStatus.FAILED and refused.outcome == "rights_not_confirmed"
    assert env["store"].brand_characters() == []
    ok = {"record_id": rid, "rights_confirmed": True, "who": "태윤"}
    done = _skill("brand_character_mark", ok)
    assert done.status == TaskStatus.COMPLETED and done.output["marked"] is True
    item = _skill("brand_characters_list", {}).output["items"][0]
    assert (item["record_id"], item["rights_confirmed_by"]) == (rid, "태윤")
    assert _skill("brand_characters_list", {}, "nubitour").output["items"] == []
    gone = _skill("brand_character_unmark", {"record_id": rid}, "nubitour")
    assert gone.outcome == "not_found"  # 다른 회사는 표시를 뗄 수도 없다
    assert _skill("brand_character_unmark", {"record_id": rid}).output["marked"] is False


# ---------------------------------------------------------------- 영상 준비


def test_prepare_copies_the_brand_and_builds_a_storyboard_from_plan_scenes(
    env: dict[str, Any],
) -> None:
    brand = _brand(env)
    rid, why = _prepare(brand)
    assert why is None and rid and rid != brand
    assert _prepare(brand) == (rid, None)  # 다시 불려도 같은 기록 하나
    rec = env["store"].get(rid)
    sheet = rec["render"]["sheet_image"]
    assert sheet["status"] == "done" and sheet["source"] == "brand_copy"
    assert (env["dir"] / "creative" / rid / "sheet.jpg").read_bytes() == JPEG
    board = rec["storyboard"]
    assert [s["scene_ko"] for s in board["shots"]] == [
        "책방 문을 여는 고양이",
        "책 더미 사이 추천",
        "창가에서 읽기",
    ]
    assert board["character_used"] and "green scarf" in board["sheet_prompt_en"]
    assert board["style_en"] == "soft watercolor"
    assert env["store"].get(brand)["storyboard"] is None  # 대표 캐릭터 기록은 그대로
    assert env["hf"].submits == [] and env["gate"].calls == []  # 준비에서는 만들지 않는다


def test_prepare_skill_passes_the_record_to_generate_media(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    brand = _brand(env)
    plan = PLAN.model_dump() | {"duration_s": 15}
    out = _skill(
        "prepare_video_draft_from_plan",
        {"proposal_id": "PROP-1", "video_plan": plan, "brand_character_record_id": brand},
    ).output
    assert out["creative_record_id"] and out["brand_character_skipped"] is None
    assert out["generate_media_input"][request_creative.INPUT_KEY] == out["creative_record_id"]
    unknown = {"brand_character_record_id": "CR-000000000000"}
    missing = _skill(
        "prepare_video_draft_from_plan", {"proposal_id": "PROP-1", "video_plan": plan} | unknown
    ).output
    assert missing["creative_record_id"] is None
    assert missing["brand_character_skipped"] == "record_not_found"
    assert request_creative.INPUT_KEY not in missing["generate_media_input"]  # 지금 흐름 그대로


EXTRA_JPEG = JPEG + b"extra-1"
EXTRA_PNG = b"\x89PNG\r\n\x1a\n" + b"extra-2"


def _virtual_brand(env: dict[str, Any], refs: list[int]) -> str:
    """가상 인물 대표 캐릭터: 원본 + `references`에 적은 추가 번호(파일은 1번 jpg·2번 png만)."""
    listed = [{"index": i, "label": "x"} for i in [0, *refs]]
    sheet = SHEET_FULL | {"kind": "virtual_human", "references": listed}
    rid = env["store"].add_sheet(sheet, "가상 인물", None)
    rr.save_original(rid, JPEG, "image/jpeg")
    rr.save_extra(rid, 1, EXTRA_JPEG, "image/jpeg")
    rr.save_extra(rid, 2, EXTRA_PNG, "image/png")
    env["store"].mark_brand(rid, "태윤")
    return rid


def test_prepare_copies_the_brand_extra_references(env: dict[str, Any]) -> None:
    brand = _virtual_brand(env, [1, 2, 3])  # 3번은 파일이 없다 → 건너뜀
    rid, why = _prepare(brand)
    assert why is None and rid
    folder = env["dir"] / "creative" / rid
    assert (folder / "ref_1.jpg").read_bytes() == EXTRA_JPEG
    assert (folder / "ref_2.png").read_bytes() == EXTRA_PNG
    assert not list(folder.glob("ref_3.*"))
    rec = env["store"].get(rid)
    assert rr.extra_reference_bytes(rid, rec, None) == [EXTRA_JPEG, EXTRA_PNG]


def test_prepare_fills_only_missing_extra_references_when_called_again(
    env: dict[str, Any],
) -> None:
    brand = _virtual_brand(env, [1, 2])
    rid, _ = _prepare(brand)
    folder = env["dir"] / "creative" / str(rid)
    (folder / "ref_2.png").unlink()  # 예전 복사(추가 참조 없음)처럼
    (folder / "ref_1.jpg").write_bytes(b"kept")
    assert _prepare(brand) == (rid, None)
    assert (folder / "ref_2.png").read_bytes() == EXTRA_PNG  # 빠진 것만 채운다
    assert (folder / "ref_1.jpg").read_bytes() == b"kept"  # 있는 파일은 그대로


def test_extra_references_not_in_the_sheet_list_are_not_copied(env: dict[str, Any]) -> None:
    rid, _ = _prepare(_virtual_brand(env, [1]))  # 2번 파일은 있지만 references에 없다
    folder = env["dir"] / "creative" / str(rid)
    assert (folder / "ref_1.jpg").exists() and not (folder / "ref_2.png").exists()


def test_keyframe_image_references_include_the_copied_extra_references(
    env: dict[str, Any],
) -> None:
    from marketing_agent import keyframes

    rid, _ = _prepare(_virtual_brand(env, [1, 2]))
    rec = env["store"].get(str(rid))
    refs = keyframes._refs(str(rid), rec, b"layout", None)  # noqa: SLF001 - 그림 참조 조립 경로
    assert refs[:2] == [b"layout", JPEG] and EXTRA_JPEG in refs[2:]


def test_extra_references_are_not_copied_into_another_company_folder(
    env: dict[str, Any],
) -> None:
    brand = _virtual_brand(env, [1, 2])  # 기본 회사
    with company_scope.using("nubitour"):
        assert _prepare(brand) == (None, "record_not_found")  # 다른 회사는 이 대표 캐릭터를 못 본다
    assert not (env["dir"] / "companies" / "nubitour").exists()


# ---------------------------------------------------------------- 영상 만들기


def test_with_a_sheet_image_only_the_storyboard_image_is_made_then_video_is_held(
    env: dict[str, Any],
) -> None:
    rid, _ = _prepare(_brand(env))
    held = _make(env, rid)
    assert held.status == TaskStatus.FAILED and held.outcome == request_creative.VIDEO_HELD
    assert held.output["review_id"] == "rev_1" and held.output["retryable"] is False
    actions = [a for a, _p, _k in env["gate"].calls]
    assert actions == ["generate_media", "generate_creative_video"]  # 시트 그림은 다시 안 만든다
    assert [m for m, _ in env["hf"].submits] == ["google/nano-banana/edit"]  # 영상은 아직 안 부름
    assert env["ledger"].month_usage(_month(), "image") == 1
    assert env["ledger"].month_usage(_month(), "video") == 10  # 영상은 초로 예약
    # 아직 사람이 정하지 않았다: 다시 불러도 같은 키로 묻기만 한다
    again = _make(env, rid)
    assert again.outcome == request_creative.VIDEO_HELD
    assert env["gate"].calls[-1][2] == env["gate"].calls[1][2]  # 같은 멱등 키
    assert len(env["hf"].submits) == 1


def test_after_approval_the_video_is_made_and_listed(env: dict[str, Any]) -> None:
    rid, _ = _prepare(_brand(env))
    _make(env, rid)
    env["gate"].status["generate_creative_video"] = "executed"  # 사람이 승인했다
    done = _make(env, rid)
    assert done.status == TaskStatus.COMPLETED and done.outcome == "generated"
    assert done.output["creative_record_id"] == rid and done.output["media_kind"] == "video"
    assert Path(done.output["asset_ref"]).read_bytes() == b"video-bytes"
    assert [m for m, _ in env["hf"].submits][-1] == "kling/video/image-to-video"
    meta = (env["dir"] / "creative" / rid / "meta.json").read_text(encoding="utf-8")
    assert '"post_draft_id": "spd_x"' in meta and '"media_kind": "video"' in meta
    video_calls = [k for a, _p, k in env["gate"].calls if a == "generate_creative_video"]
    assert len(set(video_calls)) == 1  # 승인도 같은 키로 확인했다
    assert env["ledger"].month_usage(_month(), "image") == 1  # 그림은 의뢰당 최대 2장


def test_rejected_video_is_not_asked_again(env: dict[str, Any]) -> None:
    rid, _ = _prepare(_brand(env))
    _make(env, rid)
    env["gate"].status["generate_creative_video"] = "rejected"
    rejected = _make(env, rid)
    assert rejected.outcome == request_creative.VIDEO_REJECTED
    calls = len(env["gate"].calls)
    assert _make(env, rid).outcome == request_creative.VIDEO_REJECTED
    assert len(env["gate"].calls) == calls  # 거절 뒤에는 관문에 다시 올리지 않는다
    assert all("video" not in m for m, _ in env["hf"].submits)


def test_without_a_sheet_image_two_images_at_most(env: dict[str, Any]) -> None:
    rid, _ = _prepare(_brand(env, with_image=False))
    _make(env, rid)
    assert env["ledger"].month_usage(_month(), "image") == 2  # 시트 1장 + 스토리보드 1장
    parts = [p["scene_template_id"] for a, p, _k in env["gate"].calls if a == "generate_media"]
    assert parts == ["creative_sheet_image", "creative_board_image"]
    _make(env, rid)  # 다시 불러도 그림을 더 만들지 않는다
    assert env["ledger"].month_usage(_month(), "image") == 2


def test_higgsfield_is_never_called_when_the_gate_blocks_images(env: dict[str, Any]) -> None:
    env["gate"].status["generate_media"] = "rejected"
    rid, _ = _prepare(_brand(env))
    out = _make(env, rid)
    assert out.status == TaskStatus.FAILED and out.outcome.startswith("board_image_")
    assert env["hf"].submits == []
    assert "generate_creative_video" not in [a for a, _p, _k in env["gate"].calls]


def test_other_company_files_go_to_its_own_folder(env: dict[str, Any]) -> None:
    with company_scope.using("nubitour"):
        store = cr.store()
        rid = store.add_sheet(SHEET_FULL, "누비", None)
        store.mark_brand(rid, "x")
        made, _ = _prepare(rid)
    assert made
    _make(env, made, "nubitour")
    folder = env["dir"] / "companies" / "nubitour" / "creative" / made
    assert (folder / "board.jpg").exists() and (folder / "sheet.jpg").exists()
    assert not (env["dir"] / "creative" / made).exists()


def test_requests_without_a_character_keep_the_old_path(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[str] = []

    async def old_flow(request, **_kw):  # noqa: ANN001, ANN003, ANN202
        seen.append("generate_media_flow")
        return handlers._result(request, TaskStatus.FAILED, outcome="x")  # noqa: SLF001

    async def creative(*_a):  # noqa: ANN002, ANN202
        raise AssertionError("캐릭터 없는 의뢰가 캐릭터 경로로 갔다")

    monkeypatch.setattr(handlers, "generate_media_flow", old_flow)
    monkeypatch.setattr(request_creative, "make_video", creative)
    request = AgentRequest(meta=_meta(), skill="generate_media", input={"post_draft_id": "spd_x"})
    asyncio.run(handlers._generate_media_sync(request))  # noqa: SLF001
    assert seen == ["generate_media_flow"]
