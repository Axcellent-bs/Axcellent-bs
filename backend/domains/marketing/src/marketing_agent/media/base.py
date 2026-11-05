"""공급자 인터페이스. 어댑터는 정규화된 요청을 받아 asset 파일을 만들고 결과를 돌려준다.

- 공급자 이름·모델 ID는 환경변수에서만 읽는다(`config.py`). 어댑터에 모델 이름을 박지 않는다.
- 어댑터는 비용을 계산하지 않는다. 비용은 단가표(`pricing.py`)와 장부(`ledger.py`)가 맡는다.
- 키는 어댑터가 환경변수에서 읽기만 하고 로그·결과에 내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol

MediaKind = Literal["image", "video"]


@dataclass(frozen=True)
class MediaRequest:
    """공급자에게 넘기는 정규화된 요청. 프롬프트는 장면 템플릿으로 만든 문장뿐이다."""

    media_kind: MediaKind
    prompt: str
    prompt_sha256: str
    resolution: str  # 이미지 "1024x1024", 영상 "720p"
    quality: str | None  # 이미지만
    duration_s: int | None  # 영상만
    remove_audio: bool  # 영상: True(기본)면 무음으로 받는다. 오디오는 쓰지 않는다
    model: str  # 설정(`MARKETING_*_MODEL`)에서 온 값
    output_dir: Path  # asset을 쓸 폴더(이미 만들어 둔다)


@dataclass(frozen=True)
class Usage:
    """공급자 응답의 사용량. 모르는 항목은 None."""

    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True)
class MediaResult:
    asset_path: Path
    sha256: str
    provider: str
    model_id: str  # 응답에서 받은 값
    usage: Usage | None  # 응답에 없으면 None
    generated_at: datetime
    provider_job_id: str


class MediaProvider(Protocol):
    name: str

    def generate(self, request: MediaRequest) -> MediaResult:
        """asset 파일을 `request.output_dir`에 쓰고 결과를 돌려준다. 실패하면 예외."""
        ...
