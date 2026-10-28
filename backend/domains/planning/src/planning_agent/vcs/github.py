"""GitHub 연결 부품 (FR-AG-008, IF-EXT-001). REST v3를 `httpx.AsyncClient`로 부른다.

- 대상 레포 하나(`owner/name`)만 본다. 주소를 바꾸면 GitHub Enterprise도 된다.
- 필요한 권한: contents 읽기·쓰기, pull requests 읽기·쓰기, checks·commit statuses 읽기.
- ③ 커밋은 git data API(ref → blob → tree → commit → ref)로 만든다. 브랜치는 `gen/`만.
- 토큰은 요청 헤더에만 넣는다. 로그·오류 메시지·repr에 남기지 않는다.
- HTTP 오류는 `VcsError(code, message)`로 바꾼다.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from planning_agent.vcs.base import (
    Capabilities,
    Change,
    ChangedFile,
    CheckRun,
    Checks,
    CheckState,
    OpenedChange,
    TraceMeta,
    VcsError,
    check_branch,
    combine_checks,
    parse_trace,
)

DEFAULT_BASE_URL = "https://api.github.com"
MERGE_METHODS = ("merge", "squash", "rebase")
PER_PAGE = 100
MAX_PAGES = 10
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def _time(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else None


def added_lines(patch: str | None) -> tuple[int, ...]:
    """unified diff에서 새 파일 기준으로 더한 줄 번호를 꺼낸다."""
    if not patch:
        return ()
    out: list[int] = []
    line = 0
    for row in patch.splitlines():
        m = _HUNK.match(row)
        if m:
            line = int(m.group(1))
            continue
        if row.startswith("+"):
            out.append(line)
            line += 1
        elif row.startswith(("-", "\\")):
            continue
        else:
            line += 1
    return tuple(out)


def _run_state(run: dict) -> CheckState:
    if run.get("status") != "completed":
        return "pending"
    return "passed" if run.get("conclusion") in ("success", "neutral", "skipped") else "failed"


def _status_state(raw: str | None) -> CheckState:
    if raw == "success":
        return "passed"
    if raw == "pending":
        return "pending"
    return "failed"


class GithubVcsAdapter:
    """GitHub REST v3 연결 부품. 테스트는 `client`에 `httpx.MockTransport`를 넣는다."""

    def __init__(
        self,
        repo: str,
        token: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        base_branch: str = "dev",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ):
        if not _REPO.match(repo or ""):
            raise VcsError("config", "레포는 owner/name 형식이어야 한다")
        if not token:
            raise VcsError("config", "토큰이 비어 있다")
        self.repo = repo
        self.base_url = base_url.rstrip("/")
        self.base_branch = base_branch
        self.__token = token
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._caps = Capabilities(kind="github")

    def __repr__(self) -> str:
        return (
            f"GithubVcsAdapter(repo={self.repo!r}, base_url={self.base_url!r}, "
            f"base_branch={self.base_branch!r})"
        )

    @property
    def capabilities(self) -> Capabilities:
        return self._caps

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---- HTTP

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.__token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _clean(self, text: str) -> str:
        return text.replace(self.__token, "***")

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}/repos/{self.repo}{path}"
        where = f"{method} {path}"
        try:
            res = await self._client.request(
                method, url, json=json, params=params, headers=self._headers()
            )
        except httpx.HTTPError as exc:
            # 예외 문구에 요청 정보가 섞일 수 있어 이름만 남긴다
            raise VcsError("unavailable", f"{where} 연결 실패 ({type(exc).__name__})") from None
        if res.status_code < 400:
            return res.json() if res.content else None
        try:
            body = res.json()
            detail = str(body.get("message", "")) if isinstance(body, dict) else ""
        except ValueError:
            detail = ""
        message = self._clean(f"{where} → {res.status_code} {detail}".rstrip())
        raise VcsError(self._code(res, detail), message)

    @staticmethod
    def _code(res: httpx.Response, detail: str) -> str:
        status = res.status_code
        low = detail.lower()
        if status == 401:
            return "unauthorized"
        if status == 429 or (
            status == 403
            and (res.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in low)
        ):
            return "rate_limited"
        if status == 403:
            return "forbidden"
        if status == 404:
            return "not_found"
        if status in (405, 409) or (status == 422 and "already exists" in low):
            return "conflict"
        if status in (400, 422):
            return "invalid"
        return "unavailable"

    async def _pages(self, path: str, params: dict | None = None) -> list[dict]:
        out: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            rows = await self._call(
                "GET", path, params={**(params or {}), "per_page": PER_PAGE, "page": page}
            )
            items = rows if isinstance(rows, list) else []
            out.extend(items)
            if len(items) < PER_PAGE:
                break
        return out

    # ---- 변환

    @staticmethod
    def _change(pr: dict, files: list[dict]) -> Change:
        return Change(
            number=int(pr["number"]),
            title=pr.get("title") or "",
            branch=(pr.get("head") or {}).get("ref", ""),
            base=(pr.get("base") or {}).get("ref", ""),
            merged=bool(pr.get("merged") or pr.get("merged_at")),
            merged_at=_time(pr.get("merged_at")),
            url=pr.get("html_url"),
            files=tuple(
                ChangedFile(
                    path=f["filename"],
                    additions=int(f.get("additions", 0)),
                    deletions=int(f.get("deletions", 0)),
                    lines=added_lines(f.get("patch")),
                )
                for f in files
            ),
        )

    # ---- 공통 인터페이스

    async def find_changes(
        self, since: datetime, until: datetime, path_prefix: str | None = None
    ) -> list[Change]:
        found: list[Change] = []
        for page in range(1, MAX_PAGES + 1):
            rows = await self._call(
                "GET",
                "/pulls",
                params={
                    "state": "closed",
                    "base": self.base_branch,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            rows = rows if isinstance(rows, list) else []
            for pr in rows:
                merged_at = _time(pr.get("merged_at"))
                if merged_at is None or not since <= merged_at <= until:
                    continue
                files = await self._pages(f"/pulls/{int(pr['number'])}/files")
                change = self._change(pr, files)
                if path_prefix is None or any(
                    p.startswith(path_prefix) for p in change.changed_paths
                ):
                    found.append(change)
            # 갱신 순이라 구간 시작보다 먼저 갱신된 PR이 나오면 더 볼 필요가 없다
            oldest = _time(rows[-1].get("updated_at")) if rows else None
            if len(rows) < PER_PAGE or (oldest is not None and oldest < since):
                break
        return sorted(found, key=lambda c: (c.merged_at, c.number), reverse=True)

    async def get_change(self, number: int) -> Change:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        files = await self._pages(f"/pulls/{int(number)}/files")
        return self._change(pr, files)

    async def open_change(
        self,
        branch: str,
        title: str,
        body: str,
        base: str | None = None,
        files: dict[str, str] | None = None,
    ) -> OpenedChange:
        check_branch(branch)
        if not files:
            raise VcsError("invalid", "커밋할 파일이 없다")
        base = base or self.base_branch
        ref = await self._call("GET", f"/git/ref/heads/{quote(base, safe='/')}")
        parent = ref["object"]["sha"]
        commit = await self._call("GET", f"/git/commits/{parent}")
        tree_items = []
        for path, content in files.items():
            blob = await self._call(
                "POST", "/git/blobs", json={"content": content, "encoding": "utf-8"}
            )
            tree_items.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        tree = await self._call(
            "POST", "/git/trees", json={"base_tree": commit["tree"]["sha"], "tree": tree_items}
        )
        new = await self._call(
            "POST",
            "/git/commits",
            json={"message": title, "tree": tree["sha"], "parents": [parent]},
        )
        await self._call(
            "POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": new["sha"]}
        )
        pr = await self._call(
            "POST", "/pulls", json={"title": title, "body": body, "head": branch, "base": base}
        )
        return OpenedChange(number=int(pr["number"]), url=pr.get("html_url"), branch=branch)

    async def merge_change(self, number: int, method: str = "squash") -> Change:
        if method not in MERGE_METHODS:
            raise VcsError("invalid", f"모르는 병합 방식: {method}")
        pr = await self._call("GET", f"/pulls/{int(number)}")
        if pr.get("merged") or pr.get("merged_at"):
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        await self._call("PUT", f"/pulls/{int(number)}/merge", json={"merge_method": method})
        return await self.get_change(number)

    async def get_trace_meta(self, number: int) -> TraceMeta:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        return parse_trace(int(pr["number"]), pr.get("title") or "", pr.get("body"))

    async def get_checks(self, number: int) -> Checks:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        sha = pr["head"]["sha"]
        # 개인용 토큰(fine-grained)에는 Checks 권한이 없다. 막히면 커밋 상태만으로 판단한다
        try:
            runs_raw = await self._call(
                "GET", f"/commits/{sha}/check-runs", params={"per_page": PER_PAGE}
            )
        except VcsError as exc:
            if exc.code not in ("forbidden", "not_found"):
                raise
            runs_raw = None
        status = await self._call("GET", f"/commits/{sha}/status")
        runs = [
            CheckRun(name=r.get("name", ""), state=_run_state(r))
            for r in (runs_raw or {}).get("check_runs", [])
        ]
        runs += [
            CheckRun(name=st.get("context", ""), state=_status_state(st.get("state")))
            for st in (status or {}).get("statuses", [])
        ]
        return combine_checks(runs)
