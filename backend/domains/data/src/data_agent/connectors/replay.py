"""외부 API(네이버·YouTube) 응답 저장·재생. 로컬 확인에서 할당량을 쓰지 않으려고 둔다.

- `DATA_EXTERNAL_MODE`
  - `live`(기본): 실제로 부른다
  - `record`: 실제로 부르고 응답 본문을 `DATA_REPLAY_DIR`에 저장한다
  - `replay`: 부르지 않고 저장된 응답만 쓴다. 없으면 `ConnectorUnavailable("REPLAY_MISS")`
    (지어내지 않는다). 할당량·하루 호출 수를 세지 않는다
- 저장 키 = 서비스 + 주소 + 요청 본문(또는 쿼리) 해시.
  **키·토큰은 헤더로만 가므로 파일에 들어가지 않는다**
- 저장 폴더 기본 `backend/.data/replay/` (커밋하지 않는다)
README "외부 호출 없이 확인하기".
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from data_agent.connectors import ConnectorUnavailable

MODE_ENV = "DATA_EXTERNAL_MODE"
DIR_ENV = "DATA_REPLAY_DIR"
DEFAULT_DIR = Path(__file__).resolve().parents[5] / ".data" / "replay"  # backend/.data/replay
MODES = ("live", "record", "replay")


def mode(env: Mapping[str, str] | None = None) -> str:
    value = ((env if env is not None else os.environ).get(MODE_ENV) or "live").strip().lower()
    return value if value in MODES else "live"


def _dir(env: Mapping[str, str] | None) -> Path:
    raw = (env if env is not None else os.environ).get(DIR_ENV)
    return Path(raw) if raw else DEFAULT_DIR


def key(service: str, url: str, request: Any) -> str:
    text = json.dumps([service, url, request], ensure_ascii=False, sort_keys=True, default=str)
    return f"{service}-{hashlib.sha256(text.encode('utf-8')).hexdigest()[:24]}"


def load(service: str, url: str, request: Any, env: Mapping[str, str] | None = None) -> Any:
    """저장된 응답 본문. 없으면 REPLAY_MISS."""
    path = _dir(env) / f"{key(service, url, request)}.json"
    if not path.is_file():
        raise ConnectorUnavailable(
            "REPLAY_MISS",
            f"저장된 {service} 응답이 없어 부르지 않음 (replay 모드, 먼저 record로 저장)",
        )
    return json.loads(path.read_text(encoding="utf-8"))["response"]


def save(
    service: str, url: str, request: Any, response: Any, env: Mapping[str, str] | None = None
) -> Path:
    """응답 본문을 저장한다 (요청은 주소·본문만, 헤더·키는 넣지 않는다)."""
    folder = _dir(env)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{key(service, url, request)}.json"
    record = {"service": service, "url": url, "request": request, "response": response}
    path.write_text(json.dumps(record, ensure_ascii=False, default=str), encoding="utf-8")
    return path
