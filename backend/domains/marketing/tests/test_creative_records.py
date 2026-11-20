"""이미지 시트·스토리보드 제작 기록 (`creative_records` + 핸들러). 가짜 LLM, 메모리 DB."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import character_sheet as cs
from marketing_agent import creative_records as cr
from marketing_agent import handlers
from marketing_agent import storyboard as sb

PNG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) + bytes(64)
IMAGE = {"media_type": "image/png", "data_base64": base64.b64encode(PNG).decode()}
STORY = "가을 들판에서 소녀가 고양이를 만난다"


def sheet_read() -> cs.CharacterRead:
    return cs.CharacterRead.model_validate(
        {
            "has_character": True,
            "summary_ko": "단발머리 소녀",
            "face_en": "round face",
            "hair_en": "short brown bob",
            "eyes_en": "brown eyes",
            "body_en": "slim girl",
            "outfit_en": "cream cardigan",
            "art_style_en": "anime illustration",
            "proportions_en": "6 heads tall",
            "palette": [
                {"name": "brown", "hex": "#7B4A2D"},
                {"name": "cream", "hex": "#F3E9D2"},
                {"name": "navy", "hex": "#1F2A44"},
            ],
        }
    )


def board_read(n: int) -> sb.StoryRead:
    shots = [{"scene_ko": f"장면 {i}", "shot_size": "wide", "angle": "eye_level"} for i in range(n)]
    return sb.StoryRead.model_validate({"title_ko": "들판", "logline_ko": "하루", "shots": shots})


def sheet_llm(read: cs.CharacterRead) -> MockLLMClient:
    return MockLLMClient(responses={cs.CharacterRead: read})


def board_llm(read: sb.StoryRead) -> MockLLMClient:
    return MockLLMClient(responses={sb.StoryRead: read})


JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 32).decode()


@pytest.fixture(autouse=True)
def mem_store() -> Iterator[cr.CreativeRecords]:
    s = cr.CreativeRecords(":memory:")
    cr.set_store(s)
    yield s
    cr.set_store(None)


def _req(skill: str, untrusted: dict | None = None, inp: dict | None = None) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_test_creative",
            token="t",
            idempotency_key="k1",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        ),
        skill=skill,
        input=inp or {},
        untrusted=untrusted or {},
    )


def _run(skill: str, **kw: dict):
    return asyncio.run(handlers.HANDLERS[skill](_req(skill, **kw)))


def test_sheet_then_storyboard_lands_in_one_record(monkeypatch: pytest.MonkeyPatch) -> None:
    s_llm, b_llm = sheet_llm(sheet_read()), board_llm(board_read(6))
    monkeypatch.setattr(handlers, "CHARACTER_SHEET_LLM", lambda: s_llm)
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: b_llm)
    untrusted = {"image": IMAGE, "note": "겨울옷", "thumbnail": JPEG}
    sheet = _run("character_sheet_prompt", untrusted=untrusted)
    rid = sheet.output["record_id"]
    assert rid and rid.startswith("CR-")
    board = _run("storyboard_prompt", untrusted={"story": STORY, "shots": 6, "record_id": rid})
    assert board.output["record_id"] == rid

    items = _run("creative_records_list", inp={"limit": 10}).output["items"]
    assert [i["record_id"] for i in items] == [rid]
    assert items[0]["shot_count"] == 6 and items[0]["thumbnail_base64"] == JPEG
    assert items[0]["note"] == "겨울옷" and items[0]["story"] == STORY

    got = _run("creative_record_get", inp={"record_id": rid}).output
    assert got["sheet"]["prompt_en"] == sheet.output["prompt_en"]
    assert len(got["storyboard"]["shots"]) == 6
    assert "record_id" not in got["sheet"]  # 저장본에는 번호를 겹쳐 넣지 않는다


def test_storyboard_without_a_real_record_is_not_attached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    b_llm = board_llm(board_read(6))
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: b_llm)
    out = _run("storyboard_prompt", untrusted={"story": STORY, "shots": 6})
    assert out.status == TaskStatus.COMPLETED and out.output["record_id"] is None
    ghost = {"story": STORY, "shots": 6, "record_id": "CR-000000000000"}
    assert _run("storyboard_prompt", untrusted=ghost).output["record_id"] is None


def test_unknown_record_is_not_found() -> None:
    out = _run("creative_record_get", inp={"record_id": "CR-0123456789ab"})
    assert out.status == TaskStatus.FAILED and out.outcome == "not_found"


@pytest.mark.parametrize(
    "thumb",
    [None, "", "not base64!", base64.b64encode(b"\x89PNG....").decode(), "A" * 90_000],
)
def test_only_small_jpeg_thumbnails_are_kept(thumb: object) -> None:
    assert cr.clean_thumbnail(thumb) is None


def test_old_records_are_trimmed(
    mem_store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cr, "KEEP", 3)
    for i in range(5):
        mem_store.add_sheet({"summary_ko": f"캐릭터 {i}"}, None, None)
    assert len(mem_store.list(50)) == 3


def test_record_failure_does_not_break_the_sheet(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def add_sheet(self, *a: object) -> str:
            raise RuntimeError("disk full")

    s_llm = sheet_llm(sheet_read())
    monkeypatch.setattr(handlers, "CHARACTER_SHEET_LLM", lambda: s_llm)
    monkeypatch.setattr(cr, "store", lambda: Broken())
    out = _run("character_sheet_prompt", untrusted={"image": IMAGE})
    assert out.status == TaskStatus.COMPLETED and out.output["record_id"] is None
