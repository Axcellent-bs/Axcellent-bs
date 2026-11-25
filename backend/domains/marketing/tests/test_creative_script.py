"""영상 대본(`creative_script`·`creative_script_save`)과 대본을 따르는 스토리보드·영상 프롬프트.

가짜 LLM, 메모리 DB로만 돈다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_agent_template import events
from ax_agent_template.llm import LLMResponseError
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.marketing_creative import CreativeRecordGetOutput
from marketing_agent import creative_records as cr
from marketing_agent import creative_render as rr
from marketing_agent import creative_script as cs
from marketing_agent import handlers
from marketing_agent import storyboard as sb
from marketing_agent.chat import ChatOut, ChatService, StoryAsk
from marketing_agent.chat_store import ChatStore

SHEET = {"summary_ko": "단발머리 소녀", "consistency_en": "The exact same girl in every panel."}
STORY = "가을 들판에서 소녀가 고양이 3마리를 만난다. 함께 노을을 본다. 주먹밥을 나눠 먹는다."


# 숫자가 든 글은 줄거리에 없으면 빠지므로(숫자 검사) 장면 글에는 숫자 대신 이름을 쓴다
NAMES = ["가", "나", "다", "라", "마", "바", "사", "아", "자", "차", "카", "타"]


def _scene(i: int, **over: Any) -> dict[str, Any]:
    n = NAMES[i - 1]
    base = {
        "visual_ko": f"장면 {n} 화면",
        "speaker_ko": "소녀" if i % 2 else "",
        "line_ko": f"대사 {n}예요.",
        "caption_ko": f"자막{n}",
        "weight": 1,
    }
    base.update(over)
    return base


class FakeLLM:
    """요청한 형식(schema)에 맞춰 답하고, 받은 프롬프트·데이터를 남긴다."""

    def __init__(self, scenes: list[dict[str, Any]] | None = None, story: str = "") -> None:
        self.scenes = scenes
        self.story = story
        self.calls: list[tuple[type, str, dict[str, Any] | None]] = []

    async def structured(self, prompt: str, schema: type, untrusted: dict | None = None, **_: Any):
        self.calls.append((schema, prompt, untrusted))
        if schema is cs.ScriptRead:
            n = (untrusted or {}).get("scenes", 6)
            scenes = (
                self.scenes if self.scenes is not None else [_scene(i) for i in range(1, n + 1)]
            )
            return cs.ScriptRead(title_ko="들판의 하루", scenes=scenes, cta_ko="지금 만나 보세요")
        if schema is sb.StoryRead:
            n = (untrusted or {}).get("shots", 12)
            shots = [{"scene_ko": f"칸 {i}", "shot_size": "wide"} for i in range(1, n + 1)]
            return sb.StoryRead(title_ko="들판의 하루", logline_ko="소녀와 고양이", shots=shots)
        if schema is StoryAsk:
            return StoryAsk(story=self.story, shots=9)
        return ChatOut(reply="일반 답이에요.", summary="")

    def data_for(self, schema: type) -> dict[str, Any]:
        return next(u for s, _, u in self.calls if s is schema) or {}

    def prompt_for(self, schema: type) -> str:
        return next(p for s, p, _ in self.calls if s is schema)


class FailingLLM:
    async def structured(self, *a: Any, **k: Any):
        raise LLMResponseError("시간 초과")


@pytest.fixture(autouse=True)
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[cr.CreativeRecords]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    s = cr.CreativeRecords(":memory:")
    cr.set_store(s)
    yield s
    cr.set_store(None)


def _request(skill: str, input: dict, untrusted: dict | None = None) -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id="ctx_test_script",
            token="t",
            idempotency_key="k1",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
        ),
        skill=skill,
        input=input,
        untrusted=untrusted or {},
    )


def _call(skill: str, input: dict, untrusted: dict | None = None):
    return asyncio.run(handlers.HANDLERS[skill](_request(skill, input, untrusted)))


def _use(monkeypatch: pytest.MonkeyPatch, llm: Any) -> None:
    monkeypatch.setattr(handlers, "SCRIPT_LLM", lambda: llm)
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: llm)


# ---------------------------------------------------------------- 대본 쓰기
def test_llm_writes_a_script_with_times_split_by_code(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    llm = FakeLLM()
    _use(monkeypatch, llm)
    res = _call("creative_script", {"record_id": rid, "scenes": 6, "seconds": 30}, {"story": STORY})
    assert res.status == TaskStatus.COMPLETED
    script = res.output["script"]
    assert script["source"] == "llm" and script["title_ko"] == "들판의 하루"
    assert [s["no"] for s in script["scenes"]] == [1, 2, 3, 4, 5, 6]
    assert script["scenes"][0]["start_s"] == 0 and script["scenes"][-1]["end_s"] == 30
    pairs = zip(script["scenes"], script["scenes"][1:], strict=False)
    assert all(a["end_s"] == b["start_s"] for a, b in pairs)
    assert script["cta_ko"] == "지금 만나 보세요"
    # 줄거리·캐릭터는 데이터 블록(untrusted)으로만
    data = llm.data_for(cs.ScriptRead)
    assert data["story"] == STORY and data["character"]["look_en"] == SHEET["consistency_en"]
    assert STORY not in llm.prompt_for(cs.ScriptRead)
    rec = store.get(rid)
    assert rec and rec["script"]["source"] == "llm" and rec["story"] == STORY


def test_numbers_not_in_the_story_are_dropped_from_the_script(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    scenes = [
        _scene(1, line_ko="고양이 3마리가 와요. 오늘만 50% 할인해요!", caption_ko="12,000원"),
        _scene(2, caption_ko="고양이 3마리"),
    ]
    _use(monkeypatch, FakeLLM(scenes=scenes))
    res = _call("creative_script", {"record_id": rid, "scenes": 6}, {"story": STORY})
    script = res.output["script"]
    first, second = script["scenes"][0], script["scenes"][1]
    assert first["line_ko"] == "고양이 3마리가 와요."  # 3은 줄거리에 있다
    assert first["caption_ko"] == ""  # 12,000원은 줄거리에 없다
    assert second["caption_ko"] == "고양이 3마리"
    assert "오늘만 50% 할인해요!" in script["dropped_ko"] and "12,000원" in script["dropped_ko"]
    assert len(script["scenes"]) == 2  # 모자란 장면을 지어내지 않는다


def test_scrub_treats_comma_numbers_as_the_same_number() -> None:
    kept, dropped = cs.scrub("가격은 12,000원이에요. 좋아요.", cs.numbers_in("12000원짜리"))
    assert kept == "가격은 12,000원이에요. 좋아요." and dropped == []


def test_without_llm_a_rule_script_uses_only_the_story(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    _use(monkeypatch, None)
    res = _call("creative_script", {"record_id": rid, "scenes": 6, "seconds": 15}, {"story": STORY})
    assert res.status == TaskStatus.COMPLETED
    script = res.output["script"]
    assert script["source"] == "rule"
    lines = [s["line_ko"] for s in script["scenes"]]
    assert lines[0] == "가을 들판에서 소녀가 고양이 3마리를 만난다."
    assert all(line in STORY for line in lines)  # 줄거리 글만 쓴다
    assert all(s["caption_ko"] == "" for s in script["scenes"]) and script["cta_ko"] == ""
    assert script["scenes"][-1]["end_s"] == 15


def test_llm_failure_falls_back_to_the_rule_script() -> None:
    out = asyncio.run(cs.write_script(STORY, 6, 30, SHEET, lambda: FailingLLM()))
    assert out.source == "rule" and len(out.scenes) >= 3


def test_rule_script_groups_sentences_when_there_are_more_than_scenes() -> None:
    story = "하나. 둘. 셋. 넷. 다섯. 여섯."
    out = cs.rule_script(story, 3, 30)
    assert [s.line_ko for s in out.scenes] == ["하나. 둘.", "셋. 넷.", "다섯. 여섯."]


def test_story_falls_back_to_the_record_story_and_refuses_without_any(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    _use(monkeypatch, FakeLLM())
    res = _call("creative_script", {"record_id": rid})
    assert res.status == TaskStatus.FAILED and res.outcome == "no_story"
    store.attach_storyboard(rid, "기록에 남은 줄거리", 6, {"title_ko": "t", "shots": []})
    res = _call("creative_script", {"record_id": rid})
    assert res.status == TaskStatus.COMPLETED


def test_unknown_record_is_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    _use(monkeypatch, FakeLLM())
    res = _call("creative_script", {"record_id": "CR-0123456789ab"}, {"story": STORY})
    assert res.outcome == "not_found"
    save = {"record_id": "CR-0123456789ab", "title_ko": "t", "scenes": [_saved(1)]}
    assert _call("creative_script_save", save).outcome == "not_found"


# ---------------------------------------------------------------- 사람이 고친 값
def _saved(no: int, **over: Any) -> dict[str, Any]:
    base = {"no": no, "start_s": (no - 1) * 5, "end_s": no * 5, "visual_ko": f"고친 화면 {no}"}
    base.update(over)
    return base


def test_human_edits_are_saved_as_is_and_renumbered(store: cr.CreativeRecords) -> None:
    rid = store.add_sheet(SHEET, None, None)
    body = {
        "record_id": rid,
        "title_ko": "고친 제목",
        "cta_ko": "오늘 19,900원",  # 사람이 넣은 숫자는 사람이 책임진다(빼지 않는다)
        "scenes": [_saved(7, line_ko="안녕"), _saved(9, start_s=8, end_s=4)],
        "who": "taeyoon",
    }
    res = _call("creative_script_save", body)
    assert res.status == TaskStatus.COMPLETED
    script = store.get(rid)["script"]
    assert script["source"] == "human" and script["edited_by"] == "taeyoon"
    assert [s["no"] for s in script["scenes"]] == [1, 2]
    assert script["scenes"][1]["end_s"] == 8.5  # 끝이 시작보다 앞서면 맞춘다
    assert script["cta_ko"] == "오늘 19,900원" and script["dropped_ko"] == []
    summary = store.list()[0]
    assert summary["script_scenes"] == 2 and summary["script_at"]


# ---------------------------------------------------------------- 스토리보드가 대본을 따른다
def test_storyboard_follows_the_script_scenes(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    llm = FakeLLM()
    _use(monkeypatch, llm)
    _call("creative_script", {"record_id": rid, "scenes": 6}, {"story": STORY})
    res = _call("storyboard_prompt", {}, {"story": STORY, "shots": 12, "record_id": rid})
    assert res.status == TaskStatus.COMPLETED
    out = res.output
    assert out["script_used"] is True and out["cta_ko"] == "지금 만나 보세요"
    assert len(out["shots"]) == 6  # 칸 수 = 대본 장면 수(요청 12가 아니라)
    first = out["shots"][0]
    assert first["line_ko"] == "대사 가예요." and first["caption_ko"] == "자막가"
    assert first["speaker_ko"] == "소녀" and first["start_s"] == 0
    data = llm.data_for(sb.StoryRead)
    assert data["shots"] == 6 and [s["visual_ko"] for s in data["script"]][:2] == [
        "장면 가 화면",
        "장면 나 화면",
    ]
    assert "대본(script)" in llm.prompt_for(sb.StoryRead)
    assert store.get(rid)["storyboard"]["script_used"] is True


def test_storyboard_follows_human_edited_script(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    _use(monkeypatch, FakeLLM())
    scenes = [_saved(i, caption_ko=f"고친 자막 {i}") for i in (1, 2, 3)]
    _call("creative_script_save", {"record_id": rid, "title_ko": "t", "scenes": scenes})
    res = _call("storyboard_prompt", {}, {"story": STORY, "record_id": rid})
    assert [s["caption_ko"] for s in res.output["shots"]] == [
        "고친 자막 1",
        "고친 자막 2",
        "고친 자막 3",
    ]


def test_rule_script_does_not_decide_the_panel_count(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    monkeypatch.setattr(handlers, "SCRIPT_LLM", lambda: None)
    _call("creative_script", {"record_id": rid}, {"story": "한 문장 줄거리"})
    monkeypatch.setattr(handlers, "STORYBOARD_LLM", lambda: FakeLLM())
    res = _call("storyboard_prompt", {}, {"story": "한 문장 줄거리", "shots": 9, "record_id": rid})
    assert len(res.output["shots"]) == 9 and res.output["script_used"] is False


def test_old_record_without_script_still_works(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    _use(monkeypatch, FakeLLM())
    res = _call("storyboard_prompt", {}, {"story": STORY, "shots": 9, "record_id": rid})
    assert len(res.output["shots"]) == 9 and res.output["script_used"] is False
    assert res.output["shots"][0]["line_ko"] == "" and res.output["shots"][0]["start_s"] is None
    rec = store.get(rid)
    assert rec["script"] is None
    assert CreativeRecordGetOutput.model_validate(rec).script is None
    assert store.list()[0]["script_scenes"] == 0


def test_old_database_without_script_column_is_migrated(tmp_path: Any) -> None:
    import sqlite3

    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE marketing_creative_records (record_id TEXT PRIMARY KEY, created_at TEXT"
        " NOT NULL, note TEXT, story TEXT, shots INTEGER, thumbnail TEXT, sheet TEXT NOT NULL,"
        " storyboard TEXT, storyboard_at TEXT)"
    )
    conn.execute(
        "INSERT INTO marketing_creative_records (record_id, created_at, sheet)"
        " VALUES ('CR-0123456789ab', '2026-11-20T00:00:00+00:00', '{}')"
    )
    conn.commit()
    conn.close()
    s = cr.CreativeRecords(path)
    assert s.get("CR-0123456789ab")["script"] is None
    assert s.attach_script("CR-0123456789ab", {"title_ko": "t"}, None)


# ---------------------------------------------------------------- 영상 프롬프트
BASE = (
    "Animate this storyboard as one continuous short film, panel by panel in order, "
    "keeping the same characters, outfits and art style."
)


def test_video_prompt_carries_the_script_flow(
    store: cr.CreativeRecords, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    _use(monkeypatch, FakeLLM())
    _call("creative_script", {"record_id": rid, "scenes": 6}, {"story": STORY})
    _call("storyboard_prompt", {}, {"story": STORY, "record_id": rid})
    prompt = rr._video_prompt(store.get(rid))
    assert prompt.startswith(BASE)
    # 11/25: 자막·CTA는 프롬프트에 넣지 않고(코드가 얹는다), 대사는 따옴표 없이 장면 내용으로만
    assert "Scene 1 (0-5s): 칸 1 / the action conveys: 대사 가예요.." in prompt
    assert "Scene 2 (5-10s): 칸 2 / the action conveys: 대사 나예요.." in prompt
    assert "자막가" not in prompt and "지금 만나 보세요" not in prompt and '"' not in prompt
    assert prompt.endswith(rr.NO_TEXT)
    params = rr._video_mcp_params(store.get(rid), 10)
    assert params["prompt"] == prompt


def test_video_prompt_stays_within_the_limit_and_keeps_the_no_text_line() -> None:
    shots = [
        {
            "no": i,
            "start_s": i * 5,
            "end_s": i * 5 + 5,
            "scene_ko": "긴 장면 " * 30,
            "line_ko": "긴 대사 " * 40,
            "caption_ko": "자막" * 20,
        }
        for i in range(1, 13)
    ]
    prompt = rr.storyboard_video_prompt("soft anime " * 15, shots)
    assert len(prompt) <= rr.VIDEO_PROMPT_MAX
    assert "Scene 12 (60-65s)" in prompt
    assert prompt.endswith(rr.NO_TEXT)


def test_video_prompt_without_script_is_unchanged() -> None:
    assert rr.storyboard_video_prompt("flat 2D") == f"{BASE} flat 2D"
    board = {"style_en": "flat 2D", "shots": [{"no": 1, "scene_ko": "x"}], "script_used": False}
    assert rr._video_prompt({"storyboard": board}) == f"{BASE} flat 2D"
    assert rr.storyboard_video_prompt(None, [{"no": 1, "start_s": 0.25, "end_s": 2.25}]) == (
        f"{BASE} Follow this script panel by panel: Scene 1 (0.3-2.3s): . {rr.NO_TEXT}"
    )


# ---------------------------------------------------------------- 대화방
def test_chat_image_and_story_make_sheet_script_and_storyboard(
    store: cr.CreativeRecords,
) -> None:
    rid = store.add_sheet(SHEET, None, None)
    llm = FakeLLM(story="가을 들판에서 소녀가 고양이를 만난다")
    svc = ChatService(ChatStore(":memory:"), handlers.STATE.chat_facts, lambda: llm)

    async def go() -> dict:
        th = svc.send("gaon", None, "이 캐릭터로 스토리보드 만들어 줘", rid)
        await svc.wait_idle()
        return svc.get("gaon", th["id"])

    ai = asyncio.run(go())["messages"][-1]
    assert ai["card"] == {
        "kind": "creative",
        "record_id": rid,
        "role": "result",
        "has_storyboard": True,
        "has_script": True,
    }
    assert "대본 9장면과 스토리보드 9칸" in ai["text"]
    rec = store.get(rid)
    assert rec["script"]["source"] == "llm" and len(rec["script"]["scenes"]) == 9
    assert rec["storyboard"]["script_used"] is True and len(rec["storyboard"]["shots"]) == 9
    assert rec["storyboard"]["shots"][2]["caption_ko"] == "자막다"
