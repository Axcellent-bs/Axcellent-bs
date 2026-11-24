"""기획 영상 제작안 → 실제 영상 프롬프트 (11/22, 고양이 책방 고정 없애기).

11/20 서버 영상: prepare_video_draft_from_plan이 video_plan의 장면 프롬프트를 쓰지 않고
cat_bookstore_video 고정 문장(고양이가 책방에서 책을 꺼냄)만 써서, 아이돌 의뢰도 같은 영상이 됐다.
같은 제안을 다시 쓴 의뢰는 초안 id가 내용만으로 정해져 영상이 예전 의뢰 밑에 붙었다.
실제 영상 생성 호출은 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest
from marketing_agent import handlers, request_video, sns, sns_media, video_draft
from marketing_agent.media.prompts import (
    PLAN_SCENE_FIELDS,
    VIDEO_PROMPT_MAX,
    render_prompt_detailed,
)

SCENES = [
    "Glowing light sticks sway around a cozy reading nook, slow dolly in",
    "Photo cards are arranged on a desk beside an open notebook, soft stage lights",
    "Fans share a decorated merch style book bag under warm stage lights",
    "Everyone waves goodbye as stage lights fade, warm ending",
]
PLAN = {
    "concept": "응원봉 불빛 아래 책 읽기",
    "duration_s": 15,
    "aspect": "9:16",
    "script": "응원봉을 든 팬이 책을 펼친다. 마지막에 함께 인사한다.",
    "scenes": [
        {"n": i, "start_s": (i - 1) * 4, "end_s": i * 4, "visual": "화면", "caption": "",
         "prompt": p}
        for i, p in enumerate(SCENES, start=1)
    ],
    "style": "Short vertical clip",
    "negative": "No text",
    "written_by": "llm",
}  # fmt: skip
ALL_PLAN = {"plan_character", "plan_setting", "plan_concept", *PLAN_SCENE_FIELDS}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(root))
    for name in ("AX_TENANT", "AX_TENANTS_DIR", "AX_COMPANY_CONNECTION_FILE"):
        monkeypatch.delenv(name, raising=False)
    return root


def _request(ctx: str = "ctx_wr_idol0001", **input_) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": ctx,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "prepare_video_draft_from_plan",
            "input": input_,
        }
    )


def _prepare(plan: dict, ctx: str = "ctx_wr_idol0001", store: sns.SnsStore | None = None):
    result = video_draft.prepare_video_draft(
        _request(ctx, proposal_id="PROP-9", video_plan=plan), store or handlers.STATE.sns
    )
    assert result.output is not None, result
    return result


def _with_scenes(*prompts: str) -> dict:
    scenes = [
        {"n": i, "start_s": i - 1, "end_s": i, "visual": "화면", "caption": "", "prompt": p}
        for i, p in enumerate(prompts, start=1)
    ]
    return PLAN | {"scenes": scenes}


def test_plan_story_video_is_chosen_with_scene_and_brand_fields() -> None:
    out = _prepare(PLAN).output
    assert out["scene_template_id"] == "plan_story_video"
    assert set(out["fields"]) == ALL_PLAN
    assert out["generate_media_input"]["fields"] == out["fields"]
    assert out["generate_media_input"]["scene_template_id"] == "plan_story_video"
    assert out["fields"]["plan_scene_1"] == SCENES[0] and out["fields"]["plan_scene_4"] == SCENES[3]
    assert out["field_sources"]["plan_character"] == "brand"
    assert out["field_sources"]["plan_scene_2"] == "video_plan"
    assert out["rejected_plan_fields"] == [] and out["template_fallback"] is None
    prompt = render_prompt_detailed("plan_story_video", out["fields"]).prompt
    assert "light sticks" in prompt and "cat mascot" not in prompt and "bookstore" not in prompt


def test_catbooks_brand_uses_defaults_and_nubi_has_none(monkeypatch: pytest.MonkeyPatch) -> None:
    # 11/24 캣북스 [brand]에서 고양이 마스코트·책방을 뺐다: 캐릭터·배경은 기본값
    monkeypatch.setenv("AX_TENANT", "catbooks")
    cat = _prepare(PLAN).output["fields"]
    assert cat["plan_character"] == "original illustrated characters, not real people"
    assert cat["plan_setting"] == "a setting that fits the company's products"
    cat_prompt = render_prompt_detailed("plan_story_video", cat).prompt.lower()
    assert "cat mascot" not in cat_prompt and "bookstore" not in cat_prompt
    monkeypatch.setenv("AX_TENANT", "nubi")
    nubi = _prepare(PLAN, ctx="ctx_wr_nubi0001").output["fields"]
    assert nubi["plan_setting"] == "scenic overseas travel destinations"
    prompt = render_prompt_detailed("plan_story_video", nubi).prompt.lower()
    assert "cat" not in prompt.split() and "bookstore" not in prompt


def test_scene_with_real_person_or_brand_is_dropped_alone() -> None:
    plan = _with_scenes(
        SCENES[0],
        "A famous actor reads on stage",  # 실존 인물
        "A Starbucks cup next to a book",  # 실제 브랜드
        SCENES[3],
    )
    body = _prepare(plan)
    out = body.output
    assert out["scene_template_id"] == "plan_story_video"
    assert out["rejected_plan_fields"] == ["plan_scene_2", "plan_scene_3"]  # 이름만
    assert "plan_scene_2" not in out["fields"] and "plan_scene_3" not in out["fields"]
    assert out["fields"]["plan_scene_4"] == SCENES[3]
    dumped = json.dumps(body.model_dump(mode="json"), ensure_ascii=False)
    assert "Starbucks" not in dumped and "famous actor" not in dumped  # 값은 남기지 않는다


def test_all_scenes_rejected_falls_back_to_old_template() -> None:
    plan = _with_scenes("A celebrity waves", "The cat holds a sign that says hello")
    missing = _prepare(plan).output  # 예전 템플릿 필드(분류)를 못 정하면 지금처럼 거절 + 이유
    assert missing["missing_fields"] == ["category"]
    assert missing["template_fallback"] == "plan_fields_rejected"
    out = _prepare(plan | {"media_hints": {"category": "에세이"}}).output
    assert out["scene_template_id"] == "cat_bookstore_video"
    assert out["template_fallback"] == "plan_fields_rejected"
    assert {"plan_scene_1", "plan_scene_2"} <= set(out["rejected_plan_fields"])
    assert not any(k.startswith("plan_") for k in out["fields"])  # 기획 내용을 지어내지 않는다


def test_rendered_prompt_keeps_no_text_rule_no_digits_and_length_limit() -> None:
    out = _prepare(PLAN).output
    prompt = render_prompt_detailed("plan_story_video", out["fields"]).prompt
    assert "No text, no letters, no numbers, no logos" in prompt
    assert re.search(r"\d", prompt) is None and len(prompt) <= VIDEO_PROMPT_MAX


def test_too_long_prompt_drops_scenes_from_the_back_in_fixed_order() -> None:
    long = " ".join(["gentle lantern light over quiet harbor streets"] * 4)  # 160자 넘음 → 잘림
    fields = {
        "plan_character": "original illustrated travelers not real people " * 2,
        "plan_setting": "scenic overseas travel destinations with mountains " * 2,
        "plan_concept": "a calm journey story " * 10,
        **{name: long for name in PLAN_SCENE_FIELDS},
    }
    rendered = render_prompt_detailed("plan_story_video", fields)
    dropped = [s["field"] for s in rendered.plan_skipped if s["reason"] == "too_long"]
    assert dropped and dropped == list(reversed(PLAN_SCENE_FIELDS))[: len(dropped)]  # 4 → 3 → …
    used = [u["field"] for u in rendered.plan_used]
    assert "plan_scene_1" in used and len(rendered.prompt) <= VIDEO_PROMPT_MAX
    # 다시 그리면 같은 결과(계산 순서 고정)
    assert render_prompt_detailed("plan_story_video", fields) == rendered


def test_same_proposal_in_two_requests_gets_own_draft_and_video(_env: Path) -> None:
    store = sns.SnsStore(":memory:")
    ids = {}
    for ctx in ("ctx_wr_aaaa0001", "ctx_wr_bbbb0002"):
        ids[ctx] = _prepare(PLAN, ctx=ctx, store=store).output["post_draft_id"]
        folder = _env / "shop-demo" / ctx / "med_1"
        folder.mkdir(parents=True)
        (folder / "meta.json").write_text(
            json.dumps({"media_id": f"med_{ctx[-4:]}", "media_kind": "video",
                        "post_draft_id": ids[ctx], "duration_s": 15,
                        "generated_at": "2026-11-22T01:00:00+00:00"}),
            encoding="utf-8",
        )  # fmt: skip
    assert ids["ctx_wr_aaaa0001"] != ids["ctx_wr_bbbb0002"]
    # 같은 의뢰로 다시 준비하면 같은 id (멱등)
    assert _prepare(PLAN, ctx="ctx_wr_aaaa0001", store=store).output["post_draft_id"] == ids[
        "ctx_wr_aaaa0001"
    ]
    for ctx, draft_id in ids.items():
        listed = asyncio.run(
            request_video.list_request_videos(
                AgentRequest.model_validate(
                    {
                        "meta": _request(ctx).meta.model_dump(mode="json"),
                        "skill": "list_request_videos",
                        "input": {"request_id": ctx.removeprefix("ctx_")},
                    }
                ),
                store,
            )
        ).output["items"]
        assert [i["post_draft_id"] for i in listed] == [draft_id]
        assert listed[0]["request_id"] == ctx.removeprefix("ctx_")
