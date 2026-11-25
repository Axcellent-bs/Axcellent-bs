"""기획안 → 제작 기록 연결(`prepare_brief_creative`). Higgsfield는 가짜만 쓴다."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template import company_scope
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import brief_creative, handlers
from marketing_agent import creative_render as rr
from marketing_agent.briefs import BriefStore


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parent / file)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


B = _load("request_creative_helpers_brief", "test_request_creative.py")
env = B.env  # 임시 기록 DB·가짜 Higgsfield·가짜 안전 관문
PID = "PROP-34"
VIDEO_PLAN: dict[str, Any] = {
    "concept": "한 줄 기록 챌린지",
    "duration_s": 15,
    "script": "책을 덮고 한 줄을 적는다. 사진을 올린다.",
    "style": "soft watercolor",
    "negative": "no text, no logo",
    "scenes": [
        {
            "n": 1,
            "start_s": 0,
            "end_s": 7,
            "visual": "책을 덮는 손",
            "prompt": "a hand closes a book on a wooden desk",
        },
        {
            "n": 2,
            "start_s": 7,
            "end_s": 15,
            "visual": "한 줄을 적는 노트",
            "prompt": "a hand writes one line in a small notebook",
        },
    ],
}


def _briefs(video_plan: dict[str, Any] | None = VIDEO_PLAN) -> BriefStore:
    store = BriefStore(":memory:")
    brief = CampaignBriefInput(
        proposal_id=PID,
        title="한 줄 기록 챌린지",
        idea="읽고 한 줄 남기기",
        why="기록하는 독자가 늘었다",
        approved_by="태윤",
        video_plan=video_plan,
    )
    store.add(brief, datetime.now(UTC))
    return store


def _call(
    env: dict[str, Any], briefs: BriefStore, pid: str = PID, company: str | None = None
) -> Any:
    extra = {"tenant_id": company} if company else {}
    request = AgentRequest(
        meta=RequestMeta(
            context_id="ctx_creative_ui",
            token="t",
            idempotency_key="ui:creative:test",
            deadline=datetime.now(UTC) + timedelta(seconds=60),
            requester="dashboard-api",
            **extra,
        ),
        skill=brief_creative.SKILL,
        input={"proposal_id": pid},
    )

    async def go():  # noqa: ANN202
        with company_scope.using(company):
            out = await brief_creative.prepare_brief_creative(
                request, briefs=briefs, ledger=env["ledger"]
            )
        await rr.wait_idle()
        return out

    return asyncio.run(go())


def test_default_character_is_copied_and_the_plan_becomes_the_storyboard(
    env: dict[str, Any],
) -> None:
    brand = B._brand(env)
    out = _call(env, _briefs())
    assert out.status == TaskStatus.COMPLETED
    rid = out.output["record_id"]
    assert rid != brand
    assert out.output["character_record_id"] == brand
    assert out.output["reused"] is False
    rec = env["store"].get(rid)
    assert [s["scene_ko"] for s in rec["storyboard"]["shots"]] == [
        "책을 덮는 손",
        "한 줄을 적는 노트",
    ]
    assert rec["storyboard"][rr.PLAN_DURATION_KEY] == 15
    assert rec["render"]["sheet_image"]["status"] == "done"  # 시트 그림은 파일째 복사
    # 스토리보드 그림은 화면과 같은 규칙으로 관문(B)을 지나 시작한다. 영상은 만들지 않는다
    assert [c[0] for c in env["gate"].calls] == ["generate_media"]
    assert "video" not in rec["render"]


def test_pressing_twice_keeps_one_record_and_starts_no_second_image(
    env: dict[str, Any],
) -> None:
    B._brand(env)
    briefs = _briefs()
    first = _call(env, briefs)
    gates, submits = len(env["gate"].calls), len(env["hf"].submits)
    again = _call(env, briefs)
    assert again.output["record_id"] == first.output["record_id"]
    assert again.output["reused"] is True
    assert (len(env["gate"].calls), len(env["hf"].submits)) == (gates, submits)
    assert len(env["store"].list()) == 2  # 대표 캐릭터 + 만든 기록 하나


def test_changing_the_default_character_does_not_make_a_second_record(
    env: dict[str, Any],
) -> None:
    B._brand(env)
    briefs = _briefs()
    first = _call(env, briefs)
    B._brand(env, note="새 캐릭터")  # 기본 캐릭터가 바뀐다
    again = _call(env, briefs)
    assert again.output["record_id"] == first.output["record_id"]
    assert again.output["character_record_id"] == first.output["character_record_id"]


def test_without_a_brand_character_a_record_is_made_and_no_image_is_started(
    env: dict[str, Any],
) -> None:
    briefs = _briefs()
    out = _call(env, briefs)
    assert out.status == TaskStatus.COMPLETED
    assert out.output["character_record_id"] is None
    rec = env["store"].get(out.output["record_id"])
    assert len(rec["storyboard"]["shots"]) == 2
    assert rec["sheet"]["palette"] == [] and rec["sheet"]["panels"] == []
    assert rec["render"] == {}
    assert env["gate"].calls == [] and env["hf"].submits == []
    assert env["store"].list()[0]["summary_ko"] == "대표 캐릭터 없이 만드는 기획안 영상"
    again = _call(env, briefs)
    assert again.output["record_id"] == out.output["record_id"]
    assert again.output["reused"] is True


def test_a_removed_record_is_made_again(env: dict[str, Any]) -> None:
    briefs = _briefs()
    first = _call(env, briefs)
    store = env["store"]
    with store._lock, store._conn:  # noqa: SLF001 - 보관 한도로 지워진 상황
        store._conn.execute(  # noqa: SLF001
            "DELETE FROM marketing_creative_records WHERE record_id = ?",
            (first.output["record_id"],),
        )
    again = _call(env, briefs)
    assert again.output["reused"] is False
    assert store.get(again.output["record_id"])["storyboard"]


def test_missing_brief_or_plan_leaves_no_record(env: dict[str, Any]) -> None:
    assert _call(env, _briefs(), pid="PROP-99").outcome == "brief_not_found"
    assert _call(env, _briefs(video_plan=None)).outcome == "no_video_plan"
    scene = VIDEO_PLAN["scenes"][0] | {"prompt": "   ", "end_s": 15}  # 쓸 수 없는 짧은 장면
    assert _call(env, _briefs(VIDEO_PLAN | {"scenes": [scene]})).outcome == "no_scenes"
    assert env["store"].list() == []


def test_another_company_gets_its_own_record(env: dict[str, Any]) -> None:
    mine = _call(env, _briefs())
    with company_scope.using("othercorp"):
        theirs = _briefs()
    other = _call(env, theirs, company="othercorp")
    assert other.status == TaskStatus.COMPLETED
    assert other.output["reused"] is False
    assert other.output["record_id"] != mine.output["record_id"]
    assert env["store"].get(other.output["record_id"]) is None


def test_skill_is_registered_and_the_cards_allow_only_the_dashboard() -> None:
    assert brief_creative.SKILL in handlers.HANDLERS
    here = Path(__file__).resolve()
    for path in (
        here.parents[1] / "agent-card.json",
        here.parents[3] / "services" / "coupon" / "agent-card.json",
    ):
        skills = json.loads(path.read_text(encoding="utf-8"))["skills"]
        found = next(s for s in skills if s["id"] == brief_creative.SKILL)
        assert found["allowed_requesters"] == ["dashboard-api"]
        assert found["grade"] == "read"
