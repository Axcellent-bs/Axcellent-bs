"""인스타 레이더 계정: 회사 연결에서 데이터 AI 레이더가 읽을 계정 목록을 정한다.

- `GET  /api/dashboard/instagram-radar`: 지금 쓰는 목록과 출처
  (`screen` 화면 저장 · `tenant` 회사 설정 파일 · `auto` 경쟁사 찾기로 확인한 계정 · `none` 없음),
  회사 설정 파일의 계정 수. 자동 목록은 `tenant.ig_radar_auto_accounts()`로만 읽는다
  (data를 import 하지 않음)
- `POST /api/dashboard/instagram-radar/save`: 목록 저장(사람 토큰).
  `@`·공백은 떼고 중복은 하나로, 최대 200곳
- `POST /api/dashboard/instagram-radar/clear`: 화면 저장 목록을 지우고 회사 설정 파일로 돌아간다

저장 파일은 `ax_agent_template.tenant.ig_radar_file()`(회사 연결 파일 옆).
data 레이더가 같은 파일을 읽는다.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ax_agent_template import tenant
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok

MAX_ACCOUNTS = 200
# 인스타 사용자 이름: 영문·숫자·`.`·`_`, 30자까지
HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

router = APIRouter(prefix="/api/dashboard/instagram-radar", dependencies=[Depends(require_human)])


class SaveIn(BaseModel):
    accounts: list[str] = Field(max_length=1000)


def _tenant_accounts() -> list[str]:
    path = tenant.setting("DATA_TREND_CONFIG")
    if not path or not Path(path).is_file():
        return []
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("instagram") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return []
    listed = raw.get("radar_accounts", []) if isinstance(raw, dict) else []
    return [str(a).strip().lstrip("@") for a in listed if str(a).strip()]


def _state() -> dict[str, Any]:
    saved = tenant.ig_radar_accounts()
    from_tenant = _tenant_accounts()
    auto = tenant.ig_radar_auto_accounts() or []
    if saved is not None:
        source, accounts = "screen", saved
    elif from_tenant:
        source, accounts = "tenant", from_tenant
    elif auto:  # data 경쟁사 찾기가 확인한 계정 (data 레이더와 같은 우선순위)
        source, accounts = "auto", auto
    else:
        source, accounts = "none", []
    return {
        "accounts": accounts[:MAX_ACCOUNTS],
        "source": source,
        "tenant_count": len(from_tenant),
        "max": MAX_ACCOUNTS,
        "editable": tenant.ig_radar_file() is not None,
    }


def _clean(raw: list[str]) -> list[str]:
    out: list[str] = []
    bad: list[str] = []
    for item in raw:
        handle = item.strip().lstrip("@").strip()
        if not handle:
            continue
        if not HANDLE.match(handle):
            bad.append(item.strip())
            continue
        if handle.lower() not in {h.lower() for h in out}:
            out.append(handle)
    if bad:
        shown = ", ".join(bad[:5])
        raise ApiError(422, "INVALID_HANDLE", f"인스타 계정 이름이 아니에요: {shown}")
    if len(out) > MAX_ACCOUNTS:
        raise ApiError(422, "TOO_MANY", f"레이더 계정은 {MAX_ACCOUNTS}곳까지예요 ({len(out)}곳)")
    return out


def _path() -> Path:
    path = tenant.ig_radar_file()
    if path is None:
        raise ApiError(409, "RADAR_NOT_EDITABLE", "회사 연결 파일 설정이 없어 저장할 수 없다")
    return path


@router.get("")
def get_radar() -> dict:
    return ok(_state())


@router.post("/save")
def save_radar(body: SaveIn) -> dict:
    path = _path()
    accounts = _clean(body.accounts)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"accounts": accounts}, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)
    return ok(_state())


@router.post("/clear")
def clear_radar() -> dict:
    _path().unlink(missing_ok=True)
    return ok(_state())
