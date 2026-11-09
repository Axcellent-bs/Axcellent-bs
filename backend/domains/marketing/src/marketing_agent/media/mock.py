"""목 어댑터. 가짜 asset 파일과 고정 비용만 돌려준다. 외부 호출·키가 없다."""

from __future__ import annotations

import hashlib
import struct
import zlib
from datetime import UTC, datetime

from marketing_agent.media.base import MediaRequest, MediaResult

NAME = "mock"
MODEL_ID = "mock-media"
# 고정 비용(원). 0원은 safety 규칙(`media_cost_zero`)이 보류하므로 0이 아닌 값으로 둔다
MOCK_COST_KRW = {"image": 100, "video": 1000}


def _tiny_png() -> bytes:
    """1×1 PNG(유효한 파일). 오버레이·검사 쪽이 열어 볼 수 있다."""

    def chunk(kind: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(kind + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixel = zlib.compress(b"\x00\xf5\xc2\x7a")
    return (
        b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixel) + chunk(b"IEND", b"")
    )


class MockProvider:
    name = NAME

    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind == "image":
            data, suffix = _tiny_png(), "png"
        else:  # 영상 자리표시. 유효한 mp4가 아니다(오버레이 시험은 자체 샘플을 쓴다)
            data, suffix = b"MOCK-VIDEO-PLACEHOLDER\n", "mp4"
        path = request.output_dir / f"asset.{suffix}"
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        return MediaResult(
            asset_path=path,
            sha256=digest,
            provider=NAME,
            model_id=MODEL_ID,
            usage=None,
            generated_at=datetime.now(UTC),
            provider_job_id=f"mock_{request.prompt_sha256[:12]}",
        )
