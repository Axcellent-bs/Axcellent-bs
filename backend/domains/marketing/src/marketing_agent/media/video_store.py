"""영상 공급자 선택과 커스텀 공급자 스펙 저장소(JSON 파일 + 변경 이력).

- 파일: `MARKETING_VIDEO_PROVIDERS_FILE`(없으면 `<MARKETING_MEDIA_DIR>/video_providers.json`).
  내용 `{"version":1, "selection":{provider,model,updated_by,updated_at}, "custom":{id: 스펙}}`.
  키 값은 어디에도 없다(스펙에는 환경변수 이름만 있다).
- 이력: `<파일>.history.jsonl`에 바꿀 때마다 한 줄(언제·누가·무엇을·대상 id). 스펙 전체나 키는 적지
  않는다.
- 쓰는 곳은 마케팅 skill(`video_providers.py`, execute() 관문 뒤) 한 곳이다. 읽기는 누구나.
- 선택(`selection`)이 있으면 환경변수 `MARKETING_VIDEO_PROVIDER/MODEL`보다 우선한다
  (`config.MediaSettings.from_env`). 깨진 파일은 없는 것으로 본다(환경변수로 돌아간다).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marketing_agent import company_scope
from marketing_agent.media.custom_spec import MAX_PROVIDERS, CustomVideoSpec, validate_spec

FILE_ENV = "MARKETING_VIDEO_PROVIDERS_FILE"
_LOCK = threading.Lock()


class VideoStoreError(Exception):
    """저장소 쓰기·검사 실패. 메시지는 사람이 읽는 사유다."""


def store_path() -> Path:
    """`MARKETING_VIDEO_PROVIDERS_FILE` → 회사 폴더(옛 자리에서 옮김) → 옛 자리."""
    return company_scope.config_file(FILE_ENV, "video_providers.json")


def _read() -> dict[str, Any]:
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write(data: dict[str, Any]) -> None:
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        raise VideoStoreError("저장소에 쓸 수 없다") from None


def _history(who: str, action: str, target: str) -> None:
    path = store_path().with_name(store_path().name + ".history.jsonl")
    line = {
        "at": datetime.now(UTC).isoformat(),
        "by": who[:64],
        "action": action,
        "target": target[:80],
    }
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        raise VideoStoreError("변경 이력을 쓸 수 없다") from None


def history(limit: int = 10) -> list[dict[str, Any]]:
    path = store_path().with_name(store_path().name + ".history.jsonl")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out[::-1]


def custom_specs() -> dict[str, CustomVideoSpec]:
    """저장된 커스텀 스펙. 지금 규칙(허용 호스트 포함)을 못 지키는 건 건너뛴다(fail-closed)."""
    found: dict[str, CustomVideoSpec] = {}
    custom = _read().get("custom")
    for spec_id, raw in custom.items() if isinstance(custom, dict) else []:
        try:
            spec = validate_spec(raw)
        except ValueError:
            continue
        if spec.id == spec_id:
            found[spec_id] = spec
    return found


def get_custom(spec_id: str) -> CustomVideoSpec | None:
    return custom_specs().get(spec_id)


def selection() -> dict[str, str] | None:
    raw = _read().get("selection")
    if not isinstance(raw, dict):
        return None
    provider, model = raw.get("provider"), raw.get("model", "")
    if not isinstance(provider, str) or not provider or not isinstance(model, str):
        return None
    return {
        "provider": provider,
        "model": model,
        "updated_by": str(raw.get("updated_by") or ""),
        "updated_at": str(raw.get("updated_at") or ""),
    }


def save_custom(spec: CustomVideoSpec, who: str) -> None:
    with _LOCK:
        data = _read()
        custom = data.get("custom") if isinstance(data.get("custom"), dict) else {}
        if spec.id not in custom and len(custom) >= MAX_PROVIDERS:
            raise VideoStoreError(f"커스텀 공급자는 {MAX_PROVIDERS}개까지다")
        custom[spec.id] = spec.model_dump(mode="json")
        data.update(version=1, custom=custom)
        _write(data)
        _history(who, "save_custom", spec.id)


def delete_custom(spec_id: str, who: str) -> bool:
    with _LOCK:
        data = _read()
        custom = data.get("custom") if isinstance(data.get("custom"), dict) else {}
        if spec_id not in custom:
            return False
        del custom[spec_id]
        chosen = data.get("selection")
        if isinstance(chosen, dict) and chosen.get("provider") == f"custom:{spec_id}":
            data.pop("selection", None)  # 지운 공급자가 선택돼 있으면 환경변수로 돌아간다
        data.update(version=1, custom=custom)
        _write(data)
        _history(who, "delete_custom", spec_id)
        return True


def set_selection(provider: str, model: str, who: str) -> None:
    with _LOCK:
        data = _read()
        data.update(
            version=1,
            selection={
                "provider": provider,
                "model": model,
                "updated_by": who[:64],
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )
        _write(data)
        _history(who, "select", f"{provider}/{model}")
