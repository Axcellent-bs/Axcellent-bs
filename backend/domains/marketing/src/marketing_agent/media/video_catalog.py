"""선택 가능한 영상 공급자 목록(화면 드롭다운·API의 원본). 키 값은 절대 내지 않는다(설정 여부만).

- 내장: 목, Gemini, 그리고 `media/*_video.py`에서 자동 등록된 `AsyncVideoProvider`들.
- 커스텀: 저장된 스펙(`custom:<id>`). 꺼진 것도 목록에는 나오지만 고를 수 없다(`selectable=false`).
- `models`는 단가표(`prices.toml`)에 영상 단가가 있는 모델이다. 비어 있으면 가격 미확인이라
  호출이 비용 상한에서 막힌다(`priced=false`). 커스텀은 스펙 가격이 있으므로 항상 priced다.
"""

from __future__ import annotations

import os
from typing import Any

from marketing_agent.media import pricing, video_store
from marketing_agent.media.config import API_KEY_ENVS, MOCK, MediaSettings
from marketing_agent.media.custom_spec import public_view
from marketing_agent.media.gemini_video import (
    ALLOWED_RESOLUTIONS,
    MAX_DURATION_S,
    MIN_DURATION_S,
)
from marketing_agent.media.gemini_video import NAME as GEMINI_NAME
from marketing_agent.media.registry import ADAPTERS
from marketing_agent.media.video_common import AsyncVideoProvider


def _priced_models(provider: str) -> list[str]:
    models: list[str] = []
    for entry in pricing.load_table():
        if entry.provider == provider and entry.kind == "video" and entry.model not in models:
            models.append(entry.model)
    return models


def _configured(env: str) -> bool:
    return bool(os.getenv(env, "").strip())


def _builtin() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {
            "id": MOCK,
            "label": "목(시험용, 비용 없음)",
            "kind": "builtin",
            "key_env": None,
            "key_configured": True,
            "resolutions": ["720p"],
            "min_duration_s": 1,
            "max_duration_s": 8,
            "models": [],
            "priced": True,
            "selectable": True,
        },
        {
            "id": GEMINI_NAME,
            "label": "Gemini (Omni)",
            "kind": "builtin",
            "key_env": API_KEY_ENVS["video"],
            "key_configured": _configured(API_KEY_ENVS["video"]),
            "resolutions": list(ALLOWED_RESOLUTIONS),
            "min_duration_s": MIN_DURATION_S,
            "max_duration_s": MAX_DURATION_S,
            "models": _priced_models(GEMINI_NAME),
            "priced": bool(_priced_models(GEMINI_NAME)),
            "selectable": True,
        },
    ]
    for name, factory in sorted(ADAPTERS.items()):
        if not (isinstance(factory, type) and issubclass(factory, AsyncVideoProvider)):
            continue
        models = _priced_models(name)
        rows.append(
            {
                "id": name,
                "label": str(getattr(factory, "label", "") or name),
                "kind": "builtin",
                "key_env": factory.key_env,
                "key_configured": _configured(factory.key_env),
                "resolutions": list(factory.resolutions),
                "min_duration_s": factory.min_duration_s,
                "max_duration_s": factory.max_duration_s,
                "models": models,
                "priced": bool(models),
                "selectable": True,
            }
        )
    return rows


def _custom() -> list[dict[str, Any]]:
    rows = []
    for spec in video_store.custom_specs().values():
        view = public_view(spec)
        rows.append(
            {
                "id": spec.provider_name,
                "label": spec.label,
                "kind": "custom",
                "key_env": spec.auth.secretRef,
                "key_configured": view["keyConfigured"],
                "resolutions": list(spec.limits.resolutions),
                "min_duration_s": spec.limits.minDurationSec,
                "max_duration_s": spec.limits.maxDurationSec,
                "models": [spec.defaultModel] if spec.defaultModel else [],
                "priced": True,
                "selectable": spec.enabled,
                "enabled": spec.enabled,
                "spec": view,
            }
        )
    return rows


def list_video_providers() -> dict[str, Any]:
    settings = MediaSettings.from_env()
    chosen = video_store.selection()
    return {
        "selected": {
            "provider": settings.video_provider,
            "model": settings.video_model,
            "source": "ui" if chosen else "env",
            "updated_by": chosen["updated_by"] if chosen else None,
            "updated_at": chosen["updated_at"] if chosen else None,
        },
        "providers": [*_builtin(), *_custom()],
        "custom_host_allowlist_set": bool(
            os.getenv("MARKETING_CUSTOM_VIDEO_ALLOWED_HOSTS", "").strip()
        ),
        "history": video_store.history(),
    }
