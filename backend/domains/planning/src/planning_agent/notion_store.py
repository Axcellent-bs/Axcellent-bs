"""노션 연결 토큰 (연결 페이지 › 문서 › 노션). 개발의 고객사 API 키(llm_key.py)와 같은 방식.

- 토큰은 이 프로세스 메모리와 저장 파일(`PLANNING_STATE_DIR`/notion_token.json, run_all.sh가
  git에 안 올라가는 backend/.data/에 둔다)에만 둔다. 화면·로그·상태 파일로는 돌려주지 않는다
  (넣었는지, 끝 4자리, 확인한 워크스페이스 이름만).
- 저장한 토큰이 없으면 서버 환경변수(`PLANNING_NOTION_TOKEN`·`NOTION_TOKEN`)를 쓴다.
- 넣고 지우기는 관리자만 (api.py `ADMIN`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

FILE_NAME = "notion_token.json"


@dataclass(frozen=True)
class NotionToken:
    token: str = field(repr=False)
    workspace: str = ""
    checked_at: str = ""  # ISO, 마지막으로 노션에서 확인한 시각


_saved: NotionToken | None = None
_loaded = False


class NotionTokenError(ValueError):
    """입력이 틀렸다 (메시지에 토큰 값을 넣지 않는다)."""


def _hint(key: str) -> str:
    return f"••••{key[-4:]}" if len(key) >= 12 else "••••"


def _path() -> str:
    base = os.getenv("PLANNING_STATE_DIR", "").strip()
    return os.path.join(base, FILE_NAME) if base else ""


def _save() -> None:
    path = _path()
    if not path:
        return
    if _saved is None:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(tmp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(_saved), f)
    os.replace(tmp, path)


def _load() -> None:
    global _saved, _loaded
    if _loaded:
        return
    _loaded = True
    path = _path()
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        _saved = NotionToken(
            token=str(raw["token"]),
            workspace=str(raw.get("workspace") or ""),
            checked_at=str(raw.get("checked_at") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError):
        _saved = None


def validate(token: str) -> str:
    key = (token or "").strip()
    if not key:
        raise NotionTokenError("노션 토큰을 넣어 주세요")
    if not key.isascii() or any(ch.isspace() for ch in key) or len(key) > 300:
        raise NotionTokenError(
            "토큰에 쓸 수 없는 글자(한글·공백 등)가 있어요. 다시 복사해 넣어 주세요"
        )
    return key


def saved_token() -> str:
    _load()
    return _saved.token if _saved else ""


def set_token(token: str, workspace: str) -> None:
    """노션에서 확인한 토큰을 저장한다 (확인은 api.py가 먼저 한다)."""
    global _saved, _loaded
    _loaded = True
    now = datetime.now(UTC).isoformat()
    _saved = NotionToken(token=validate(token), workspace=workspace, checked_at=now)
    _save()


def mark_checked(workspace: str) -> None:
    global _saved
    _load()
    if _saved is None:
        return
    now = datetime.now(UTC).isoformat()
    _saved = NotionToken(token=_saved.token, workspace=workspace, checked_at=now)
    _save()


def clear() -> None:
    global _saved, _loaded
    _loaded = True
    _saved = None
    _save()


def public() -> dict:
    """화면에 보여 줄 모양 (토큰 값은 넣지 않는다).
    `oauth_ready` = 노션 계정 연결 버튼을 켤 수 있는지."""
    from planning_agent import notion_oauth  # 순환 import 피하기

    return {**_public(), "oauth_ready": notion_oauth.ready()}


def _public() -> dict:
    _load()
    env = (os.getenv("PLANNING_NOTION_TOKEN") or os.getenv("NOTION_TOKEN") or "").strip()
    if _saved is not None:
        return {
            "connected": True,
            "source": "saved",
            "hint": _hint(_saved.token),
            "workspace": _saved.workspace,
            "checked_at": _saved.checked_at or None,
        }
    if env:
        return {"connected": True, "source": "env", "hint": _hint(env), "workspace": "",
                "checked_at": None}  # fmt: skip
    return {"connected": False, "source": "none", "hint": "", "workspace": "", "checked_at": None}


def reset_for_tests() -> None:
    global _saved, _loaded
    _saved = None
    _loaded = False
