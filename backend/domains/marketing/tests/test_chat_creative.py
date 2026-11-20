"""마케팅 대화의 이미지 시트·스토리보드 흐름 (`ChatService._creative_turn`). 가짜 LLM, 메모리 DB."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from ax_agent_template import events
from marketing_agent import creative_records as cr
from marketing_agent import handlers
from marketing_agent import storyboard as sb
from marketing_agent.chat import SHEET_ONLY, ChatOut, ChatService, StoryAsk
from marketing_agent.chat_store import ChatStore

ME = "gaon"
SHEET = {"summary_ko": "단발머리 소녀", "consistency_en": "The exact same girl in every panel."}


class FakeLLM:
    """요청한 형식(schema)에 맞춰 답한다."""

    def __init__(self, story: str = "", shots: int | None = None) -> None:
        self.story, self.shots = story, shots
        self.asked: list[type] = []
        self.untrusted: list[dict[str, Any] | None] = []

    async def structured(self, prompt: str, schema: type, untrusted: dict | None = None, **_: Any):
        self.asked.append(schema)
        self.untrusted.append(untrusted)
        if schema is StoryAsk:
            return StoryAsk(story=self.story, shots=self.shots)
        if schema is sb.StoryRead:
            n = untrusted["shots"] if untrusted else 12
            shots = [{"scene_ko": f"장면 {i}", "shot_size": "wide"} for i in range(n)]
            return sb.StoryRead(title_ko="들판의 하루", logline_ko="소녀와 고양이", shots=shots)
        return ChatOut(reply="일반 답이에요.", summary="")


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> Iterator[cr.CreativeRecords]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    store = cr.CreativeRecords(":memory:")
    cr.set_store(store)
    yield store
    cr.set_store(None)


def _svc(llm: FakeLLM) -> ChatService:
    return ChatService(ChatStore(":memory:"), handlers.STATE.chat_facts, lambda: llm)


def _say(svc: ChatService, text: str, thread_id: str | None = None, rid: str | None = None):
    async def go() -> dict:
        th = svc.send(ME, thread_id, text, rid)
        await svc.wait_idle()
        return svc.get(ME, th["id"])

    return asyncio.run(go())


def test_image_with_a_story_makes_a_storyboard_and_attaches_it(_env: cr.CreativeRecords) -> None:
    rid = _env.add_sheet(SHEET, None, None)
    llm = FakeLLM(story="가을 들판에서 소녀가 고양이를 만난다", shots=8)
    th = _say(_svc(llm), "이 캐릭터로 가을 들판에서 고양이를 만나는 스토리보드 만들어 줘", rid=rid)
    user, ai = th["messages"]
    assert user["card"] == {
        "kind": "creative",
        "record_id": rid,
        "role": "attached",
        "has_storyboard": False,
    }
    assert ai["card"]["record_id"] == rid and ai["card"]["has_storyboard"] is True
    assert "스토리보드 9칸" in ai["text"]  # 8칸 → 가까운 9칸
    rec = _env.get(rid)
    assert rec and rec["storyboard"] and len(rec["storyboard"]["shots"]) == 9
    board_in = llm.untrusted[llm.asked.index(sb.StoryRead)]
    assert board_in and board_in["character"] == SHEET["consistency_en"]


def test_image_without_a_story_only_reports_the_sheet(_env: cr.CreativeRecords) -> None:
    rid = _env.add_sheet(SHEET, None, None)
    th = _say(_svc(FakeLLM(story="")), "이 캐릭터로 만들어 줘", rid=rid)
    ai = th["messages"][-1]
    assert ai["text"] == SHEET_ONLY and ai["card"]["has_storyboard"] is False
    rec = _env.get(rid)
    assert rec and rec["storyboard"] is None


def test_later_story_message_uses_the_latest_sheet_in_the_thread(
    _env: cr.CreativeRecords,
) -> None:
    rid = _env.add_sheet(SHEET, None, None)
    llm = FakeLLM(story="")
    svc = _svc(llm)
    first = _say(svc, "캐릭터 올려요", rid=rid)
    llm.story = "바닷가에서 연을 날린다"
    second = _say(svc, "스토리보드: 바닷가에서 연을 날린다", thread_id=first["id"])
    ai = second["messages"][-1]
    assert ai["card"]["record_id"] == rid and ai["card"]["has_storyboard"] is True


def test_plain_question_after_a_sheet_is_normal_chat(_env: cr.CreativeRecords) -> None:
    rid = _env.add_sheet(SHEET, None, None)
    svc = _svc(FakeLLM(story=""))
    first = _say(svc, "캐릭터 올려요", rid=rid)
    second = _say(svc, "받은 기획안 몇 건이야?", thread_id=first["id"])
    assert second["messages"][-1]["text"] == "일반 답이에요."
    assert second["messages"][-1]["card"] is None


def test_unknown_record_is_an_error_line() -> None:
    th = _say(_svc(FakeLLM()), "만들어 줘", rid="CR-0123456789ab")
    assert th["messages"][-1]["kind"] == "error"


def test_storyboard_refusal_is_reported(
    _env: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _env.add_sheet(SHEET, None, None)

    async def refuse(*a: object, **k: object) -> None:
        raise sb.StoryboardRefused("llm_failed", "장면을 다 만들지 못했다")

    monkeypatch.setattr(sb, "make_storyboard", refuse)
    th = _say(_svc(FakeLLM(story="들판")), "들판 스토리보드", rid=rid)
    ai = th["messages"][-1]
    assert ai["kind"] == "error" and "장면을 다 만들지 못했다" in ai["text"]
