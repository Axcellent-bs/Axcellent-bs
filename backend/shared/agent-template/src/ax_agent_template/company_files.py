"""공통층 파일을 회사 폴더로 옮기기 (여러 회사 설계 1-4, 2026-11-20).

회사 연결·도구 연결·켠 서비스·변환기·비용 정책 파일을 `<회사 폴더>/catbooks/` 아래에 둔다.
지금은 회사가 캣북스 하나라 읽고 쓰는 회사는 늘 캣북스다(동작 그대로).

- 켜기: `AX_COMPANIES_DIR`을 정했을 때만(run_all.sh가 `.data/companies`로 정한다).
  안 정했으면 모든 파일이 예전 경로 그대로다. `AX_COMPANY_FOLDERS=off`면 정했어도 끈다(되돌리기)
- 파일마다 경로 환경변수(`AX_TOOL_CONNECTIONS_FILE` 등)를 정했으면 그 경로를 그대로 쓴다.
  이 판단은 부르는 모듈이 한다. 여기는 회사 폴더 쪽만 맡는다
- 이관(`company_file`): 옛 경로(`<회사 폴더의 부모>/<이름>`, 또는 부르는 쪽이 준 경로)에
  파일·폴더가 있고 새 경로에 없으면 처음 읽을 때 한 번 옮긴다.
  1) `<회사 폴더>/catbooks/.migrate.lock`을 O_EXCL로 잡는다(여러 프로세스가 같이 켜져도 하나만).
     회사 폴더는 0700, 잠금 파일은 0600으로 만든다
  2) 잠근 뒤 다시 확인하고, 옛 것을 새 폴더의 임시 이름으로 복사한 뒤 `os.replace`로 바꿔치기
     (딸린 파일 `.history.jsonl`은 있을 때만 먼저, 본 파일을 마지막에. 본 파일이 생기면 이관 끝)
  3) 옛 것은 지우지 않고 `<옛 이름>.moved`로 바꾼다(이미 있으면 `.moved.<시각>`)
  4) 로그 한 줄(`회사 폴더로 옮겼다`)
  실패하면(복사 오류·잠금 대기 초과) 옛 것을 그대로 두고 옛 경로를 돌려준다.
  다음 읽기에서 다시 해 본다
- 되돌리기(`restore_old`): `.env`에 `AX_COMPANY_FOLDERS=off`만 넣고 다시 켜면, 옛 경로에 파일이
  없고 회사 폴더에 있을 때 회사 폴더 → 옛 경로로 복사해 되살린다(이관 뒤 화면에서 바꾼 값까지).
  같은 잠금·임시 파일·`os.replace`로 하고, 회사 폴더 파일은 지우지 않는다. 로그 한 줄
  (`회사 폴더 값으로 옛 경로를 되살렸다`). 실패하면 경고 한 번, 옛 경로는 비어 있는 채로 읽는다
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ax_agent_template.companies import COMPANIES_DIR_ENV, companies_dir, company_path
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS

FOLDERS_ENV = "AX_COMPANY_FOLDERS"
_OFF = ("off", "0", "false", "no")
MOVED_SUFFIX = ".moved"
HISTORY_SUFFIX = ".history.jsonl"
LOCK_NAME = ".migrate.lock"
# 잠금: LOCK_STALE초 지난 잠금은 죽은 프로세스가 남긴 것으로 보고 지운다. LOCK_TIMEOUT초 기다려도
# 못 잡으면 이번에는 옛 경로를 쓴다
LOCK_STALE = 60.0
LOCK_TIMEOUT = 10.0
DIR_MODE = 0o700

logger = logging.getLogger("ax.company_files")
_warned: set[str] = set()


def reset_for_tests() -> None:
    _warned.clear()


def folders_off() -> bool:
    """`AX_COMPANY_FOLDERS=off`로 회사 폴더를 껐는가(되돌리기)."""
    return os.getenv(FOLDERS_ENV, "").strip().lower() in _OFF


def folders_on() -> bool:
    """회사 폴더를 쓰는가. `AX_COMPANIES_DIR`을 정했고 `AX_COMPANY_FOLDERS=off`가 아닐 때."""
    return not folders_off() and bool(os.getenv(COMPANIES_DIR_ENV, "").strip())


def legacy_path(name: str) -> Path | None:
    """예전 기본 자리 `<회사 폴더의 부모>/<이름>` (run_all.sh의 `.data/<이름>`)."""
    root = companies_dir()
    return root.parent / name if root is not None else None


def _new_path(name: str, company_id: str) -> Path:
    new = company_path(company_id, name)
    if new is None:
        raise RuntimeError(f"{COMPANIES_DIR_ENV}가 없어 회사 폴더를 정할 수 없다")
    return new


def company_file(
    name: str,
    *,
    source: Path | None = None,
    extras: Sequence[str] = (),
    company_id: str = TENANT_ID_CATBOOKS,
) -> Path:
    """회사 폴더의 파일(폴더) 경로. 옛 것이 있으면 먼저 옮긴다. 옮기지 못했으면 옛 경로.

    `source`: 옛 경로(없으면 `legacy_path(name)`). `extras`: 같이 옮길 딸린 파일의 꼬리
    (예: `.history.jsonl`). `folders_on()`일 때만 부른다.
    """
    new = _new_path(name, company_id)
    old = source if source is not None else legacy_path(name)
    if old is None or new.exists() or not old.exists():
        return new
    return _migrate(old, new, extras)


def peek_company_file(
    name: str, *, source: Path | None = None, company_id: str = TENANT_ID_CATBOOKS
) -> Path:
    """옮기지 않고 지금 읽을 경로만. 아직 안 옮긴 옛 파일이 있으면 옛 경로, 아니면 회사 폴더.

    잠금을 기다리면 안 되는 곳(안전 관문 판정)용. 이관은 켤 때 `company_file`로 한 번 한다.
    """
    new = _new_path(name, company_id)
    old = source if source is not None else legacy_path(name)
    if old is not None and not new.exists() and old.exists():
        return old
    return new


def restore_old(
    name: str,
    *,
    target: Path,
    extras: Sequence[str] = (),
    company_id: str = TENANT_ID_CATBOOKS,
) -> bool:
    """off일 때 옛 경로(`target`)가 비어 있고 회사 폴더에 있으면 회사 폴더 → 옛 경로로 복사.

    되살렸으면 True. off가 아니거나, 옛 경로에 이미 있거나, 회사 폴더에 없거나, 실패하면 False.
    회사 폴더 파일은 지우지 않는다. 딸린 파일은 회사 폴더에 있고 옛 경로에 없을 때만 복사한다.
    """
    if not folders_off() or companies_dir() is None or target.exists():
        return False
    src = _new_path(name, company_id)
    if not src.exists():
        return False
    lock = src.parent / LOCK_NAME
    token = _acquire(lock)
    if token is None:
        _warn_once(f"restore:{target}", "옛 경로를 되살리지 못했다(잠금 대기 초과): %s", target)
        return False
    try:
        if target.exists() or not src.exists():  # 다른 프로세스가 먼저 되살렸다
            return False
        pairs = [
            (s, d)
            for s, d in (
                (src.with_name(src.name + e), target.with_name(target.name + e)) for e in extras
            )
            if s.exists() and not d.exists()
        ]
        target.parent.mkdir(parents=True, exist_ok=True)
        _place([*pairs, (src, target)])
    except OSError as exc:
        _warn_once(f"restore:{target}", "옛 경로를 되살리지 못했다: %s (%s)", target, exc)
        return False
    finally:
        _release(lock, token)
    logger.info(
        "회사 폴더 값으로 옛 경로를 되살렸다: %s → %s (딸린 파일 %d개, 회사 폴더 파일은 그대로)",
        src,
        target,
        len(pairs),
    )
    return True


def moved_unrestored(target: Path | None) -> bool:
    """옛 경로가 비어 있고 `.moved`만 남았는가(off인데 되살리지 못한 상태)."""
    if target is None or target.exists():
        return False
    return any(target.parent.glob(f"{target.name}{MOVED_SUFFIX}*"))


def warn_unrestored(target: Path | None, what: str) -> bool:
    """`moved_unrestored`면 경고를 한 번 남기고 True."""
    if not moved_unrestored(target):
        return False
    _warn_once(
        f"unrestored:{target}",
        "%s 옛 경로에 파일이 없고 .moved만 있다. 회사 폴더 값으로 되살리지 못해 기본값을 쓴다: %s",
        what,
        target,
    )
    return True


def _pairs(old: Path, new: Path, extras: Sequence[str]) -> list[tuple[Path, Path]]:
    """옮길 (옛, 새) 짝. 딸린 파일은 있을 때만 먼저, 본 파일 마지막."""
    out = [(old.with_name(old.name + e), new.with_name(new.name + e)) for e in extras]
    return [(src, dst) for src, dst in out if src.exists()] + [(old, new)]


def _place(pairs: list[tuple[Path, Path]]) -> None:
    """짝마다 임시 이름으로 복사한 뒤 `os.replace`로 바꿔치기. 마지막 짝이 본 파일.

    실패하면 임시 파일을 지우고 OSError를 그대로 올린다.
    """
    temps: list[Path] = []
    try:
        staged: list[tuple[Path, Path]] = []
        for src, dst in pairs:
            tmp = dst.with_name(f".{dst.name}.{uuid.uuid4().hex}.tmp")
            temps.append(tmp)
            _copy(src, tmp)
            staged.append((tmp, dst))
        for tmp, dst in staged:
            os.replace(tmp, dst)
        temps.clear()
    finally:
        for tmp in temps:
            try:
                if tmp.is_dir():
                    shutil.rmtree(tmp)
                elif tmp.exists():
                    tmp.unlink()
            except OSError:
                pass


def _moved_name(src: Path) -> Path:
    target = src.with_name(src.name + MOVED_SUFFIX)
    if not target.exists():
        return target
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return src.with_name(f"{src.name}{MOVED_SUFFIX}.{stamp}-{uuid.uuid4().hex[:6]}")


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, copy_function=shutil.copy2)
    else:
        shutil.copy2(src, dst)


def _private_dirs(folder: Path) -> None:
    """회사 폴더들(`<회사 폴더>`, `<회사 폴더>/<회사 ID>`)을 0700으로 만든다(이미 있으면 그대로)."""
    folder.parent.parent.mkdir(parents=True, exist_ok=True)
    folder.parent.mkdir(mode=DIR_MODE, exist_ok=True)
    folder.mkdir(mode=DIR_MODE, exist_ok=True)


def _acquire(lock: Path) -> str | None:
    """잠금을 잡으면 토큰, 시간 안에 못 잡으면 None."""
    token = uuid.uuid4().hex
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except (FileExistsError, PermissionError):
            pass  # 윈도: 지워지는 중인 잠금 파일은 PermissionError
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(token)
            return token
        try:
            stale = time.time() - lock.stat().st_mtime >= LOCK_STALE
        except OSError:
            stale = False  # 그사이 풀렸다
        if stale:
            try:
                lock.unlink()
            except OSError:
                pass
            continue
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.02)


def _release(lock: Path, token: str) -> None:
    try:
        if lock.read_text(encoding="utf-8") == token:
            lock.unlink()
    except OSError:
        pass


def _warn_once(key: str, message: str, *args: object) -> None:
    if key in _warned:
        return
    _warned.add(key)
    logger.warning(message, *args)


def _migrate(old: Path, new: Path, extras: Sequence[str]) -> Path:
    try:
        _private_dirs(new.parent)
    except OSError as exc:
        _warn_once(str(old), "회사 폴더로 옮기지 못했다, 옛 경로를 쓴다: %s (%s)", old, exc)
        return old
    lock = new.parent / LOCK_NAME
    token = _acquire(lock)
    if token is None:
        _warn_once(str(old), "회사 폴더 이관 잠금을 못 잡았다, 이번에는 옛 경로를 쓴다: %s", old)
        return old
    try:
        if new.exists() or not old.exists():  # 다른 프로세스가 먼저 옮겼다
            return new
        pairs = _pairs(old, new, extras)
        _place(pairs)
    except OSError as exc:
        _warn_once(str(old), "회사 폴더로 옮기지 못했다, 옛 경로를 쓴다: %s (%s)", old, exc)
        return old
    finally:
        _release(lock, token)
    moved: list[str] = []
    for src, _dst in pairs:
        target = _moved_name(src)
        try:
            src.rename(target)
            moved.append(target.name)
        except OSError as exc:  # 새 파일은 이미 있어 새 경로를 쓴다. 옛 파일은 원래 이름으로 남는다
            logger.warning("옛 파일 이름을 .moved로 바꾸지 못했다: %s (%s)", src, exc)
    logger.info(
        "회사 폴더로 옮겼다: %s → %s (딸린 파일 %d개, 옛 파일은 %s)",
        old,
        new,
        len(pairs) - 1,
        ", ".join(moved) or "원래 이름 그대로",
    )
    return new
