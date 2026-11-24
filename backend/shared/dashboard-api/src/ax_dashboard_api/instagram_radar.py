"""인스타 레이더 계정: 회사 연결에서 데이터 AI 레이더가 읽을 계정 목록을 정한다.

- `GET  /api/dashboard/instagram-radar`: 지금 쓰는 목록과 출처
  (`screen` 화면 저장 · `tenant` 회사 설정 파일 · `auto` 경쟁사 찾기로 확인한 계정 · `none` 없음),
  회사 설정 파일의 계정 수. 자동 목록은 `tenant.ig_radar_auto_accounts()`로만 읽는다
  (data를 import 하지 않음)
- `POST /api/dashboard/instagram-radar/save`: 목록 저장(사람 토큰).
  `@`·공백은 떼고 중복은 하나로, 최대 200곳. 11/24: `meme_accounts`(밈·크리에이터 계정)도 같은
  파일에 둔다. 벤치마크와 합쳐 200곳, 두 칸에 같은 계정은 안 됨. 안 보내면 저장해 둔 밈 목록 그대로
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
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ax_agent_template import ad_words, tenant
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok

MAX_ACCOUNTS = 200
# 인스타 사용자 이름: 영문·숫자·`.`·`_`, 30자까지
HANDLE = re.compile(r"^[A-Za-z0-9._]{1,30}$")

router = APIRouter(prefix="/api/dashboard/instagram-radar", dependencies=[Depends(require_human)])


MIN_FOLLOWERS = 50_000  # [instagram] radar_min_followers 기본값 (creator·meme 계정만)


class MetaIn(BaseModel):
    """한 줄 고치기 (11/24): 사람이 고친 태그는 tag_source=human이고 AI가 다시 덮지 않는다."""

    account: str = Field(min_length=1, max_length=40)
    kind: Literal["industry", "creator", "meme", "brand"]
    ages: list[Literal["10s", "20s", "30s", "40s", "50s", "60plus"]] = Field(max_length=6)
    genders: list[Literal["male", "female", "all"]] = Field(max_length=3)
    topic: str | None = Field(default=None, max_length=20)


class SaveIn(BaseModel):
    accounts: list[str] = Field(max_length=1000)
    # 11/24 밈·크리에이터 계정. 안 보내면(None) 저장해 둔 밈 목록을 그대로 둔다
    meme_accounts: list[str] | None = Field(default=None, max_length=1000)


def _tenant_raw() -> dict[str, Any]:
    path = tenant.setting("DATA_TREND_CONFIG")
    if not path or not Path(path).is_file():
        return {}
    try:
        with Path(path).open("rb") as f:
            raw = tomllib.load(f).get("instagram") or {}
    except (OSError, tomllib.TOMLDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _tenant_list(key: str) -> list[str]:
    listed = _tenant_raw().get(key, [])
    listed = listed if isinstance(listed, list) else []
    return [str(a).strip().lstrip("@") for a in listed if str(a).strip()]


def _min_followers() -> int:
    try:
        return max(0, int(_tenant_raw().get("radar_min_followers", MIN_FOLLOWERS)))
    except (TypeError, ValueError):
        return MIN_FOLLOWERS


def _tenant_accounts() -> list[str]:
    return _tenant_list("radar_accounts")


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
    # 밈·크리에이터 계정: 화면 저장(같은 파일 meme_accounts) > 회사 설정 radar_meme_accounts > 없음
    saved_meme = tenant.ig_radar_meme_accounts()
    meme_tenant = _tenant_list("radar_meme_accounts")
    if saved_meme is not None:
        meme_source, meme = "screen", saved_meme
    elif meme_tenant:
        meme_source, meme = "tenant", meme_tenant
    else:
        meme_source, meme = "none", []
    accounts = accounts[:MAX_ACCOUNTS]
    lower = {a.lower() for a in accounts}
    meme = [a for a in meme if a.lower() not in lower][: max(0, MAX_ACCOUNTS - len(accounts))]
    # 11/24 계정 메타 (지금 회사 것만). 목록에 있는 계정만 보여 준다
    listed = {a.lower() for a in [*accounts, *meme]}
    meta = {a: row for a, row in tenant.ig_radar_meta().items() if a.lower() in listed}
    # 11/24 계정 이름에 욕설·성인 표현 → "광고 근거 제외: 표현" (보고서 근거에서 빠지는 계정)
    extra = ad_words.words_from(_tenant_raw())
    blocked = {a: m for a in [*accounts, *meme] if (m := ad_words.mark((a,), extra))}
    return {
        "ad_excluded": blocked,
        "accounts": accounts,
        "source": source,
        "tenant_count": len(from_tenant),
        "meme_accounts": meme,
        "meme_source": meme_source,
        "meme_tenant_count": len(meme_tenant),
        "max": MAX_ACCOUNTS,
        "editable": tenant.ig_radar_file() is not None,
        "meta": meta,
        "min_followers": _min_followers(),
        "meta_editable": tenant.ig_radar_meta_file() is not None,
    }


def _saved_file(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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
    data: dict[str, Any] = {"accounts": accounts}
    if body.meme_accounts is not None:
        meme = _clean(body.meme_accounts)
    else:
        kept = _saved_file(path).get("meme_accounts")
        meme = [a for a in kept if isinstance(a, str) and a] if isinstance(kept, list) else None
    if meme is not None:
        both = sorted({a.lower() for a in accounts} & {a.lower() for a in meme})
        if both:
            shown = ", ".join(both[:5])
            raise ApiError(422, "DUPLICATE_HANDLE", f"두 칸에 같은 계정이 있어요: {shown}")
        if len(accounts) + len(meme) > MAX_ACCOUNTS:
            raise ApiError(422, "TOO_MANY", f"레이더 계정은 합쳐서 {MAX_ACCOUNTS}곳까지예요"
                           f" ({len(accounts) + len(meme)}곳)")  # fmt: skip
        data["meme_accounts"] = meme
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)
    return ok(_state())


@router.post("/meta")
def save_meta(body: MetaIn) -> dict:
    """계정 한 줄의 종류·층·주제를 사람이 정한다 (tag_source=human, 팔로워 값은 그대로)."""
    from datetime import UTC, datetime

    handle = body.account.strip().lstrip("@")
    if not HANDLE.match(handle):
        raise ApiError(422, "INVALID_HANDLE", f"인스타 계정 이름이 아니에요: {body.account}")
    state = _state()
    listed = {a.lower(): a for a in [*state["accounts"], *state["meme_accounts"]]}
    if handle.lower() not in listed:
        raise ApiError(404, "NOT_IN_RADAR", f"레이더 목록에 없는 계정이에요: {handle}")
    name = listed[handle.lower()]
    topic = " ".join((body.topic or "").split()) or None

    def change(data: dict[str, Any]) -> dict[str, Any]:
        old = data["accounts"].get(name) or {}
        data["accounts"][name] = {
            **old, "kind": body.kind, "ages": list(dict.fromkeys(body.ages)),
            "genders": list(dict.fromkeys(body.genders)), "topic": topic,
            "tag_source": "human", "tagged_at": datetime.now(UTC).isoformat(),
        }  # fmt: skip
        return data

    if tenant.update_ig_radar_meta(change) is None:
        raise ApiError(409, "META_NOT_EDITABLE", "회사 연결 파일 설정이 없어 저장할 수 없다")
    return ok(_state())


@router.post("/clear")
def clear_radar() -> dict:
    _path().unlink(missing_ok=True)
    return ok(_state())
