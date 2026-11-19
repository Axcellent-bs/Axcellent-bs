"""지금 회사: 시스템 관리 › 회사 연결에서 고른 회사의 설정 폴더(`config/tenants/<회사>/`).

캣북스 기본값은 없다. 회사를 연결하기 전에는 어느 회사도 아니다(`current()`가 None).
고르는 순서: `AX_TENANT`(설치하는 사람이 정한 값) → 회사 연결 파일의 `tenant` → 없음.

`setting(이름)`은 run_all이 내보내던 회사 설정 환경변수와 같은 이름으로 값을 준다.
환경변수를 정했으면 그것, 아니면 지금 회사 폴더 `tenant.toml`에서 찾는다(재시작 없이 따라감).
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

TENANT_ENV = "AX_TENANT"
TENANTS_DIR_ENV = "AX_TENANTS_DIR"
DEFAULT_TENANTS_DIR = Path(__file__).resolve().parents[4] / "config" / "tenants"
TENANT_FILE = "tenant.toml"
# 이 이름들은 환경변수가 없으면 회사 폴더에서 찾는다
DERIVED = (
    "AX_ORG_CONFIG",
    "DATA_TENANT_CATALOG",
    "DATA_TREND_CONFIG",
    "DATA_YOUTUBE_CONFIG",
    "DATA_TENANT_TIMEZONE",
    "DATA_TENANT_CONFIG",
    "FINANCE_PER_HEAD_MONTHLY",
)
# tenant.toml 키 → 회사 폴더 안 파일 경로인 것
_FILE_KEYS = {
    "AX_ORG_CONFIG": "org",
    "DATA_TREND_CONFIG": "trends",
    "DATA_YOUTUBE_CONFIG": "youtube",
}

logger = logging.getLogger("ax.tenant")
_cache: dict[Path, tuple[int, dict[str, Any]]] = {}


def tenants_dir() -> Path:
    """회사 폴더들이 있는 곳: `AX_TENANTS_DIR` → 레포 backend/config/tenants → 작업 폴더.

    도커 이미지는 패키지를 site-packages에 깔아(--no-editable) 레포 경로가 맞지 않는다.
    그때는 작업 폴더(WORKDIR /app = backend)의 config/tenants를 본다.
    """
    raw = os.getenv(TENANTS_DIR_ENV, "").strip()
    if raw:
        return Path(raw)
    if DEFAULT_TENANTS_DIR.is_dir():
        return DEFAULT_TENANTS_DIR
    return Path.cwd() / "config" / "tenants"


def _read(folder: Path) -> dict[str, Any] | None:
    path = folder / TENANT_FILE
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return None
    hit = _cache.get(path)
    if hit and hit[0] == stamp:
        return hit[1]
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("회사 설정을 읽지 못했다: %s (%s)", path, type(exc).__name__)
        return None
    _cache[path] = (stamp, raw)
    return raw


def list_tenants() -> list[dict[str, str]]:
    """고를 수 있는 회사: `tenant.toml`이 있는 폴더. [{id, name}]."""
    root = tenants_dir()
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir())
    except OSError:
        return []
    out = []
    for folder in folders:
        raw = _read(folder)
        if raw is not None:
            out.append({"id": folder.name, "name": str(raw.get("name") or folder.name)})
    return out


def is_tenant(name: str) -> bool:
    return (
        bool(name)
        and "/" not in name
        and "\\" not in name
        and name not in (".", "..")
        and (_read(tenants_dir() / name) is not None)
    )


def current() -> str | None:
    """지금 회사 id. 연결 전·모르는 회사면 None."""
    from ax_agent_template.company_connection import read_connection

    name = os.getenv(TENANT_ENV, "").strip()
    if not name:
        found = read_connection()
        name = (found.tenant or "") if found else ""
    if not name:
        name = _remembered()
    return name if is_tenant(name) else None


# 설치 기록: 회사 설정은 연결이 아니라 설치에 딸린다. 연결을 끊었다 다시 저장해도 유지되게
# 회사 연결 파일 옆 `install_tenant.json`에 마지막으로 정한 회사를 남긴다
# (11/18 연결 해제로 회사 설정이 사라짐)
INSTALL_TENANT_FILE = "install_tenant.json"


def _install_file() -> Path | None:
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(INSTALL_TENANT_FILE) if conn else None


def _remembered() -> str:
    import json

    path = _install_file()
    if path is None:
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    name = data.get("tenant") if isinstance(data, dict) else None
    return name if isinstance(name, str) else ""


def remember(name: str) -> None:
    """이 설치의 회사 설정을 남긴다(회사 연결을 저장할 때)."""
    import json

    path = _install_file()
    if path is None or not is_tenant(name):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"tenant": name}, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def setting(env_name: str) -> str:
    """회사 설정 값. 환경변수 → 지금 회사 폴더. 없으면 ""."""
    given = os.getenv(env_name, "").strip()
    if given or env_name not in DERIVED:
        return given
    name = current()
    if name is None:
        return ""
    folder = (tenants_dir() / name).resolve()
    raw = _read(folder) or {}
    if env_name in _FILE_KEYS:
        value = raw.get(_FILE_KEYS[env_name])
        if not isinstance(value, str) or not value:
            return ""
        path = (folder / value).resolve()
        return path.as_posix() if path.is_file() else ""
    if env_name == "DATA_TENANT_CATALOG":
        catalog = raw.get("catalog")
        if not isinstance(catalog, str):
            return ""
        return (folder / catalog).resolve().as_posix() if catalog.endswith(".toml") else catalog
    if env_name == "DATA_TENANT_TIMEZONE":
        tz = raw.get("timezone")
        return tz if isinstance(tz, str) else ""
    if env_name == "DATA_TENANT_CONFIG":
        return (folder / TENANT_FILE).as_posix()
    finance = raw.get("finance")
    per_head = finance.get("per_head_monthly") if isinstance(finance, dict) else None
    ok = isinstance(per_head, int) and not isinstance(per_head, bool) and per_head >= 0
    return str(per_head) if ok else ""


# ---------------------------------------------------------------- 인스타 레이더 계정
# 회사 연결 › 인스타 레이더 계정에서 저장한 목록.
# 있으면 회사 설정 trends.toml [instagram] radar_accounts보다 먼저다.
# 파일: `AX_IG_RADAR_FILE` → 회사 연결 파일 옆 `instagram_radar.json`.
# dashboard-api가 쓰고 data가 읽는다
IG_RADAR_FILE_ENV = "AX_IG_RADAR_FILE"
IG_RADAR_FILE = "instagram_radar.json"


def ig_radar_file() -> Path | None:
    raw = os.getenv(IG_RADAR_FILE_ENV, "").strip()
    if raw:
        return Path(raw)
    conn = os.getenv("AX_COMPANY_CONNECTION_FILE", "").strip()
    return Path(conn).with_name(IG_RADAR_FILE) if conn else None


def ig_radar_accounts() -> list[str] | None:
    """화면에서 저장한 레이더 계정. 저장한 적 없거나 깨졌으면 None(회사 설정 파일을 쓴다)."""
    import json

    path = ig_radar_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    accounts = data.get("accounts") if isinstance(data, dict) else None
    if not isinstance(accounts, list):
        return None
    return [a for a in accounts if isinstance(a, str) and a]
