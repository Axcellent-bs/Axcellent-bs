"""회사 홍보 영상 방향 (11/24 "재미 먼저, 브랜드는 끝에").

마케팅 › 브랜드(/marketing/brand)의 두 줄.
- `GET  /api/dashboard/promo-style`: 지금 회사의 promo_style·brand_exposure와 칸마다 출처
  (`screen` 화면 저장 · `tenant` 회사 설정 tenant.toml [brand] · `default` 기본값).
  사람 토큰이면 누구나
- `POST /api/dashboard/promo-style/save`: 두 칸 저장. 고객사 관리자(`admin`)만.
  모르는 값은 422
- `POST /api/dashboard/promo-style/clear`: 이 회사의 화면 저장 값을 지우고 회사 설정 파일로.
  관리자만

저장 파일은 `ax_agent_template.tenant.promo_file()`(회사 연결 파일 옆 `promo_style.json`).
회사 이름으로 나눠 저장해서 다른 회사 값은 읽지도 덮지도 않는다.
data 트렌드 보고서(`tenant.promo()`)가 같은 파일을 읽는다.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field

from ax_agent_template import human_auth
from ax_agent_template import tenant as tenants
from ax_dashboard_api import accounts
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok

router = APIRouter(prefix="/api/dashboard/promo-style", dependencies=[Depends(require_human)])
_lock = threading.Lock()


class SaveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promo_style: Literal["fun_first", "product"]
    brand_exposure: Literal["end_card", "prop", "caption"]
    # 기본 캐릭터·배경·그림체 (선택). 안 보내면 저장돼 있던 값 그대로, 빈 글자("")면 지움.
    # 영어 칸은 사람이 쓴다(자동 번역 없음). 금칙·길이는 tenant.brand_ok와 같다
    character_ko: str | None = Field(default=None, max_length=200)
    character_en: str | None = Field(default=None, max_length=200)
    setting_ko: str | None = Field(default=None, max_length=200)
    setting_en: str | None = Field(default=None, max_length=200)
    style_en: str | None = Field(default=None, max_length=200)


def _admin(authorization: str | None) -> human_auth.Human:
    human = accounts.session_human(authorization)
    if human.role != "admin":
        raise ApiError(403, "ROLE_REQUIRED", "고객사 관리자만 홍보 영상 방향을 바꿀 수 있어요")
    if not any(r.get("id") == human.account_id for r in accounts.load_accounts()):
        raise ApiError(401, "ACCOUNT_GONE", "계정이 없어졌어요. 다시 로그인해 주세요")
    return human


def _company() -> str:
    name = tenants.screen_key()  # 설정 폴더가 없는 회사는 정식 회사 id (11/25)
    if not name:
        raise ApiError(409, "NO_COMPANY", "회사를 먼저 연결해 주세요")
    return name


def _path() -> Path:
    path = tenants.promo_file()
    if path is None:
        raise ApiError(409, "PROMO_NOT_EDITABLE", "회사 연결 파일 설정이 없어 저장할 수 없다")
    return path


def _state() -> dict[str, Any]:
    name = tenants.screen_key()  # 저장 열쇠: 설정 폴더 이름 → 없으면 정식 회사 id (11/25)
    folder = tenants.current()  # 회사 설정 파일(tenant.toml)은 설정 폴더 이름으로만 읽는다
    saved = tenants.promo_saved_all().get(name, {}) if name else {}
    from_file = tenants.promo_tenant(folder) if folder else {}
    now = {k: saved.get(k) or from_file.get(k) or d for k, d in tenants.PROMO_DEFAULTS.items()}
    source = {k: "screen" if k in saved else "tenant" if k in from_file else "default"
              for k in tenants.PROMO_DEFAULTS}  # fmt: skip
    brand_screen = tenants.brand_saved(name)
    section = tenants.brand_section(folder) if folder else {}
    for k in tenants.BRAND_DEFAULTS:
        source[k] = (
            "screen" if k in brand_screen
            else "tenant" if tenants.brand_ok(k, section.get(k))
            else "default"
        )  # fmt: skip
    shown = (
        tenants.brand_values({**section, **brand_screen}) if name else dict(tenants.BRAND_DEFAULTS)
    )
    return {
        **now,
        **shown,
        "source": source,
        "defaults": {**tenants.PROMO_DEFAULTS, **tenants.BRAND_DEFAULTS},
        "editable": tenants.promo_file() is not None and name is not None,
    }


def _raw(path: Path) -> dict[str, Any]:
    """파일 전체 (다른 회사 칸을 그대로 두고 이 회사 칸만 바꾸려고). 없거나 깨졌으면 빈 모양."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"companies": {}}
    if not isinstance(data, dict) or not isinstance(data.get("companies"), dict):
        return {"companies": {}}
    return data


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


@router.get("")
def get_promo_style() -> dict:
    return ok(_state())


@router.post("/save")
def save_promo_style(body: SaveIn, authorization: str | None = Header(None)) -> dict:
    _admin(authorization)
    name, path = _company(), _path()
    sent = body.model_fields_set
    brand_in: dict[str, str] = {}
    for key in tenants.BRAND_DEFAULTS:
        text = " ".join((getattr(body, key) or "").split())
        if key in sent and text:
            if not tenants.brand_ok(key, text):  # 금칙·길이·글자 규칙은 tenant와 같다
                raise ApiError(422, "BRAND_FIELD_REJECTED", f"{key} 칸에 쓸 수 없는 말이 있어요")
            brand_in[key] = text
    with _lock:
        data = _raw(path)
        row = {"promo_style": body.promo_style, "brand_exposure": body.brand_exposure}
        old = data["companies"].get(name)
        old = old if isinstance(old, dict) else {}
        for key in tenants.BRAND_DEFAULTS:  # 안 보낸 칸은 저장돼 있던 값 그대로, ""는 지움
            if key in sent:
                if key in brand_in:
                    row[key] = brand_in[key]
            elif isinstance(old.get(key), str):
                row[key] = old[key]
        data["companies"][name] = row
        _write(path, data)
    return ok(_state())


@router.post("/clear")
def clear_promo_style(authorization: str | None = Header(None)) -> dict:
    _admin(authorization)
    name, path = _company(), _path()
    with _lock:
        data = _raw(path)
        if data["companies"].pop(name, None) is not None:
            _write(path, data)
    return ok(_state())
