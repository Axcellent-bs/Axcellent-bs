"""GitHub 계정 연결 (GitHub App 설치 하나) → 레포는 자동으로 찾는다.

Claude·Vercel처럼 "GitHub 연결" 한 번으로 끝내고, 레포를 하나씩 등록하지 않는다.

1. 관리자가 앱 ID·설치 ID·앱 개인 키를 넣는다 (`set_account`). 레포 범위는 GitHub 설치 화면에서
   고른 레포까지다 ("선택한 레포만" 권장).
2. `refresh`가 설치 토큰(1시간, 고른 레포·권한까지만)으로 `GET /installation/repositories`를 읽어
   레포 목록과 기본 브랜치를 채운다.
3. 사람이 레포마다 "사용"을 켜야(`enable_repo`) 개발 AI가 쓴다. 켜면 기존 레포 연결
   (`vcs.upsert_connection`, auth=app)을 만들어서 레포 고르기·관문·연결 대장이 그대로 동작한다.
   설치 범위에 새 레포가 늘어도 사람이 켜기 전에는 쓰지 않는다 (몰래 넓어지지 않게).

보안
- 앱 개인 키는 이 프로세스 메모리와 저장 파일(`PLANNING_VCS_ACCOUNT_STORE`, 0600)에만 둔다.
  화면·응답·repr로 돌려주지 않는다 (넣었는지만).
- `warnings`: 모든 레포에 설치됨(repository_selection=all),
  필요 이상 권한(관리·비밀·워크플로 쓰기 등).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

import httpx

from dev_agent import vcs
from dev_agent.vcs.base import VcsError
from dev_agent.vcs.github import DEFAULT_BASE_URL
from dev_agent.vcs.github_app import GithubAppAuth

# 개발 AI에 필요한 권한. 이보다 넓은 쓰기 권한은 경고한다 (읽기는 괜찮다)
NEEDED = {"contents", "pull_requests", "metadata", "checks", "statuses", "issues", "actions"}
RISKY = {
    "administration": "레포 설정·삭제",
    "secrets": "비밀값",
    "workflows": "CI 워크플로 바꾸기",
    "members": "조직 멤버",
    "organization_administration": "조직 설정",
    "environments": "배포 환경·비밀",
    "repository_hooks": "웹훅",
}
_SLUG = re.compile(r"[^a-z0-9-]+")


@dataclass(frozen=True)
class Repo:
    full_name: str
    default_branch: str = "main"
    private: bool = True
    enabled: bool = False  # 사람이 켜야 개발 AI가 쓴다


@dataclass(frozen=True)
class Account:
    app_id: str
    installation_id: str
    private_key: str = field(repr=False)
    base_url: str = ""
    selection: str = ""  # all · selected (GitHub 설치 범위)
    permissions: dict[str, str] = field(default_factory=dict)
    repos: tuple[Repo, ...] = ()
    checked_at: str = ""
    error: str = ""

    def warnings(self) -> list[str]:
        out = []
        if self.selection == "all":
            out.append("모든 레포에 설치됨 → GitHub 설치 화면에서 '선택한 레포만'으로 좁히기")
        for name, level in sorted(self.permissions.items()):
            if name in RISKY and level in ("write", "admin"):
                out.append(f"필요 이상 권한: {name}={level} ({RISKY[name]})")
        return out

    def public(self) -> dict[str, Any]:
        return {
            "connected": True,
            "app_id": self.app_id,
            "installation_id": self.installation_id,
            "base_url": self.base_url,
            "key_set": bool(self.private_key),
            "selection": self.selection,
            "permissions": dict(self.permissions),
            "repos": [asdict(r) for r in self.repos],
            "checked_at": self.checked_at,
            "error": self.error,
            "warnings": self.warnings(),
        }


_account: Account | None = None
_client: httpx.AsyncClient | None = None  # 시험에서 바꿔 끼운다


def set_client(client: httpx.AsyncClient | None) -> None:
    global _client
    _client = client


def _store_path() -> str:
    explicit = os.getenv("PLANNING_VCS_ACCOUNT_STORE")
    if explicit:
        return explicit
    conns = os.getenv("PLANNING_VCS_STORE") or ""
    return os.path.join(os.path.dirname(conns), "vcs_account.json") if conns else ""


def _save() -> None:
    path = _store_path()
    if not path:
        return
    if _account is None:
        if os.path.exists(path):
            os.remove(path)
        return
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.chmod(tmp, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(asdict(_account), f, ensure_ascii=False)
    os.replace(tmp, path)


def load_saved() -> bool:
    global _account
    path = _store_path()
    if not path or not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        repos = tuple(Repo(**r) for r in raw.pop("repos", []))
        _account = Account(**{**raw, "repos": repos})
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return True


def public() -> dict[str, Any]:
    out = _account.public() if _account else {"connected": False, "repos": [], "warnings": []}
    return {**out, "app": app_config()}


# ---- AXcellent 앱으로 설치만 하기 (Claude·Vercel처럼)
# 운영팀이 GitHub App을 한 번 만들어 두면 고객사는 "GitHub 연결" → GitHub 설치 화면에서
# 레포 고르기로 끝난다. 앱 ID·키는 서버 설정이고
# (DEV_GITHUB_APP_ID · DEV_GITHUB_APP_SLUG · DEV_GITHUB_APP_KEY_FILE), 설치 ID는 GitHub이 설치 뒤
# Setup URL(연결 페이지)로 돌려준다. state(10분, 한 번만)로 우리가 보낸 설치인지 확인한다.

STATE_TTL = 600
_states: dict[str, float] = {}


def app_config() -> dict[str, Any]:
    slug = (os.getenv("DEV_GITHUB_APP_SLUG") or "").strip()
    ok = bool(slug and (os.getenv("DEV_GITHUB_APP_ID") or "").strip() and _app_key_path())
    return {"configured": ok, "slug": slug if ok else ""}


def _app_key_path() -> str:
    path = (os.getenv("DEV_GITHUB_APP_KEY_FILE") or "").strip()
    return path if path and os.path.exists(path) else ""


def install_url() -> str:
    """GitHub 설치 화면 주소 (state를 붙인다). 앱 설정이 없으면 VcsError(config)."""
    cfg = app_config()
    if not cfg["configured"]:
        raise VcsError(
            "config", "AXcellent GitHub App 설정이 없다 (DEV_GITHUB_APP_ID·SLUG·KEY_FILE)"
        )
    now = time.time()
    for k in [k for k, t in _states.items() if now - t > STATE_TTL]:
        _states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _states[state] = now
    web = (os.getenv("DEV_GITHUB_WEB_URL") or "https://github.com").rstrip("/")
    return f"{web}/apps/{cfg['slug']}/installations/new?state={state}"


async def finish_install(installation_id: str, state: str) -> Account:
    """GitHub 설치에서 돌아왔다: state를 확인하고 AXcellent 앱 + 이 설치로 연결한 뒤
    레포를 찾는다."""
    started = _states.pop(state or "", None)
    if started is None or time.time() - started > STATE_TTL:
        raise VcsError(
            "config",
            "연결 요청이 만료됐거나 우리가 보낸 것이 아니다. GitHub 연결을 다시 눌러 주세요",
        )
    path = _app_key_path()
    if not path:
        raise VcsError("config", "AXcellent GitHub App 키 파일이 없다 (DEV_GITHUB_APP_KEY_FILE)")
    with open(path, encoding="utf-8") as f:
        key = f.read()
    set_account(
        (os.getenv("DEV_GITHUB_APP_ID") or "").strip(),
        installation_id,
        key,
        (os.getenv("DEV_GITHUB_API_URL") or "").strip(),
    )
    return await refresh()


def account() -> Account | None:
    return _account


def connection_id(full_name: str) -> str:
    """이 레포로 만든 레포 연결의 id (예: gh-catbooks-shop-web)."""
    return ("gh-" + _SLUG.sub("-", full_name.lower()).strip("-"))[:40]


def set_account(
    app_id: str, installation_id: str, private_key: str | None, base_url: str = ""
) -> Account:
    """계정(앱 설치)을 넣거나 바꾼다. 키를 비우면 이전 키를 쓴다. 형식이 틀리면 VcsError(config)."""
    global _account
    key = (private_key or "").strip() or (_account.private_key if _account else "")
    if not key:
        raise VcsError("config", "GitHub App 개인 키(.pem)를 넣어 주세요")
    GithubAppAuth(app_id, installation_id, key)  # 형식 검사 (틀리면 VcsError)
    keep = _account if _account and _account.installation_id == installation_id.strip() else None
    _account = Account(
        app_id=app_id.strip(),
        installation_id=installation_id.strip(),
        private_key=key,
        base_url=(base_url or "").strip(),
        repos=keep.repos if keep else (),
    )
    _save()
    return _account


async def refresh() -> Account:
    """설치 토큰으로 레포 목록·기본 브랜치·권한·설치 범위를 읽는다. 켠 레포는 그대로 둔다."""
    global _account
    acc = _account
    if acc is None:
        raise VcsError("config", "GitHub 연결이 없다")
    base = (acc.base_url or DEFAULT_BASE_URL).rstrip("/")
    auth = GithubAppAuth(acc.app_id, acc.installation_id, acc.private_key)
    client = _client or httpx.AsyncClient(timeout=15)
    on = {r.full_name for r in acc.repos if r.enabled}
    try:
        token = await auth.token(client, base)
        head = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        repos: list[Repo] = []
        selection = ""
        for page in range(1, 11):  # 최대 1000개
            res = await client.get(f"{base}/installation/repositories", headers=head,
                                   params={"per_page": 100, "page": page})  # fmt: skip
            if res.status_code >= 400:
                raise VcsError("unavailable", f"GET /installation/repositories → {res.status_code}")
            body = res.json()
            selection = str(body.get("repository_selection") or selection)
            items = body.get("repositories") or []
            for r in items:
                name = str(r.get("full_name") or "")
                if name:
                    repos.append(Repo(name, str(r.get("default_branch") or "main"),
                                      bool(r.get("private", True)), name in on))  # fmt: skip
            if len(items) < 100:
                break
        _account = replace(
            acc, repos=tuple(repos), permissions=dict(auth.permissions), selection=selection,
            checked_at=datetime.now(UTC).isoformat(timespec="seconds"), error="",
        )  # fmt: skip
    except VcsError as exc:
        _account = replace(acc, error=exc.message if hasattr(exc, "message") else str(exc),
                           checked_at=datetime.now(UTC).isoformat(timespec="seconds"))  # fmt: skip
        _save()
        raise
    finally:
        if _client is None:
            await client.aclose()
    # 설치 범위에서 빠진 레포는 연결도 끈다
    names = {r.full_name for r in _account.repos}
    for gone in on - names:
        vcs.delete_connection(connection_id(gone))
    _save()
    return _account


def enable_repo(full_name: str, enabled: bool) -> Account:
    """사람이 레포 "사용"을 켜고 끈다. 켜면 기존 레포 연결(auth=app)을 만든다."""
    global _account
    acc = _account
    if acc is None:
        raise VcsError("config", "GitHub 연결이 없다")
    repo = next((r for r in acc.repos if r.full_name == full_name), None)
    if repo is None:
        raise VcsError("config", f"설치 범위에 없는 레포: {full_name} (먼저 목록 새로 고침)")
    cid = connection_id(full_name)
    if enabled:
        before = vcs.find_connection(cid)
        vcs.upsert_connection(
            id=cid, kind="github", repo=full_name, base_url=acc.base_url,
            base_branch=(before.base_branch if before else repo.default_branch),
            description=before.description if before else "",
            paths=list(before.paths) if before else [],
            mount=before.mount if before else "",
            auth="app", app_id=acc.app_id, installation_id=acc.installation_id,
            private_key=acc.private_key,
        )  # fmt: skip
    else:
        vcs.delete_connection(cid)
    repos = tuple(replace(r, enabled=enabled) if r.full_name == full_name else r for r in acc.repos)
    _account = replace(acc, repos=repos)
    _save()
    return _account


def clear_account() -> None:
    """계정 연결을 끊는다. 이 계정으로 켠 레포 연결도 지운다."""
    global _account
    if _account is not None:
        for r in _account.repos:
            if r.enabled:
                vcs.delete_connection(connection_id(r.full_name))
    _account = None
    _save()
