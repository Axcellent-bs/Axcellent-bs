"""공용 도구 연결 보관함: 화면에서 정한 외부 도구 연결(주소·토큰·개인 키)을 한 파일에 둔다.

- 저장: `AX_TOOL_CONNECTIONS_FILE`이 가리키는 JSON 파일 (연결 목록).
  쓰는 곳은 dashboard-api 한 곳이다.
  비밀 값이 들어 있어 `0600` 임시 파일에 쓰고 `os.replace`로 바꿔치기한다.
- 읽기: 부를 때마다 바뀐 시각·크기로 확인해 읽는다 (재시작 없이 반영).
  파일이 없으면 연결이 없는 것이다(경고 없음). 깨졌으면 경고를 한 번 남기고 없는 것으로 본다.
- 비밀 값(`secrets`)은 repr·로그·오류 문구·이력 줄에 넣지 않는다. 화면에는 `public()`만 준다.
- 바꿀 때마다 `<파일>.history.jsonl`에 한 줄씩 쌓는다 (언제·누가·무엇을·id·종류·이름).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILE_ENV = "AX_TOOL_CONNECTIONS_FILE"
KINDS = ("github_app", "github_actions", "jenkins", "dejafix")

logger = logging.getLogger("ax.tool_connections")


class ToolConnectionError(Exception):
    """연결 오류. 메시지에 비밀 값을 넣지 않는다."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def mask(value: str, *, key: bool = False) -> str:
    """화면에 보일 가린 값. 개인 키는 `설정됨`, 나머지 비밀은 앞글자 없이 `••••`.

    목록은 회원 모두가 보므로 토큰·비밀번호의 일부도 내보내지 않는다.
    """
    return "설정됨" if key else "••••"


@dataclass(frozen=True)
class ToolConnection:
    """저장된 도구 연결 한 건."""

    id: str
    kind: str
    name: str
    config: dict[str, Any]
    secrets: dict[str, str] = field(repr=False)
    checked_at: str | None = None
    check_ok: bool | None = None
    check_detail: str | None = None
    updated_at: str | None = None
    updated_by: str | None = None

    def public(self) -> dict[str, Any]:
        """화면·응답용. 비밀 값 대신 가린 값만 담는다."""
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "config": dict(self.config),
            "secrets_set": {
                k: mask(v, key="private_key" in k or "PRIVATE KEY" in v)
                for k, v in self.secrets.items()
                if v
            },
            "checked_at": self.checked_at,
            "check_ok": self.check_ok,
            "check_detail": self.check_detail,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }

    def secret_values(self) -> list[str]:
        """오류 문구에서 가릴 값 목록."""
        return [v for v in self.secrets.values() if v]

    def _row(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "name": self.name,
            "config": self.config,
            "secrets": self.secrets,
            "checked_at": self.checked_at,
            "check_ok": self.check_ok,
            "check_detail": self.check_detail,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def connections_file() -> str:
    """보관함 파일 경로. 설정하지 않았으면 빈 문자열(연결 없음)."""
    return os.getenv(FILE_ENV, "").strip()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) else None


def _parse(raw: Any, path: str) -> list[ToolConnection]:
    if not isinstance(raw, list):
        raise ValueError(f"{path}: 맨 위가 목록이어야 한다")
    out: list[ToolConnection] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"{path}: 연결은 객체여야 한다")
        conn_id, kind, name = item.get("id"), item.get("kind"), item.get("name")
        config, secrets = item.get("config", {}), item.get("secrets", {})
        if not (isinstance(conn_id, str) and isinstance(kind, str) and isinstance(name, str)):
            raise ValueError(f"{path}: id·kind·name(문자열)이 필요하다")
        if not isinstance(config, dict) or not isinstance(secrets, dict):
            raise ValueError(f"{path}: config·secrets는 객체여야 한다")
        ok = item.get("check_ok")
        out.append(
            ToolConnection(
                id=conn_id,
                kind=kind,
                name=name,
                config=config,
                secrets={str(k): v for k, v in secrets.items() if isinstance(v, str)},
                checked_at=_text(item, "checked_at"),
                check_ok=ok if isinstance(ok, bool) else None,
                check_detail=_text(item, "check_detail"),
                updated_at=_text(item, "updated_at"),
                updated_by=_text(item, "updated_by"),
            )
        )
    return out


# 바뀐 시각·크기가 같으면 다시 읽지 않는다. 깨진 파일 경고도 같은 상태면 한 번만 남긴다
_cache: tuple[str, int, int, list[ToolConnection], bool] | None = None
_lock = threading.RLock()


def _load() -> tuple[list[ToolConnection], bool]:
    """(연결 목록, 파일이 있는데 읽지 못했는가)."""
    global _cache
    path = connections_file()
    if not path:
        return [], False
    with _lock:
        try:
            stat = os.stat(path)
        except OSError:
            return [], False  # 저장한 적 없음: 정상
        key = (path, stat.st_mtime_ns, stat.st_size)
        if _cache is not None and _cache[:3] == key:
            return list(_cache[3]), _cache[4]
        try:
            os.chmod(path, 0o600)  # 예전에 넓은 권한으로 만든 파일도 만든 사용자만 읽게 맞춘다
        except OSError:
            pass  # 윈도 등
        try:
            found = _parse(json.loads(Path(path).read_text(encoding="utf-8")), path)
        except (OSError, ValueError) as exc:  # json.JSONDecodeError는 ValueError다
            logger.warning("도구 연결 파일이 깨져 쓰지 않는다: %s (%s)", path, type(exc).__name__)
            found = []
            broken = True
        else:
            broken = False
        _cache = (*key, found, broken)
        return list(found), broken


def read_all() -> list[ToolConnection]:
    """저장된 연결 전부. 경로가 없거나 파일이 없거나 깨졌으면 빈 목록."""
    return _load()[0]


def _read_for_write() -> list[ToolConnection]:
    """쓰기 전에 읽는다. 파일이 깨졌으면 덮어써서 다른 연결을 잃지 않도록 거절한다."""
    conns, broken = _load()
    if broken:
        raise ToolConnectionError(
            "config",
            "보관함 파일을 읽을 수 없어 저장하지 않았어요. "
            "파일을 확인하거나 백업해 둔 뒤 다시 시도하세요",
        )
    return conns


def get(conn_id: str) -> ToolConnection | None:
    return next((c for c in read_all() if c.id == conn_id), None)


def find(kind: str, **config_match: Any) -> ToolConnection | None:
    """종류가 같고 config가 주어진 값과 모두 같은 첫 연결."""
    for conn in read_all():
        if conn.kind == kind and all(conn.config.get(k) == v for k, v in config_match.items()):
            return conn
    return None


def dejafix_proxy_url(env_name: str) -> str | None:
    """DejaFix 장애를 읽을 주소. 환경 변수(`env_name`)가 있으면 그것, 없으면 보관함에 `dejafix`가
    있을 때 dejafix-proxy 주소, 둘 다 없으면 None. 부를 때마다 다시 정한다(재시작 없이 반영)."""
    from ax_agent_template.config import agent_url  # config가 이 모듈을 쓰게 되어도 순환하지 않게

    explicit = os.getenv(env_name, "").strip().rstrip("/")
    if explicit:
        return explicit
    if find("dejafix") is not None:
        return agent_url("dejafix-proxy")
    return None


def _write(conns: list[ToolConnection]) -> None:
    global _cache
    path = connections_file()
    if not path:
        raise ToolConnectionError("config", f"{FILE_ENV}가 설정되지 않아 저장할 수 없다")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(target) + ".tmp"
    # 토큰·개인 키가 들어 있어 만든 사용자만 읽는다. 처음부터 0600으로 만들고 권한을 다시 맞춘다
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass  # 윈도 등
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([c._row() for c in conns], f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    _cache = None


def _history_path() -> Path:
    path = Path(connections_file())
    return path.with_name(path.name + ".history.jsonl")


def _record(action: str, conn: ToolConnection, by: str) -> None:
    """바꾼 기록 한 줄 (감사용). 비밀·config는 넣지 않는다. 쓰지 못해도 변경은 막지 않는다."""
    line = {
        "at": _now(),
        "by": by,
        "action": action,
        "id": conn.id,
        "kind": conn.kind,
        "name": conn.name,
    }
    try:
        with _history_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError as exc:
        logger.warning("도구 연결 기록을 남기지 못했다: %s", type(exc).__name__)


def _new_id(kind: str, conns: list[ToolConnection]) -> str:
    nums = [
        int(m.group(1))
        for c in conns
        if c.kind == kind and (m := re.fullmatch(re.escape(kind) + r"-(\d+)", c.id))
    ]
    return f"{kind}-{max(nums, default=0) + 1}"


def upsert(
    kind: str,
    name: str,
    config: dict[str, Any],
    secrets: dict[str, str],
    *,
    conn_id: str | None,
    by: str,
) -> ToolConnection:
    """연결을 만들거나 고친다. 빈 비밀 값은 이전 값을 그대로 둔다."""
    if kind not in KINDS:
        raise ToolConnectionError("invalid", f"모르는 연결 종류: {kind}")
    name = (name or "").strip()
    if not name:
        raise ToolConnectionError("invalid", "이름을 입력하세요")
    with _lock:
        conns = _read_for_write()
        before = next((c for c in conns if c.id == conn_id), None) if conn_id else None
        if conn_id and before is None:
            raise ToolConnectionError("not_found", "없는 연결이다")
        if before is not None and before.kind != kind:
            raise ToolConnectionError("invalid", "연결 종류는 바꿀 수 없다")
        merged = dict(before.secrets) if before else {}
        merged.update({k: v for k, v in secrets.items() if v})
        saved = ToolConnection(
            id=before.id if before else _new_id(kind, conns),
            kind=kind,
            name=name,
            config=dict(config),
            secrets=merged,
            updated_at=_now(),
            updated_by=by,
        )
        rows = [saved if c.id == saved.id else c for c in conns]
        if before is None:
            rows.append(saved)
        _write(rows)
        _record("save" if before is None else "update", saved, by)
        return saved


def record_check(conn_id: str, ok: bool, detail: str) -> ToolConnection:
    """확인 결과를 남긴다 (`detail`에는 비밀 값이 없어야 한다)."""
    with _lock:
        conns = _read_for_write()
        before = next((c for c in conns if c.id == conn_id), None)
        if before is None:
            raise ToolConnectionError("not_found", "없는 연결이다")
        saved = ToolConnection(
            **{
                **before.__dict__,
                "checked_at": _now(),
                "check_ok": ok,
                "check_detail": detail,
            }
        )
        _write([saved if c.id == conn_id else c for c in conns])
        return saved


def delete(conn_id: str, *, by: str) -> bool:
    """연결을 지운다. 지웠으면 True, 원래 없었으면 False."""
    with _lock:
        conns = _read_for_write()
        before = next((c for c in conns if c.id == conn_id), None)
        if before is None:
            return False
        _write([c for c in conns if c.id != conn_id])
        _record("delete", before, by)
        return True
