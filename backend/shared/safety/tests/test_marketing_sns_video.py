"""마케팅 SNS 게시·영상 생성 등급 (#487)."""

from __future__ import annotations

from typing import Any

import pytest

from ax_contracts.schemas import HoldReason
from ax_safety.gate import Gate


def _sns(**over: Any) -> dict[str, Any]:
    return {
        "platform": "instagram",
        "media_kind": "image",
        "post_draft_id": "pd_1",
        "ai_label_missing_count": 0,
        "text_mismatch_count": 0,
        "constraint_missing_count": 0,
        **over,
    }


def _video(**over: Any) -> dict[str, Any]:
    return {"post_draft_id": "pd_1", "duration_s": 8, "cost_krw": 1200, **over}


def _rule_keys(gate: Gate, review_id: str | None) -> set[str]:
    review = gate.store.get_review(review_id or "")
    assert review is not None
    return {hit["key"] for hit in review.report["rules"]}


@pytest.mark.asyncio
async def test_sns_post_always_waits_for_a_person_and_shows_summary(gate: Gate, act) -> None:
    summary = "SNS 게시 요청: 정상화 공지 ntc_0003 → instagram/image"
    held = await gate.execute(act("publish_sns", "marketing", "s1", **_sns(summary=summary)))
    assert (held.grade, held.status) == ("C", "held")
    assert held.hold_reason == HoldReason.OVER_THRESHOLD
    review = gate.store.get_review(held.review_id or "")
    assert review is not None
    assert review.report["summary"] == summary
    assert review.report["rules"] == [{"key": "base_grade", "value": "C"}]

    # 사람이 승인하면 같은 키로 다시 물었을 때 실행으로 나온다
    await gate.decide_review(held.review_id or "", "approved", "taeyoon", None)
    replay = await gate.execute(act("publish_sns", "marketing", "s1", **_sns(summary=summary)))
    assert replay.status == "executed"


@pytest.mark.asyncio
async def test_sns_only_marketing_can_ask(gate: Gate, act) -> None:
    decision = await gate.execute(act("publish_sns", "support", "s1", **_sns()))
    assert decision.hold_reason == HoldReason.UNREGISTERED_ACTION


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("over", "key"),
    [
        ({"ai_label_missing_count": 1}, "sns_ai_label_missing"),
        ({"text_mismatch_count": 2}, "sns_text_mismatch"),
        ({"constraint_missing_count": 1}, "sns_constraint_missing"),
        ({"ai_label_missing_count": None}, "sns_ai_label_missing"),  # 없으면 걸린 것으로 본다
        ({"text_mismatch_count": "0"}, "sns_text_mismatch"),
    ],
)
async def test_sns_report_names_the_failed_check(
    gate: Gate, act, over: dict[str, Any], key: str
) -> None:
    held = await gate.execute(act("publish_sns", "marketing", "s1", **_sns(**over)))
    assert held.status == "held"
    assert _rule_keys(gate, held.review_id) == {key}


@pytest.mark.asyncio
async def test_sns_daily_limit_counts_executions_not_request_values(
    gate: Gate, clock: Any, act
) -> None:
    for n in range(5):
        held = await gate.execute(
            act("publish_sns", "marketing", f"s{n}", **_sns(today_post_count=0))
        )
        assert _rule_keys(gate, held.review_id) == {"base_grade"}
        await gate.decide_review(held.review_id or "", "approved", "taeyoon", None)

    # 보류·거절 건은 세지 않는다
    rejected = await gate.execute(act("publish_sns", "marketing", "r1", **_sns()))
    await gate.decide_review(rejected.review_id or "", "rejected", "taeyoon", None)

    sixth = await gate.execute(act("publish_sns", "marketing", "s5", **_sns(today_post_count=0)))
    assert _rule_keys(gate, sixth.review_id) == {"sns_daily_posts"}

    clock.advance(24 * 3600)  # 다음 날(UTC)에는 다시 센다
    next_day = await gate.execute(act("publish_sns", "marketing", "s6", **_sns()))
    assert _rule_keys(gate, next_day.review_id) == {"base_grade"}


@pytest.mark.asyncio
async def test_video_within_limits_passes_on_rules_without_a_verifier_agent(
    gate: Gate, verifier: Any, act
) -> None:
    decision = await gate.execute(act("generate_video", "marketing", "v1", **_video()))
    assert (decision.grade, decision.status, decision.verifier) == ("B", "executed", "safety")
    assert decision.computed_amount == 1200
    assert verifier.tasks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("over", "key"),
    [
        ({"cost_krw": 3000}, "video_cost_single"),
        ({"cost_krw": 0}, "video_cost_zero"),
        ({"duration_s": 9}, "video_duration"),
        ({"duration_s": None}, "video_duration"),
    ],
)
async def test_video_over_a_limit_is_held(gate: Gate, act, over: dict[str, Any], key: str) -> None:
    held = await gate.execute(act("generate_video", "marketing", "v1", **_video(**over)))
    assert (held.grade, held.status, held.hold_reason) == (
        "C",
        "held",
        HoldReason.OVER_THRESHOLD,
    )
    assert _rule_keys(gate, held.review_id) == {key}


@pytest.mark.asyncio
async def test_video_daily_total_holds_the_clip_that_reaches_the_limit(gate: Gate, act) -> None:
    for n in range(6):
        ok = await gate.execute(
            act("generate_video", "marketing", f"v{n}", **_video(cost_krw=2900))
        )
        assert ok.status == "executed"
    held = await gate.execute(act("generate_video", "marketing", "v6", **_video(cost_krw=2900)))
    assert held.status == "held"
    assert _rule_keys(gate, held.review_id) == {"video_cost_daily"}


@pytest.mark.asyncio
async def test_video_cost_must_be_computed_and_match_the_claim(gate: Gate, act) -> None:
    missing = await gate.execute(
        act("generate_video", "marketing", "v1", post_draft_id="pd_1", duration_s=8)
    )
    assert (missing.status, missing.hold_reason) == ("held", HoldReason.SCHEMA_INVALID)
    mismatch = await gate.execute(
        act("generate_video", "marketing", "v2", **_video(claimed_cost_krw=900))
    )
    assert (mismatch.status, mismatch.hold_reason) == ("held", HoldReason.AMOUNT_MISMATCH)


@pytest.mark.asyncio
async def test_notice_on_sns_channel_is_held_and_other_channels_stay_a(gate: Gate, act) -> None:
    sns = await gate.execute(
        act("publish_notice", "marketing", "n1", draft_id="d1", channel_code="sns")
    )
    assert (sns.grade, sns.status) == ("C", "held")
    assert _rule_keys(gate, sns.review_id) == {"notice_sns_channel"}
    for n, channel in enumerate(("banner", "email")):
        ok = await gate.execute(
            act("publish_notice", "marketing", f"n{n + 2}", draft_id="d1", channel_code=channel)
        )
        assert (ok.grade, ok.status) == ("A", "executed")
