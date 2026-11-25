"""11/25 Kling 4.x 영상에 mode를 넣는다 (Higgsfield 호출 없음, params만 본다).

`mode`가 없으면 가상 인물 원본(image_references)·시작 그림(start_image)을 넣어도 모델 기본
text-to-video로 글만 보고 만들 수 있다.
"""

from __future__ import annotations

import pytest

from marketing_agent import creative_mcp as cm

MODES = ["kling_4_0_flash", "kling4_0"]


def _video(model: str) -> dict[str, object]:
    return cm.video_params(model, "a cat walks", "9:16", 5)


@pytest.mark.parametrize("model", MODES)
def test_image_references_make_reference_to_video(model: str) -> None:
    one = cm.with_media(_video(model), "m-1", cm.IMAGE_REF_ROLE)
    many = cm.with_medias(_video(model), ["m-1", "m-2"], cm.IMAGE_REF_ROLE)
    assert one["mode"] == many["mode"] == "reference-to-video"
    assert many["medias"] == [
        {"value": "m-1", "role": "image_references"},
        {"value": "m-2", "role": "image_references"},
    ]


@pytest.mark.parametrize("model", MODES)
def test_start_image_only_makes_image_to_video(model: str) -> None:
    got = cm.with_media(_video(model), "m-1", cm.VIDEO_START_ROLE)
    assert got["mode"] == "image-to-video"


@pytest.mark.parametrize("model", MODES)
def test_no_media_leaves_mode_out(model: str) -> None:
    assert "mode" not in _video(model)  # 모델 기본(text-to-video)
    assert cm.video_mode(model, []) is None


def test_kling3_turbo_and_unknown_models_get_no_mode() -> None:
    for model in ("kling3_0_turbo", "some_new_video_model", ""):
        for role in (cm.IMAGE_REF_ROLE, cm.VIDEO_START_ROLE):
            got = cm.with_media(_video(model), "m-1", role)
            assert "mode" not in got
            assert got == {**_video(model), "medias": [{"value": "m-1", "role": role}]}


def test_image_models_get_no_mode() -> None:
    params = cm.image_params("nano_banana_pro", "a cat", "", "1:1")
    got = cm.with_media(params, "m-1", cm.IMAGE_REF_ROLE)
    assert "mode" not in got


def test_kling4_0_capabilities() -> None:
    assert cm.takes_image_references("kling4_0") is True
    assert cm.video_max_seconds("kling4_0") == 30
    assert cm.video_max_seconds("kling_4_0_flash") == 20
    assert cm.takes_image_references("kling3_0_turbo") is False
