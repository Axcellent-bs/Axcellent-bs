"""마케팅 SNS 게시·이미지·영상 생성 등급 (#487, #677)."""

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
    return {
        "post_draft_id": "pd_1",
        "media_kind": "video",
        "duration_s": 8,
        **over,
    }


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
    for n in range(3):
        held = await gate.execute(
            act("publish_sns", "marketing", f"s{n}", **_sns(today_post_count=0))
        )
        assert _rule_keys(gate, held.review_id) == {"base_grade"}
        await gate.decide_review(held.review_id or "", "approved", "taeyoon", None)

    # 보류·거절 건은 세지 않는다
    rejected = await gate.execute(act("publish_sns", "marketing", "r1", **_sns()))
    await gate.decide_review(rejected.review_id or "", "rejected", "taeyoon", None)

    fourth = await gate.execute(act("publish_sns", "marketing", "s5", **_sns(today_post_count=0)))
    assert _rule_keys(gate, fourth.review_id) == {"sns_daily_posts"}

    clock.advance(24 * 3600)  # 다음 날(UTC)에는 다시 센다
    next_day = await gate.execute(act("publish_sns", "marketing", "s6", **_sns()))
    assert _rule_keys(gate, next_day.review_id) == {"base_grade"}


@pytest.mark.asyncio
async def test_video_within_limits_passes_on_rules_without_a_verifier_agent(
    gate: Gate, verifier: Any, act
) -> None:
    decision = await gate.execute(act("generate_media", "marketing", "v1", **_video()))
    assert (decision.grade, decision.status, decision.verifier) == ("B", "executed", "safety")
    assert decision.computed_amount is None  # 원화 금액은 보지 않는다(예산은 마케팅 코드가 건다)
    assert verifier.tasks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("over", "key"),
    [
        ({"duration_s": 91}, "video_duration"),
        ({"duration_s": None}, "video_duration"),
    ],
)
async def test_video_over_a_limit_is_held(gate: Gate, act, over: dict[str, Any], key: str) -> None:
    held = await gate.execute(act("generate_media", "marketing", "v1", **_video(**over)))
    assert (held.grade, held.status, held.hold_reason) == (
        "C",
        "held",
        HoldReason.OVER_THRESHOLD,
    )
    assert _rule_keys(gate, held.review_id) == {key}


@pytest.mark.asyncio
async def test_video_has_no_money_rule_even_if_a_cost_is_sent(gate: Gate, act) -> None:
    """원화 건당·하루 합계·0원 규칙은 없다. 예전 호출처럼 cost_krw를 보내도 보지 않는다."""
    for n, cost in enumerate((0, 1900, 2000, 1_000_000)):
        ok = await gate.execute(
            act("generate_media", "marketing", f"v{n}", **_video(cost_krw=cost))
        )
        assert (ok.grade, ok.status) == ("B", "executed")
        assert ok.computed_amount is None
    claim = await gate.execute(
        act("generate_media", "marketing", "v9", **_video(claimed_cost_krw=900))
    )
    assert claim.status == "executed"  # 요청 쪽이 적은 금액과 대조하지 않는다


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


def _image(**over: Any) -> dict[str, Any]:
    return {"post_draft_id": "pd_1", "media_kind": "image", **over}


@pytest.mark.asyncio
async def test_image_has_no_length_and_no_cost_limit(gate: Gate, verifier: Any, act) -> None:
    ok = await gate.execute(act("generate_media", "marketing", "i1", **_image()))
    assert (ok.grade, ok.status, ok.verifier) == ("B", "executed", "safety")
    assert verifier.tasks == []
    # 예전 금액 기준(400원·0원)은 없다
    for n, cost in enumerate((0, 400, 100_000)):
        again = await gate.execute(
            act("generate_media", "marketing", f"i{n + 2}", **_image(cost_krw=cost))
        )
        assert (again.grade, again.status) == ("B", "executed")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [None, "music"])
async def test_unknown_media_kind_is_held(gate: Gate, act, kind: str | None) -> None:
    held = await gate.execute(act("generate_media", "marketing", "m1", **_image(media_kind=kind)))
    assert held.status == "held"
    assert "media_kind_known" in _rule_keys(gate, held.review_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["marketing_chat_send", "marketing_chat_close"])
async def test_marketing_chat_is_a_and_only_marketing_asks(gate: Gate, act, action: str) -> None:
    ok = await gate.execute(act(action, "marketing", "c1", thread_id="t1"))
    assert (ok.grade, ok.status) == ("A", "executed")
    other = await gate.execute(act(action, "support", "c2", thread_id="t1"))
    assert other.hold_reason == HoldReason.UNREGISTERED_ACTION


@pytest.mark.asyncio
async def test_bgm_audio_is_b_without_a_cost_limit(gate: Gate, act) -> None:
    """영상에 입히는 AI 연주곡 BGM(audio)도 B로 실행된다. 곡당 금액 기준은 없다."""
    for n in range(3):
        ok = await gate.execute(
            act("generate_media", "marketing", f"a{n}", **_image(media_kind="audio"))
        )
        assert (ok.grade, ok.status) == ("B", "executed")


@pytest.mark.asyncio
async def test_video_up_to_the_90s_policy_limit_is_not_held_for_duration(
    gate: Gate, verifier: Any, act
) -> None:
    ok = await gate.execute(
        act("generate_media", "marketing", "v90", **_video(duration_s=90, clip_count=7))
    )
    assert (ok.status, ok.verifier) == ("executed", "safety")
