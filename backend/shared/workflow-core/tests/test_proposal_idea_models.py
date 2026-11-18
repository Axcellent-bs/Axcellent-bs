"""기획 제안 방향 선택지 상태 칸 (11/18): 예전 상태 파일(칸 없음)도 그대로 읽는다."""

from __future__ import annotations

from ax_workflow.models import IdeaOption, Proposal, ScaleFacts, TrendRef

OLD = {
    "id": "PROP-1", "title": "t", "from": "data", "skill": "s", "insight": "i",
    "idea": "할 일", "why": "이유", "created_at": "2026-11-17T00:00:00Z",
}  # fmt: skip


def test_old_proposal_without_options_loads() -> None:
    p = Proposal.model_validate(OLD)
    assert p.options == [] and p.scale is None and p.angle is None


def test_proposal_keeps_options_scale_angle() -> None:
    opt = IdeaOption(
        id="ride_trend", label="유행에 올라타기", available=True,
        refs=[TrendRef(topic="독서 챌린지", median_views=50000.0)], recommended=True, reason="r",
    )  # fmt: skip
    p = Proposal.model_validate(
        {**OLD, "options": [opt.model_dump()], "scale": ScaleFacts().model_dump(), "angle": "ride_trend"}
    )
    assert p.options[0].refs[0].topic == "독서 챌린지" and p.angle == "ride_trend"
    assert p.scale is not None and p.scale.followers is None and p.scale.past_views_count == 0
