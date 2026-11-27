"""제작 기록 파일 붙이기 계약: 모션 참조 영상(motion.mp4)도 붙일 수 있고 모르는 이름은 막힌다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.marketing_creative import CreativeRecordAttachInput

BASE = {"record_id": "CR-0123456789ab", "sha256": "a" * 64, "size_bytes": 10}


def test_motion_video_attach_asset_is_accepted() -> None:
    data = CreativeRecordAttachInput(part="motion_video", asset="motion.mp4", **BASE)
    assert data.asset == "motion.mp4"


@pytest.mark.parametrize("asset", ["sheet.png", "board.jpg", "video.mp4", "final.mp4"])
def test_existing_attach_assets_still_pass(asset: str) -> None:
    CreativeRecordAttachInput(part="video", asset=asset, **BASE)


@pytest.mark.parametrize("asset", ["evil.mp4", "motion.exe", "../motion.mp4", "motion.mp4/x"])
def test_unknown_attach_asset_is_rejected(asset: str) -> None:
    with pytest.raises(ValidationError):
        CreativeRecordAttachInput(part="motion_video", asset=asset, **BASE)
