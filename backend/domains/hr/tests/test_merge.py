"""중복 결정 병합: 확신이 매우 높으면 AI가 자동으로, 애매하면 사람이 승인, 되돌릴 수 있다 (15번)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import (
    AgentRequest,
    ListDuplicateGroupsOutput,
    RestoreMergedCardOutput,
)
from hr_agent.cards.link import AUTO_MERGE_COSINE, DENSE_SAME_TOPIC
from hr_agent.cards.merge import decide_duplicate_group, list_duplicate_groups, restore_merged_card
from hr_agent.cards.service import record_case

OLD_TEXT = "배포는 평일 오전 10시부터 오후 4시 사이에만 진행한다"
NEW_TEXT = "서비스 배포 시간은 평일 10시부터 16시까지로 한정한다"
HIGH = AUTO_MERGE_COSINE + 0.02
MID = (DENSE_SAME_TOPIC + AUTO_MERGE_COSINE) / 2


def req(skill: str, inp: dict, ctx: str, key: str = "k", token: str = "mock:orchestrator"):
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": ctx,
                "token": token,
                "deadline": datetime.now(UTC) + timedelta(seconds=30),
                "idempotency_key": f"{ctx}:{skill}:{key}",
            },
            "skill": skill,
            "input": inp,
        }
    )


class ScriptDense:
    """옛 카드의 결정 문장으로 코사인을 정해 둔 가짜 Dense."""

    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    async def search(self, query, cards, k=5, min_cosine=0.0):
        found = [(c, self.scores.get(c.extra.get("decision"), 0.0)) for c in cards]
        return [(c, s) for c, s in found if s >= min_cosine]


class ScriptJudge:
    def __init__(self, verdict) -> None:
        self.verdict = verdict
        self.calls = 0

    async def judge(self, new_text, old_text):
        self.calls += 1
        return self.verdict


class ScriptTitle:
    async def suggest(self, members):
        return "평일 10~16시에만 배포한다"


async def decision(svc, ctx: str, text: str, area: str = "infra") -> str:
    body = {
        "context_id": ctx,
        "kind": "decision",
        "summary": text,
        "decision_reason": "합성 시험 결정",
        "alternatives_considered": ["그대로 유지"],
        "source_refs": [f"eval:{ctx}"],
        "author": "t",
        "area": area,
    }
    out = (await record_case(svc, req("record_case", body, ctx, "rc"))).output
    card = await svc.memory.get_card(out["card_id"])
    if card.extra.get("candidate"):
        await svc.memory.set_candidate(card.card_id, False)
    return card.card_id


def wire(svc, cosine: float, verdict) -> ScriptJudge:
    svc.dense = ScriptDense({OLD_TEXT: cosine})
    judge = ScriptJudge(verdict)
    svc.extras["same_judge"] = judge
    svc.extras["title_suggester"] = ScriptTitle()
    return judge


async def listing(svc) -> ListDuplicateGroupsOutput:
    res = await list_duplicate_groups(svc, req("list_duplicate_groups", {}, "ctx_list"))
    return ListDuplicateGroupsOutput.model_validate(res.output)


@pytest.mark.asyncio
async def test_a_very_confident_duplicate_is_merged_automatically_and_can_be_restored(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    wire(svc, HIGH, "same")
    new = await decision(svc, "ctx_new", NEW_TEXT)
    merged = await svc.memory.get_card(old)
    assert merged.status == "superseded" and merged.superseded_by == new
    assert merged.extra["merge"]["how"] == "auto" and merged.extra["merge"]["into"] == new
    shown = await listing(svc)
    assert [m.card_id for m in shown.merged] == [old] and shown.merged[0].how == "auto"

    res = await restore_merged_card(svc, req("restore_merged_card", {"card_id": old}, "ctx_r"))
    assert RestoreMergedCardOutput.model_validate(res.output).outcome == "restored"
    back = await svc.memory.get_card(old)
    assert back.status == "active" and "merge" not in back.extra and "merge_restored" in back.extra
    again = await restore_merged_card(
        svc, req("restore_merged_card", {"card_id": old}, "ctx_r", "2")
    )
    assert again.output["outcome"] == "not_merged"


@pytest.mark.asyncio
async def test_without_the_llm_saying_same_nothing_is_merged_automatically(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    wire(svc, HIGH, None)  # 판정을 못 받은 경우
    await decision(svc, "ctx_new", NEW_TEXT)
    assert (await svc.memory.get_card(old)).status == "active"
    assert len((await listing(svc)).groups) == 1  # 사람이 본다


@pytest.mark.asyncio
async def test_an_uncertain_duplicate_waits_for_a_person_who_can_merge_it(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    wire(svc, MID, "same")
    new = await decision(svc, "ctx_new", NEW_TEXT)
    assert (await svc.memory.get_card(old)).status == "active"
    group = (await listing(svc)).groups[0]
    assert group.representative.card_id == new and [m.card_id for m in group.olders] == [old]
    assert group.suggested_title == "평일 10~16시에만 배포한다"

    done = await decide_duplicate_group(
        svc,
        req(
            "decide_duplicate_group",
            {
                "group_id": group.group_id,
                "representative_id": new,
                "decision": "merge",
                "title": "평일 10~16시에만 배포",
            },
            "ctx_h",
        ),
    )
    assert done.output["outcome"] == "merged" and done.output["merged_ids"] == [old]
    merged = await svc.memory.get_card(old)
    assert merged.status == "superseded" and merged.extra["merge"]["how"] == "human"
    assert (await svc.memory.get_card(new)).title == "[결정] 평일 10~16시에만 배포"
    assert (await listing(svc)).groups == []
    again = await decide_duplicate_group(
        svc,
        req(
            "decide_duplicate_group",
            {"group_id": group.group_id, "representative_id": new, "decision": "merge"},
            "ctx_h",
            "2",
        ),
    )
    assert again.output["outcome"] == "duplicate"


@pytest.mark.asyncio
async def test_a_person_can_leave_the_cards_as_they_are(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    wire(svc, MID, "same")
    new = await decision(svc, "ctx_new", NEW_TEXT)
    group = (await listing(svc)).groups[0]
    done = await decide_duplicate_group(
        svc,
        req(
            "decide_duplicate_group",
            {"group_id": group.group_id, "representative_id": new, "decision": "dismiss"},
            "ctx_h",
        ),
    )
    assert done.output["outcome"] == "dismissed"
    assert (await svc.memory.get_card(old)).status == "active"
    assert (await listing(svc)).groups == []
    assert old in (await svc.memory.get_card(new)).extra["dup_dismissed"]


@pytest.mark.asyncio
async def test_a_changed_decision_still_replaces_the_old_one_without_a_merge_record(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    wire(svc, HIGH, "changed")
    new = await decision(svc, "ctx_new", NEW_TEXT)
    replaced = await svc.memory.get_card(old)
    assert replaced.status == "superseded" and replaced.superseded_by == new
    assert "merge" not in replaced.extra
    shown = await listing(svc)
    assert shown.merged == [] and shown.groups == []


@pytest.mark.asyncio
async def test_a_separate_decision_is_left_alone(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    judge = wire(svc, HIGH, "separate")
    await decision(svc, "ctx_new", NEW_TEXT)
    assert (await svc.memory.get_card(old)).status == "active" and judge.calls == 1
    assert (await listing(svc)).groups == []


@pytest.mark.asyncio
async def test_below_the_topic_floor_nothing_happens_and_the_llm_is_not_called(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT)
    judge = wire(svc, DENSE_SAME_TOPIC - 0.05, "same")
    await decision(svc, "ctx_new", NEW_TEXT)
    assert (await svc.memory.get_card(old)).status == "active" and judge.calls == 0


@pytest.mark.asyncio
async def test_other_areas_are_never_merged(svc) -> None:
    old = await decision(svc, "ctx_old", OLD_TEXT, area="finance")
    wire(svc, HIGH, "same")
    await decision(svc, "ctx_new", NEW_TEXT, area="infra")
    assert (await svc.memory.get_card(old)).status == "active"


@pytest.mark.asyncio
async def test_state_changes_need_an_idempotency_key(svc) -> None:
    request = req("restore_merged_card", {"card_id": "card_x"}, "ctx_r")
    request.meta.idempotency_key = None
    result = await restore_merged_card(svc, request)
    assert result.status.value in ("failed", "rejected")
