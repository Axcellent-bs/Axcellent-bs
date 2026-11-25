"""회사별 만들기 설정 (11/25): 그림 모델·영상 모델·그림 크레딧·영상 길이·비율.

마케팅 › 만들기 설정. `promo_style.py`와 같은 방식.
- `GET  /api/dashboard/marketing/render-settings`: 저장 값(`settings`, 없으면 null)·서버 설정 기본값
  (`defaults`)·모델 목록(`models`)·지금 사람이 고칠 수 있나(`can_edit`). 사람 토큰이면 누구나
- `POST .../save`: 고객사 관리자(`admin`)만. 모델이 목록에 없거나 길이·비율이 그 영상 모델
  범위 밖이면 422. 모르는 범위(목록이 비었거나 min_s·max_s를 모름)는 막지 않는다
- `POST .../clear`: 이 회사의 저장 값을 지우고 서버 설정값으로. 관리자만

저장 파일은 `ax_agent_template.render_settings`(회사 연결 파일 옆 `render_settings.json`,
회사 이름별). 마케팅이 같은 파일을 읽는다. 모델 목록은 마케팅 skill `render_settings_options`
(회사 Higgsfield 연결의 `models_explore`, 하루 기억)에서 받는다.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header

from ax_agent_template import render_settings as store
from ax_agent_template import tenant as tenants
from ax_contracts.schemas.marketing_render_settings import RenderSettingsValues
from ax_dashboard_api import accounts, marketing_client
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.promo_style import _admin, _company

router = APIRouter(
    prefix="/api/dashboard/marketing/render-settings", dependencies=[Depends(require_human)]
)
NO_DEFAULTS: dict[str, Any] = {
    "image_model": None,
    "video_model": None,
    "image_credits_ok": True,
    "video_seconds": "plan",
    "aspect": "plan",
    "speed": None,  # 마케팅을 못 부르면 서버 기본값을 모른다(지어내지 않는다)
}


def _path() -> Any:
    path = store.settings_file()
    if path is None:
        raise ApiError(
            409, "RENDER_SETTINGS_NOT_EDITABLE", "회사 연결 파일 설정이 없어 저장할 수 없다"
        )
    return path


async def _options(*, strict: bool) -> dict[str, Any]:
    """마케팅에서 모델 목록·기본값. 보기는 못 받아도 빈 목록, 저장은 받아야 검사한다."""
    try:
        got = await marketing_client.render_settings_options_call()
    except ApiError:
        if strict:
            raise
        got = {}
    models = got.get("models") if isinstance(got.get("models"), dict) else {}
    return {
        "defaults": got.get("defaults") or dict(NO_DEFAULTS),
        "models": {"image": models.get("image") or [], "video": models.get("video") or []},
    }


async def _state(authorization: str | None) -> dict[str, Any]:
    opts = await _options(strict=False)
    saved = store.saved()
    human = accounts.session_human(authorization)
    can_edit = bool(
        human.role == "admin" and store.settings_file() is not None and tenants.current()
    )
    return {
        "settings": {**{k: None for k in store.FIELDS}, **saved} if saved else None,
        "defaults": opts["defaults"],
        "models": opts["models"],
        "can_edit": can_edit,
    }


def _check(body: RenderSettingsValues, opts: dict[str, Any]) -> None:
    images = {m["id"] for m in opts["models"]["image"]}
    videos = {m["id"]: m for m in opts["models"]["video"]}
    if body.image_model and images and body.image_model not in images:
        raise ApiError(422, "UNKNOWN_IMAGE_MODEL", "목록에 없는 그림 모델이에요")
    if body.video_model and videos and body.video_model not in videos:
        raise ApiError(422, "UNKNOWN_VIDEO_MODEL", "목록에 없는 영상 모델이에요")
    chosen = body.video_model or (opts["defaults"] or {}).get("video_model")
    spec = videos.get(chosen) if chosen else None
    if spec is None:
        return
    secs = body.video_seconds
    if isinstance(secs, int):
        lo, hi = spec.get("min_s"), spec.get("max_s")
        if (lo is not None and secs < lo) or (hi is not None and secs > hi):
            raise ApiError(
                422, "SECONDS_OUT_OF_RANGE", f"이 모델은 {lo or '?'}~{hi or '?'}초만 돼요"
            )
    aspects = spec.get("aspects") or []
    if body.aspect and body.aspect != "plan" and aspects and body.aspect not in aspects:
        raise ApiError(422, "ASPECT_NOT_SUPPORTED", "이 영상 모델이 지원하지 않는 비율이에요")


@router.get("")
async def get_render_settings(authorization: str | None = Header(None)) -> dict:
    return ok(await _state(authorization))


@router.post("/save")
async def save_render_settings(
    body: RenderSettingsValues, authorization: str | None = Header(None)
) -> dict:
    _admin(authorization)
    name, path = _company(), _path()
    _check(body, await _options(strict=True))
    row = store.clean(body.model_dump())
    with store.lock:
        data = store.raw(path)
        if row:
            data["companies"][name] = row
        else:
            data["companies"].pop(name, None)
        store.write(path, data)
    return ok(await _state(authorization))


@router.post("/clear")
async def clear_render_settings(authorization: str | None = Header(None)) -> dict:
    _admin(authorization)
    name, path = _company(), _path()
    with store.lock:
        data = store.raw(path)
        if data["companies"].pop(name, None) is not None:
            store.write(path, data)
    return ok(await _state(authorization))
