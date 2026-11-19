"""공용 도구 연결 보관함: 화면에서 정한 외부 도구 연결(주소·토큰·개인 키)을 한 파일에 둔다.

- 저장: `AX_TOOL_CONNECTIONS_FILE`이 가리키는 JSON 파일 (연결 목록).
  쓰는 곳은 dashboard-api 한 곳이다 (예외: 개발 AI가 켤 때 예전 GitHub App 정보를 한 번 옮긴다,
  `dev_agent.vcs`).
  비밀 값이 들어 있어 `0600` 임시 파일에 쓰고 `os.replace`로 바꿔치기한다.
  쓰는 프로세스가 둘이라 읽고-고치고-쓰기는 `<파일>.lock`(O_EXCL로 만드는 잠금 파일)으로 막는다.
  임시 파일 이름은 쓸 때마다 다르다. 30초 넘은 잠금은 죽은 프로세스가 남긴 것으로 보고
  다른 이름으로 옮긴 뒤 다시 확인하고 지운다 (`_break_stale`).
- 읽기: 부를 때마다 바뀐 시각·크기로 확인해 읽는다 (재시작 없이 반영).
  파일이 없으면 연결이 없는 것이다(경고 없음). 깨졌으면 경고를 한 번 남기고 없는 것으로 본다.
- 비밀 값(`secrets`)은 repr·로그·오류 문구·이력 줄에 넣지 않는다. 화면에는 `public()`만 준다.
- 바꿀 때마다 `<파일>.history.jsonl`에 한 줄씩 쌓는다 (언제·누가·무엇을·id·종류·이름).
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILE_ENV = "AX_TOOL_CONNECTIONS_FILE"
# 잠금 파일: LOCK_STALE초 지난 것은 죽은 프로세스가 남긴 것으로 보고 지운다.
# LOCK_TIMEOUT초 기다려도 못 잡으면 busy
LOCK_STALE = 30.0
LOCK_TIMEOUT = 5.0
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
# 부르는 쪽이 config·secrets를 고쳐도 캐시가 바뀌지 않게 돌려줄 때마다 깊은 복사본을 준다
_cache: tuple[str, int, int, int, list[ToolConnection], bool] | None = None
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
        # st_ino: 같은 크기로 바뀐 시각 단위 안에 다시 쓴 파일(os.replace로 inode가 바뀐다)을 놓치지
        # 않는다. 윈도에서 st_ino가 0이면 예전처럼 시각·크기만 본다
        key = (path, stat.st_mtime_ns, stat.st_size, stat.st_ino)
        if _cache is not None and _cache[:4] == key:
            return copy.deepcopy(_cache[4]), _cache[5]
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
        return copy.deepcopy(found), broken


def read_all() -> list[ToolConnection]:
    """저장된 연결 전부. 경로가 없거나 파일이 없거나 깨졌으면 빈 목록."""
    return _load()[0]


def _lock_path() -> str:
    return connections_file() + ".lock"


def _is_stale(path: str) -> bool:
    try:
        return time.time() - os.stat(path).st_mtime > LOCK_STALE
    except OSError:
        return False  # 그사이 풀렸다: 마감·대기를 거쳐 다시 시도한다


def _break_stale(lock: str) -> bool:
    """오래된 잠금을 깬다. 깼으면 True.

    두 프로세스가 같은 잠금을 오래됐다고 보고 둘 다 지우면, 뒤에 지운 쪽이 그사이 다른
    프로세스가 새로 잡은 잠금을 지울 수 있다. 그래서 지우지 않고 저마다 다른 이름으로 옮긴 뒤
    (옮기기는 한 프로세스만 성공한다) 옮긴 파일이 여전히 오래됐는지 다시 본다.
    새 잠금을 옮겨 왔으면 잠금 자리가 비어 있을 때만 되돌린다(덮어쓰지 않는 os.link).
    되돌리지 못해도 그 잠금의 주인은 풀 때 표(`_release`)를 보고 남의 잠금을 지우지 않는다.
    """
    aside = f"{lock}.stale.{os.getpid()}.{uuid.uuid4().hex[:8]}"
    try:
        os.replace(lock, aside)
    except OSError:
        return False  # 다른 프로세스가 먼저 옮겼거나 윈도에서 쓰는 중: 기다렸다 다시 본다
    try:
        if _is_stale(aside):
            logger.warning("오래된 도구 연결 잠금 파일을 지운다: %s", lock)
            return True
        try:
            os.link(aside, lock)  # 남의 새 잠금: 자리가 비었을 때만 되돌린다
        except OSError:
            # 자리를 이미 다른 프로세스가 잡았거나 하드 링크를 못 만든다. 남의 잠금을
            # 덮어쓰지 않는 쪽을 택한다
            logger.warning("새 도구 연결 잠금을 되돌리지 못했다: %s", lock)
        return False
    finally:
        try:
            os.remove(aside)
        except OSError:
            pass


def _release(lock: str, token: str) -> None:
    """내 표가 적힌 잠금만 지운다.

    오래 붙잡아 남이 오래된 잠금으로 보고 깨고 새로 잡았으면, 그 자리의 잠금은 남의 것이라
    지우지 않는다. 읽고-지우기 사이 틈은 남지만 LOCK_STALE초 넘게 붙잡았을 때만 생긴다.
    """
    try:
        held = Path(lock).read_text(encoding="ascii", errors="replace")
    except OSError:
        return  # 이미 없다
    if held != token:
        logger.warning("도구 연결 잠금이 남의 것으로 바뀌어 지우지 않는다: %s", lock)
        return
    try:
        os.remove(lock)
    except OSError:
        pass


@contextmanager
def _writing() -> Iterator[None]:
    """읽고-고치고-쓰기 잠금: 프로세스 안(RLock)과 프로세스 사이(`<파일>.lock`)."""
    global _cache
    path = connections_file()
    if not path:
        raise ToolConnectionError("config", f"{FILE_ENV}가 설정되지 않아 저장할 수 없다")
    with _lock:
        lock = _lock_path()
        Path(lock).parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + LOCK_TIMEOUT
        delay = 0.01
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                pass
            except PermissionError:
                # 윈도: 지워지는 중인 잠금 파일은 PermissionError라 다시 시도한다.
                # 잠금 파일이 없는데도 거절되면 폴더 권한 문제라 바로 알린다
                if not os.path.exists(lock):
                    raise ToolConnectionError(
                        "config", "도구 연결 보관함 폴더에 쓸 수 없어요. 폴더 권한을 확인하세요"
                    ) from None
            if _is_stale(lock) and _break_stale(lock):
                continue  # 깼으면 바로 다시 잡는다. 못 깼으면 마감·대기를 거친다
            if time.monotonic() >= deadline:
                raise ToolConnectionError(
                    "busy", "다른 곳에서 도구 연결을 저장하는 중이에요. 잠시 뒤 다시 시도하세요"
                )
            time.sleep(delay)
            delay = min(delay * 2, 0.2)
        # 잠금마다 다른 표(pid.uuid)를 적어 둔다. 풀 때 표가 같을 때만 지운다
        token = f"{os.getpid()}.{uuid.uuid4().hex}"
        try:
            try:
                os.write(fd, token.encode("ascii"))
            finally:
                os.close(fd)
        except BaseException:
            try:
                os.remove(lock)  # 방금 O_EXCL로 만든 내 잠금이라 표 없이도 지운다
            except OSError:
                pass
            raise
        try:
            _cache = None  # 다른 프로세스가 막 쓴 것을 놓치지 않게 다시 읽는다
            yield
        finally:
            _release(lock, token)


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


def dejafix_proxy_headers() -> dict[str, str]:
    """dejafix-proxy를 부를 때 붙이는 헤더: 이 에이전트의 토큰(`agent_token(get_agent_name())`).

    프록시는 토큰이 없거나 모르는 토큰이면 401로 막는다. 이름·토큰이 없으면 `RuntimeError`.
    """
    from ax_agent_template.config import agent_token, get_agent_name

    return {"Authorization": f"Bearer {agent_token(get_agent_name())}"}


def _write(conns: list[ToolConnection]) -> None:
    global _cache
    path = connections_file()
    if not path:
        raise ToolConnectionError("config", f"{FILE_ENV}가 설정되지 않아 저장할 수 없다")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = f"{target}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"  # 쓰는 곳마다 다른 임시 파일
    # 토큰·개인 키가 들어 있어 만든 사용자만 읽는다. 처음부터 0600으로 만들고 권한을 다시 맞춘다
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # 윈도 등
    except BaseException:
        os.close(fd)  # fdopen 전에 실패: 직접 닫아야 지울 수 있다(윈도)
        _remove_quietly(tmp)
        raise
    try:
        # fdopen부터는 fd를 넘겨받은 것으로 본다. 실패해도 스스로 닫으므로 다시 닫지 않는다
        # (닫힌 번호는 다른 스레드가 바로 다시 쓸 수 있다)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump([c._row() for c in conns], f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except BaseException:
        _remove_quietly(tmp)
        raise
    _cache = None


def _remove_quietly(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


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
    with _writing():
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


def record_check(conn_id: str, ok: bool, detail: str, *, by: str = "system") -> ToolConnection:
    """확인 결과를 남긴다 (`detail`에는 비밀 값이 없어야 한다).

    이력에는 다른 변경처럼 `check` 한 줄만 남긴다 (결과·문구는 넣지 않는다).
    """
    with _writing():
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
        _record("check", saved, by)
        return saved


def delete(conn_id: str, *, by: str) -> bool:
    """연결을 지운다. 지웠으면 True, 원래 없었으면 False."""
    with _writing():
        conns = _read_for_write()
        before = next((c for c in conns if c.id == conn_id), None)
        if before is None:
            return False
        _write([c for c in conns if c.id != conn_id])
        _record("delete", before, by)
        return True
