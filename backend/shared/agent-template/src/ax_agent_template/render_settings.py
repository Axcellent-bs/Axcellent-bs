"""회사별 만들기 설정 (11/25): 그림 모델·영상 모델·그림 크레딧 사용·영상 길이·비율.

`promo_style.json`과 같은 방식. 화면(마케팅 › 만들기 설정)이 저장하고 마케팅이 읽는다.
파일: `AX_RENDER_SETTINGS_FILE` → 회사 연결 파일 옆 `render_settings.json`
({"companies": {"<회사 이름>": {칸…}}}). 회사 이름은 `tenant.current()`(회사 설정 이름)이다.
다른 회사 값은 읽지도 덮지도 않는다.

칸은 모두 선택이다. 비어 있으면 서버 설정값(지금 동작)을 쓴다.
- `image_model`·`video_model`: Higgsfield 모델 id 글
- `image_credits_ok`: 그림에 크레딧을 써도 되나 (비어 있으면 true = 지금 동작)
- `video_seconds`: `"plan"`(기획 길이) 또는 정수 초
- `aspect`: `"plan"` | `"9:16"` | `"1:1"` | `"16:9"` (그림·영상 같은 비율)
- `speed`: 재생 속도 `"1.0"` | `"1.2"` | `"1.5"`
  (비어 있으면 서버 설정 `MARKETING_VIDEO_SPEED` → "1.2")
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

from ax_agent_template import company_scope
from ax_agent_template import tenant as tenants

FILE_ENV = "AX_RENDER_SETTINGS_FILE"
FILE = "render_settings.json"
ASPECTS = ("plan", "9:16", "1:1", "16:9")
FIELDS = ("image_model", "video_model", "image_credits_ok", "video_seconds", "aspect", "speed")
SPEEDS = ("1.0", "1.2", "1.5")  # 재생 속도(11/25). 계약 `marketing_render_settings.SPEEDS`와 같다
MODEL_MAX = 120
SECONDS_MAX = 600
lock = threading.Lock()  # 쓰는 쪽(dashboard-api)이 쓴다


def settings_file() -> Path | None:
    raw = os.getenv(FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(FILE) if conn else None


def clean(row: Any) -> dict[str, Any]:
    """칸마다 모양이 맞는 것만 남긴다 (모르는 칸·틀린 값·빈 값은 뺀다)."""
    if not isinstance(row, dict):
        return {}
    out: dict[str, Any] = {}
    for key in ("image_model", "video_model"):
        value = row.get(key)
        if isinstance(value, str) and value.strip() and len(value.strip()) <= MODEL_MAX:
            out[key] = value.strip()
    if isinstance(row.get("image_credits_ok"), bool):
        out["image_credits_ok"] = row["image_credits_ok"]
    secs = row.get("video_seconds")
    if secs == "plan" or (isinstance(secs, int) and not isinstance(secs, bool)
                          and 1 <= secs <= SECONDS_MAX):  # fmt: skip
        out["video_seconds"] = secs
    if row.get("aspect") in ASPECTS:
        out["aspect"] = row["aspect"]
    if row.get("speed") in SPEEDS:
        out["speed"] = row["speed"]
    return out


def raw(path: Path) -> dict[str, Any]:
    """파일 전체 (없거나 깨졌으면 빈 모양)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"companies": {}}
    if not isinstance(data, dict) or not isinstance(data.get("companies"), dict):
        return {"companies": {}}
    return data


def write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(tmp, path)


def saved(name: str | None = None) -> dict[str, Any]:
    """회사의 화면 저장 값 (없으면 빈 dict). 회사 이름은 `tenant.current()`."""
    name = name if name is not None else tenants.current()
    path = settings_file()
    if not name or path is None:
        return {}
    return clean(raw(path)["companies"].get(name))


def saved_for(company_id: str | None) -> dict[str, Any]:
    """그 회사(정식 id)의 화면 저장 값. 회사를 모르면 지금 요청 회사."""
    if not company_id:
        return saved()
    with company_scope.using(company_id):
        return saved()
