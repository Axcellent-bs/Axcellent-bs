"""KeyPose.posture (11/26): 앉은 자세. 추가만, 기본값 stand라 예전 포즈 그대로 읽힌다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.planning import KeyPose, VideoScene


def test_old_pose_without_posture_reads_as_stand() -> None:
    pose = KeyPose.model_validate({"shot": "full", "x": 0.2, "yaw": 30, "prop": "phone"})
    assert pose.posture == "stand"
    assert KeyPose().posture == "stand"


@pytest.mark.parametrize("posture", ["stand", "sit", "kneel", "lie"])
def test_posture_values_round_trip(posture: str) -> None:
    pose = KeyPose.model_validate({"posture": posture})
    assert pose.posture == posture
    assert KeyPose.model_validate(pose.model_dump(mode="json")).posture == posture


def test_unknown_posture_rejected() -> None:
    with pytest.raises(ValidationError):
        KeyPose.model_validate({"posture": "jump"})


def test_old_scene_with_pose_still_parses() -> None:
    scene = VideoScene.model_validate({
        "n": 1, "start_s": 0, "end_s": 5, "visual": "소파에 앉은 주인공",
        "prompt": "a person on a sofa",
        "start_pose": {"shot": "medium", "arm_r": {"raise": 40}},
    })
    assert scene.start_pose is not None and scene.start_pose.posture == "stand"
    assert scene.end_pose is None
