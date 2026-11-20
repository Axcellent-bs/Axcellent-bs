"""환경변수 설정. 값은 모두 `.env`(사람 입력)에서만 온다. 기본값을 지어내지 않는다.

- 공급자·모델: `MARKETING_IMAGE_PROVIDER/MODEL`, `MARKETING_VIDEO_PROVIDER/MODEL`.
  공급자 기본은 `mock`, 모델 기본은 비움.
- 오디오(BGM): `MARKETING_AUDIO_PROVIDER/MODEL/API_KEY`(영상·이미지와 별개),
  모드 `MARKETING_AUDIO_MODE`(`source` 기본 | `off` | `bgm_overlay`, 모르는 값은 off).
  `source`: 공급자가 만든 영상 소리를 그대로 쓴다(별도 BGM은 확인 단계에서 사람이 고를 때만).
  `off`: 예전 동작(항상 무음).
  `bgm_overlay`: 영상 소리를 버리고 BGM을 한 번에 입히는 예전 묶음 흐름.
  ffmpeg 경로 `MARKETING_FFMPEG_BIN`(기본 `ffmpeg`).
- 키: `MARKETING_IMAGE_API_KEY`, `MARKETING_VIDEO_API_KEY`, `MARKETING_AUDIO_API_KEY`.
  이 모듈은 이름만 정의하고, 설정돼 있는지만 `has_api_key`로 알려 준다.
  값은 돌려주지도 로그에 내지도 않는다 (실제 어댑터가 직접 읽는다).
- 사용량 예산(우리 키 전용): 원화 단가표·환율은 쓰지 않는다. 종류별 월 예산을 공급자 단위 그대로
  정한다: 영상 초 `MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY`, 이미지 장
  `MARKETING_QUOTA_IMAGE_COUNT_MONTHLY`, BGM 곡 `MARKETING_QUOTA_AUDIO_COUNT_MONTHLY`.
  화면에서 정한 값(`quota_store`)이 환경변수보다 우선한다. 우리 키로 실제 공급자를 부르는데
  그 종류의 예산이 비어 있으면 호출을 거부한다(`quota_not_configured`, 목 모드는 허용).
- 키 주인 `MARKETING_MEDIA_KEY_OWNER`: `server`(기본, 우리 키라 예산을 건다) | `customer`
  (고객사가 가져온 자기 키라 예산을 안 건다). 모르는 값은 server로 본다.
- 영상 길이 상한: `MARKETING_VIDEO_MAX_DURATION_S`(운영자가 정한다, 1~90초, 기본 15).
  숫자가 아니거나 범위 밖이면 영상 생성을 거부한다(`video_max_duration_invalid`).
  경고 길이 `MARKETING_VIDEO_WARN_DURATION_S`(1~상한, 기본 30): 화면 경고창 기준(UX).
  단일 클립 한도(공급자 규격)와 별개이며, 상한이 클립 한도보다 크면 클립을 이어 붙인다.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ax_secret_store import get_secret
from marketing_agent.media import quota_store
from marketing_agent.media.base import MediaKind
from marketing_agent.media.video_limits import (
    DEFAULT_MAX_DURATION_S,
    DEFAULT_WARN_DURATION_S,
    max_duration_from_env,
    warn_duration_from_env,
)

log = logging.getLogger(__name__)

MOCK = "mock"
LEDGER_DB_ENV = "MARKETING_MEDIA_LEDGER_DB"
MEDIA_DIR_ENV = "MARKETING_MEDIA_DIR"
API_KEY_ENVS = {
    "image": "MARKETING_IMAGE_API_KEY",
    "video": "MARKETING_VIDEO_API_KEY",
    "audio": "MARKETING_AUDIO_API_KEY",
}
# 공급자별 키 변수. Gemini는 예전부터 종류별 키(`API_KEY_ENVS`)를 쓴다.
# 새 공급자는 반드시 자기 변수만 읽는다
# (`MARKETING_VIDEO_API_KEY`에 든 Google 키가 다른 회사 서버로 가면 안 된다).
SEEDANCE_API_KEY_ENV = "MARKETING_SEEDANCE_API_KEY"
KLING_API_KEY_ENV = "MARKETING_KLING_API_KEY"
FAL_API_KEY_ENV = "MARKETING_FAL_API_KEY"
REPLICATE_API_KEY_ENV = "MARKETING_REPLICATE_API_KEY"
HIGGSFIELD_API_KEY_ENV = "MARKETING_HIGGSFIELD_API_KEY"  # `<ID>:<SECRET>` 한 줄
# Higgsfield 이미지 모델 경로(예: 나노바나나). 키 아님. 콘솔에서 확인한 경로를 그대로 넣는다
HIGGSFIELD_IMAGE_MODEL_ENV = "MARKETING_HIGGSFIELD_IMAGE_MODEL"
# Higgsfield 영상 모델 경로(스토리보드 영상, 사람 승인 뒤). 키 아님
HIGGSFIELD_VIDEO_MODEL_ENV = "MARKETING_HIGGSFIELD_VIDEO_MODEL"
WAN_API_KEY_ENV = "MARKETING_WAN_API_KEY"
WAN_WORKSPACE_ENV = "MARKETING_WAN_WORKSPACE_ID"  # 키 아님. Model Studio 작업공간 ID
PIXVERSE_API_KEY_ENV = "MARKETING_PIXVERSE_API_KEY"
PIKA_API_KEY_ENV = "MARKETING_PIKA_API_KEY"
LUMA_API_KEY_ENV = "MARKETING_LUMA_API_KEY"
VIDU_API_KEY_ENV = "MARKETING_VIDU_API_KEY"
RUNWAY_API_KEY_ENV = "MARKETING_RUNWAY_API_KEY"
FFMPEG_BIN_ENV = "MARKETING_FFMPEG_BIN"
QUOTA_ENVS = {
    "video": "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY",
    "image": "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY",
    "audio": "MARKETING_QUOTA_AUDIO_COUNT_MONTHLY",
}
KEY_OWNER_ENV = "MARKETING_MEDIA_KEY_OWNER"
KEY_OWNER_SERVER = "server"
KEY_OWNER_CUSTOMER = "customer"
AUDIO_MODE_ENV = "MARKETING_AUDIO_MODE"
AUDIO_MODES = ("source", "off", "bgm_overlay")
DEFAULT_AUDIO_MODE = "source"


def _text(name: str) -> str:
    return os.getenv(name, "").strip()


def _secret(name: str) -> str:
    """API 키. 스위치가 db면 암호화 저장소, env면 os.getenv와 같다."""
    return get_secret(name).strip()


def _integer(name: str) -> int | None:
    raw = _text(name)
    return int(raw) if raw.isdigit() and int(raw) > 0 else None


def audio_mode_from_env() -> str:
    """`source`(기본, 비어 있을 때)·`off`·`bgm_overlay`. 모르는 값(오타)은 off(무음)로 본다.

    source여도 소리가 없는 공급자·모델은 무음 영상이고, 합성 BGM은 만들지 않는다(사람이 고를 때만).
    """
    mode = _text(AUDIO_MODE_ENV).lower()
    if not mode:
        return DEFAULT_AUDIO_MODE
    return mode if mode in AUDIO_MODES else "off"


def key_owner_from_env() -> str:
    """`customer`만 고객사 키로 본다. 비었거나 모르는 값은 server(예산을 건다)."""
    owner = _text(KEY_OWNER_ENV).lower()
    return KEY_OWNER_CUSTOMER if owner == KEY_OWNER_CUSTOMER else KEY_OWNER_SERVER


def ffmpeg_bin() -> str:
    """ffmpeg 실행 파일 경로. 설정이 없으면 PATH의 `ffmpeg`."""
    return _text(FFMPEG_BIN_ENV) or "ffmpeg"


def _video_selection(provider: str, model: str) -> tuple[str, str]:
    """화면에서 고른 영상 공급자(`video_store`)가 있으면 환경변수보다 우선한다.

    고른 모델이 비어 있으면 같은 공급자일 때만 환경변수 모델을 쓰고, 커스텀 공급자는 스펙의
    기본 모델을 쓴다. 그래도 비면 `model_not_configured`로 호출 전에 막힌다.
    """
    from marketing_agent.media import video_store  # 순환 import 피하려 늦게 읽는다

    chosen = video_store.selection()
    if chosen is None:
        return provider, model
    name = chosen["provider"].lower()
    picked = chosen["model"].strip()
    if not picked and name == provider:
        picked = model
    if not picked and name.startswith("custom:"):
        spec = video_store.get_custom(name.removeprefix("custom:"))
        picked = spec.defaultModel if spec else ""
    return name, picked


@dataclass(frozen=True)
class MediaSettings:
    image_provider: str
    image_model: str
    video_provider: str
    video_model: str
    media_dir: Path
    audio_provider: str = MOCK
    audio_model: str = ""
    # 종류별 월 사용량 예산(영상 초·이미지 장·BGM 곡). 없으면 None. 우리 키(server)에만 쓴다
    video_quota: int | None = None
    image_quota: int | None = None
    audio_quota: int | None = None
    key_owner: str = KEY_OWNER_SERVER
    audio_mode: str = DEFAULT_AUDIO_MODE
    video_max_duration_s: int = DEFAULT_MAX_DURATION_S
    video_max_duration_problem: str | None = None  # 설정이 잘못됐으면 코드(영상 생성 거부)
    # 경고창 기준 길이. 화면 안내용이며 안전 관문이 아니다(잘못된 값은 기본값으로 바꾼다)
    video_warn_duration_s: int = DEFAULT_WARN_DURATION_S

    @classmethod
    def from_env(cls) -> MediaSettings:
        video_provider = _text("MARKETING_VIDEO_PROVIDER").lower() or MOCK
        video_model = _text("MARKETING_VIDEO_MODEL")
        video_provider, video_model = _video_selection(video_provider, video_model)
        max_duration = max_duration_from_env()
        warn = warn_duration_from_env(max_duration.seconds)
        if warn.problem is not None:
            log.warning("video warn duration setting ignored: %s", warn.problem)  # 사유 코드만
        return cls(
            video_quota=quota_store.quota("video") or _integer(QUOTA_ENVS["video"]),
            image_quota=quota_store.quota("image") or _integer(QUOTA_ENVS["image"]),
            audio_quota=quota_store.quota("audio") or _integer(QUOTA_ENVS["audio"]),
            key_owner=key_owner_from_env(),
            video_warn_duration_s=warn.seconds,
            video_max_duration_s=max_duration.seconds,
            video_max_duration_problem=max_duration.problem,
            image_provider=_text("MARKETING_IMAGE_PROVIDER").lower() or MOCK,
            image_model=_text("MARKETING_IMAGE_MODEL"),
            video_provider=video_provider,
            video_model=video_model,
            audio_provider=_text("MARKETING_AUDIO_PROVIDER").lower() or MOCK,
            audio_model=_text("MARKETING_AUDIO_MODEL"),
            audio_mode=audio_mode_from_env(),
            media_dir=Path(_text(MEDIA_DIR_ENV) or Path(tempfile.gettempdir()) / "marketing_media"),
        )

    @property
    def quota_applies(self) -> bool:
        """우리 키(server)로 만들 때만 예산을 건다. 고객사 키(customer)는 안 건다."""
        return self.key_owner != KEY_OWNER_CUSTOMER

    def quota(self, kind: str | None) -> int | None:
        """그 종류의 월 사용량 예산(영상 초·이미지 장·BGM 곡). 없으면 None."""
        if kind == "video":
            return self.video_quota
        if kind == "image":
            return self.image_quota
        return self.audio_quota if kind == "audio" else None

    def quotas(self) -> dict[str, int | None]:
        return {"video": self.video_quota, "image": self.image_quota, "audio": self.audio_quota}

    def provider(self, kind: MediaKind) -> str:
        if kind == "audio":
            return self.audio_provider
        return self.image_provider if kind == "image" else self.video_provider

    def model(self, kind: MediaKind) -> str:
        if kind == "audio":
            return self.audio_model
        return self.image_model if kind == "image" else self.video_model


def has_api_key(kind: MediaKind) -> bool:
    """키가 설정돼 있는지만 알려 준다. 값은 돌려주지 않는다."""
    return bool(_secret(API_KEY_ENVS[kind]))
