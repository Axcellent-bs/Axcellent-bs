"""GitHub 연결 부품 (FR-AG-008, IF-EXT-001). REST v3를 `httpx.AsyncClient`로 부른다.

- 대상 레포 하나(`owner/name`)만 본다. 주소를 바꾸면 GitHub Enterprise도 된다.
- 필요한 권한: contents 읽기·쓰기, pull requests 읽기·쓰기, checks·commit statuses 읽기.
- ③ 커밋은 git data API(ref → blob → tree → commit → ref)로 만든다. 브랜치는 `gen/`만.
  ③-2 수정 커밋은 PR 브랜치 끝에 같은 방법으로 커밋을 더하고 ref를 옮긴다.
- 파일 목록은 git trees API(재귀), 파일 글은 contents API(base64)로 읽는다. 100KB 넘는 파일·
  바이너리는 읽지 않는다.
- 토큰은 요청 헤더에만 넣는다. 로그·오류 메시지·repr에 남기지 않는다.
- HTTP 오류는 `VcsError(code, message)`로 바꾼다.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import re
from dataclasses import replace
from datetime import datetime
from typing import Any
from urllib.parse import quote

import httpx

from planning_agent.vcs.base import (
    BRANCH_LIMIT,
    BRANCH_PREFIX,
    CHANGE_LIST_LIMIT,
    COMMENT_LIMIT,
    COMMIT_LIMIT,
    DIFF_MAX_FILES,
    GRAPH_BRANCHES,
    GRAPH_LIMIT,
    GRAPH_MAX,
    GRAPH_MESSAGE_CHARS,
    ISSUE_LIST_LIMIT,
    ISSUE_STATES,
    LIST_LIMIT,
    MAX_READ_BYTES,
    Branch,
    Capabilities,
    Change,
    ChangedFile,
    ChangeSummary,
    CheckRun,
    Checks,
    CheckState,
    Comment,
    Commit,
    CommitDetail,
    Diff,
    FileDiff,
    Graph,
    GraphBranch,
    GraphCommit,
    GraphPr,
    IssueSummary,
    OpenedChange,
    TraceMeta,
    VcsError,
    cap_diff,
    check_branch,
    check_path,
    check_sha,
    combine_checks,
    decode_text,
    match_issue,
    match_query,
    order_commits,
    parse_trace,
    state_of,
)
from planning_agent.vcs.github_app import GithubAppAuth

DEFAULT_BASE_URL = "https://api.github.com"
MERGE_METHODS = ("merge", "squash", "rebase")
PER_PAGE = 100
MAX_PAGES = 10
# 레포 작업 공간: 목록은 3쪽(300개)까지 훑고, CI 점·브랜치 비교는 몇 개만 더 부른다
LIST_PAGES = 3
LIST_CHECKS = 20
BRANCH_COMPARE = 30
# 커밋 그래프: 브랜치별 커밋 목록을 동시에 몇 개까지 부를지
GRAPH_PARALLEL = 8
MAX_COMMENT_CHARS = 65_000
# GitHub 화면의 Revert 버튼과 같은 길 (REST에는 없다)
_REVERT = """
mutation($id: ID!, $title: String) {
  revertPullRequest(input: {pullRequestId: $id, title: $title}) {
    revertPullRequest { number url headRefName }
  }
}
"""
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
        token: str = "",
        *,
        app: GithubAppAuth | None = None,
        base_url: str = DEFAULT_BASE_URL,
        base_branch: str = "dev",
        client: httpx.AsyncClient | None = None,
        timeout: float = 15.0,
    ):
        if not _REPO.match(repo or ""):
            raise VcsError("config", "레포는 owner/name 형식이어야 한다")
        if not token and app is None:
            raise VcsError("config", "토큰이 비어 있다")
        self.repo = repo
        self.base_url = base_url.rstrip("/")
        self.base_branch = base_branch
        self.__token = token
        # GitHub App 연결이면 사람 토큰 대신 설치 토큰을 받아 쓴다 (github_app.py)
        self._app = app
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._caps = Capabilities(kind="github", issues=True)

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

    async def _auth(self) -> str:
        if self._app is not None:
            return await self._app.token(self._client, self.base_url)
        return self.__token

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _clean(self, text: str) -> str:
        for secret in [self.__token, *(self._app.secrets() if self._app else [])]:
            if secret:
                text = text.replace(secret, "***")
        return text

    async def _call(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        url = f"{self.base_url}/repos/{self.repo}{path}"
        return await self._send(method, url, f"{method} {path}", json=json, params=params)

    async def _send(
        self,
        method: str,
        url: str,
        where: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> Any:
        token = await self._auth()
        try:
            res = await self._client.request(
                method, url, json=json, params=params, headers=self._headers(token)
            )
        except UnicodeEncodeError:
            # 토큰·주소에 쓸 수 없는 글자가 있으면 머리글을 못 만든다. 값은 남기지 않는다
            raise VcsError("config", f"{where} 토큰이나 주소에 쓸 수 없는 글자가 있다") from None
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

    async def find_change_by_branch(self, branch: str) -> Change | None:
        owner = self.repo.split("/", 1)[0]
        pulls = await self._call(
            "GET", "/pulls", params={"head": f"{owner}:{branch}", "state": "all", "per_page": 5}
        )
        if not pulls:
            return None
        newest = max(pulls, key=lambda p: p.get("number", 0))
        return await self.get_change(int(newest["number"]))

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
        parent = await self._head(base)
        new = await self._commit(parent, files, title)
        await self._call("POST", "/git/refs", json={"ref": f"refs/heads/{branch}", "sha": new})
        pr = await self._call(
            "POST", "/pulls", json={"title": title, "body": body, "head": branch, "base": base}
        )
        return OpenedChange(number=int(pr["number"]), url=pr.get("html_url"), branch=branch)

    async def _head(self, branch: str) -> str:
        ref = await self._call("GET", f"/git/ref/heads/{quote(branch, safe='/')}")
        return ref["object"]["sha"]

    async def _commit(self, parent: str, files: dict[str, str], message: str) -> str:
        """parent 위에 파일을 올린 커밋을 만들고 sha를 돌려준다 (ref는 옮기지 않는다)."""
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
            json={"message": message, "tree": tree["sha"], "parents": [parent]},
        )
        return new["sha"]

    async def update_change(self, number: int, files: dict[str, str], message: str) -> OpenedChange:
        if not files:
            raise VcsError("invalid", "커밋할 파일이 없다")
        pr = await self._call("GET", f"/pulls/{int(number)}")
        if pr.get("merged") or pr.get("merged_at"):
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        branch = check_branch((pr.get("head") or {}).get("ref", ""))
        parent = await self._head(branch)
        new = await self._commit(parent, files, message)
        await self._call(
            "PATCH", f"/git/refs/heads/{quote(branch, safe='/')}", json={"sha": new, "force": False}
        )
        return OpenedChange(number=int(pr["number"]), url=pr.get("html_url"), branch=branch)

    async def list_files(self, prefix: str | None = None, limit: int = LIST_LIMIT) -> list[str]:
        head = await self._head(self.base_branch)
        tree = await self._call("GET", f"/git/trees/{head}", params={"recursive": "1"})
        rows = sorted(
            str(item.get("path", ""))
            for item in (tree or {}).get("tree", [])
            if item.get("type") == "blob"
            and (prefix is None or str(item.get("path", "")).startswith(prefix))
        )
        return rows[: max(0, limit)]

    async def read_file(self, path: str, ref: str | None = None) -> str:
        path = check_path(path)
        got = await self._call(
            "GET", f"/contents/{quote(path, safe='/')}", params={"ref": ref or self.base_branch}
        )
        if not isinstance(got, dict) or got.get("type") != "file":
            raise VcsError("invalid", f"파일이 아니다: {path}")
        if int(got.get("size") or 0) > MAX_READ_BYTES:
            raise VcsError("invalid", f"파일이 너무 크다 ({got.get('size')}바이트): {path}")
        if got.get("encoding") != "base64":
            raise VcsError("invalid", f"읽을 수 없는 인코딩: {path}")
        try:
            data = base64.b64decode(str(got.get("content") or ""), validate=False)
        except (binascii.Error, ValueError):
            raise VcsError("invalid", f"내용을 풀지 못함: {path}") from None
        return decode_text(data, path)

    async def close_change(self, number: int) -> None:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        if pr.get("merged") or pr.get("merged_at"):
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        if pr.get("state") != "closed":
            await self._call("PATCH", f"/pulls/{int(number)}", json={"state": "closed"})
        branch = (pr.get("head") or {}).get("ref") or ""
        if branch.startswith(BRANCH_PREFIX):  # 에이전트가 만든 브랜치만 지운다
            try:
                await self._call("DELETE", f"/git/refs/heads/{quote(branch, safe='/')}")
            except VcsError as exc:
                if exc.code not in ("not_found", "invalid"):
                    raise

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
        return await self._checks_sha(pr["head"]["sha"])

    async def _checks_sha(self, sha: str) -> Checks:
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

    # ---- 레포 작업 공간

    @staticmethod
    def _summary(
        pr: dict, *, checks: CheckState | None = None, comments: int | None = None
    ) -> ChangeSummary:
        merged = bool(pr.get("merged") or pr.get("merged_at"))
        raw_comments = pr.get("comments")
        return ChangeSummary(
            number=int(pr["number"]),
            title=pr.get("title") or "",
            state=state_of(merged, pr.get("state") == "closed"),
            branch=(pr.get("head") or {}).get("ref", ""),
            base=(pr.get("base") or {}).get("ref", ""),
            author=(pr.get("user") or {}).get("login", "") or "",
            body=pr.get("body") or "",
            draft=bool(pr.get("draft")),
            created_at=_time(pr.get("created_at")),
            updated_at=_time(pr.get("updated_at")),
            merged_at=_time(pr.get("merged_at")),
            closed_at=_time(pr.get("closed_at")),
            url=pr.get("html_url"),
            comments=raw_comments if isinstance(raw_comments, int) else comments,
            checks=checks,
        )

    async def _safe_checks(self, sha: str) -> CheckState | None:
        try:
            return (await self._checks_sha(sha)).state
        except VcsError:
            return None

    async def list_changes(
        self, state: str = "open", query: str | None = None, limit: int = CHANGE_LIST_LIMIT
    ) -> list[ChangeSummary]:
        if state not in ("open", "merged", "closed", "all"):
            raise VcsError("invalid", f"모르는 상태: {state}")
        limit = max(1, min(int(limit), PER_PAGE))
        api_state = {"open": "open", "merged": "closed", "closed": "closed"}.get(state, "all")
        raw: list[dict] = []
        for page in range(1, LIST_PAGES + 1):
            rows = await self._call(
                "GET",
                "/pulls",
                params={
                    "state": api_state,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            rows = rows if isinstance(rows, list) else []
            for pr in rows:
                c = self._summary(pr)
                if state in ("merged", "closed") and c.state != state:
                    continue
                if match_query(c, query):
                    raw.append(pr)
                if len(raw) >= limit:
                    break
            if len(raw) >= limit or len(rows) < PER_PAGE:
                break
        counts = await self._comment_counts(api_state)
        shas = [
            sha
            for pr in raw
            if pr.get("state") == "open" and (sha := str((pr.get("head") or {}).get("sha") or ""))
        ][:LIST_CHECKS]
        got = await asyncio.gather(*(self._safe_checks(sha) for sha in shas))
        ci = dict(zip(shas, got, strict=True))
        return [
            self._summary(
                pr,
                checks=ci.get(str((pr.get("head") or {}).get("sha") or "")),
                comments=counts.get(int(pr["number"])),
            )
            for pr in raw
        ]

    async def _comment_counts(self, api_state: str) -> dict[int, int]:
        """대화 코멘트 수. PR 목록에는 없어 이슈 목록 한 쪽에서 읽는다 (없으면 빈 값)."""
        try:
            rows = await self._call(
                "GET",
                "/issues",
                params={
                    "state": api_state,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": PER_PAGE,
                },
            )
        except VcsError:
            return {}
        return {
            int(r["number"]): int(r.get("comments") or 0)
            for r in (rows if isinstance(rows, list) else [])
            if isinstance(r, dict) and r.get("pull_request") is not None and "number" in r
        }

    async def describe_change(self, number: int) -> ChangeSummary:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        sha = str((pr.get("head") or {}).get("sha") or "")
        return self._summary(pr, checks=await self._safe_checks(sha) if sha else None)

    async def get_diff(self, number: int) -> Diff:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        pages = -(-DIFF_MAX_FILES // PER_PAGE)
        files: list[dict] = []
        for page in range(1, pages + 1):
            rows = await self._call(
                "GET",
                f"/pulls/{int(number)}/files",
                params={"per_page": PER_PAGE, "page": page},
            )
            items = rows if isinstance(rows, list) else []
            files.extend(items)
            if len(items) < PER_PAGE:
                break
        diff = cap_diff(
            int(number),
            [
                FileDiff(
                    path=f.get("filename", ""),
                    status=f.get("status") or "modified",
                    additions=int(f.get("additions", 0)),
                    deletions=int(f.get("deletions", 0)),
                    patch=f.get("patch"),
                    previous_path=f.get("previous_filename"),
                )
                for f in files
            ],
        )
        total = int(pr.get("changed_files") or diff.total_files)
        return replace(diff, total_files=total, truncated=diff.truncated or total > len(files))

    async def list_commits(self, number: int, limit: int = COMMIT_LIMIT) -> list[Commit]:
        rows = await self._call(
            "GET", f"/pulls/{int(number)}/commits", params={"per_page": min(limit, PER_PAGE)}
        )
        out: list[Commit] = []
        for c in (rows if isinstance(rows, list) else [])[:limit]:
            info = c.get("commit") or {}
            who = info.get("author") or {}
            out.append(
                Commit(
                    sha=str(c.get("sha") or ""),
                    message=str(info.get("message") or "")[:2000],
                    author=(c.get("author") or {}).get("login") or who.get("name") or "",
                    at=_time(who.get("date")),
                )
            )
        return out

    @staticmethod
    def _comment(c: dict) -> Comment:
        return Comment(
            id=str(c.get("id") or ""),
            author=(c.get("user") or {}).get("login", "") or "",
            body=str(c.get("body") or ""),
            at=_time(c.get("created_at")),
        )

    async def list_comments(self, number: int, limit: int = COMMENT_LIMIT) -> list[Comment]:
        rows = await self._call(
            "GET", f"/issues/{int(number)}/comments", params={"per_page": min(limit, PER_PAGE)}
        )
        return [self._comment(c) for c in (rows if isinstance(rows, list) else [])[:limit]]

    async def add_comment(self, number: int, body: str) -> Comment:
        text = (body or "").strip()
        if not text or len(text) > MAX_COMMENT_CHARS:
            raise VcsError("invalid", "코멘트가 비었거나 너무 길다")
        got = await self._call("POST", f"/issues/{int(number)}/comments", json={"body": text})
        return self._comment(got or {})

    # ---- 이슈 (GitHub /issues는 PR도 섞어 주므로 pull_request가 있는 줄은 뺀다)

    @staticmethod
    def _issue(r: dict) -> IssueSummary:
        return IssueSummary(
            number=int(r["number"]),
            title=r.get("title") or "",
            state="closed" if r.get("state") == "closed" else "open",
            author=(r.get("user") or {}).get("login", "") or "",
            body=r.get("body") or "",
            labels=tuple(
                str(x.get("name") or "") for x in r.get("labels") or [] if isinstance(x, dict)
            ),
            assignees=tuple(
                str(x.get("login") or "") for x in r.get("assignees") or [] if isinstance(x, dict)
            ),
            comments=r.get("comments") if isinstance(r.get("comments"), int) else None,
            created_at=_time(r.get("created_at")),
            updated_at=_time(r.get("updated_at")),
            closed_at=_time(r.get("closed_at")),
            url=r.get("html_url"),
            reason=r.get("state_reason"),
        )

    async def list_issues(
        self, state: str = "open", query: str | None = None, limit: int = ISSUE_LIST_LIMIT
    ) -> list[IssueSummary]:
        if state not in ISSUE_STATES:
            raise VcsError("invalid", f"모르는 상태: {state}")
        limit = max(1, min(int(limit), PER_PAGE))
        out: list[IssueSummary] = []
        for page in range(1, LIST_PAGES + 1):
            rows = await self._call(
                "GET",
                "/issues",
                params={
                    "state": state,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            )
            rows = rows if isinstance(rows, list) else []
            for r in rows:
                if (
                    not isinstance(r, dict)
                    or r.get("pull_request") is not None
                    or "number" not in r
                ):
                    continue
                i = self._issue(r)
                if match_issue(i, query):
                    out.append(i)
                if len(out) >= limit:
                    return out
            if len(rows) < PER_PAGE:
                break
        return out

    async def describe_issue(self, number: int) -> IssueSummary:
        r = await self._call("GET", f"/issues/{int(number)}")
        if not isinstance(r, dict) or r.get("pull_request") is not None:
            raise VcsError("not_found", f"이슈가 아니다: {number}")
        return self._issue(r)

    async def reopen_change(self, number: int) -> ChangeSummary:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        if pr.get("merged") or pr.get("merged_at"):
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        if pr.get("state") != "open":
            pr = await self._call("PATCH", f"/pulls/{int(number)}", json={"state": "open"})
        return self._summary(pr)

    def _graphql_url(self) -> str:
        # 사내 설치형은 https://<호스트>/api/v3 → https://<호스트>/api/graphql
        if self.base_url.endswith("/api/v3"):
            return self.base_url[: -len("v3")] + "graphql"
        return self.base_url + "/graphql"

    async def _graphql(self, query: str, variables: dict) -> dict:
        got = await self._send(
            "POST",
            self._graphql_url(),
            "POST /graphql",
            json={"query": query, "variables": variables},
        )
        errors = got.get("errors") if isinstance(got, dict) else None
        if errors:
            kinds = {str(e.get("type") or "") for e in errors if isinstance(e, dict)}
            detail = "; ".join(str(e.get("message", "")) for e in errors if isinstance(e, dict))
            if "FORBIDDEN" in kinds:
                code = "forbidden"
            elif "NOT_FOUND" in kinds:
                code = "not_found"
            else:
                code = "invalid"
            raise VcsError(code, self._clean(f"POST /graphql → {detail}"[:300]))
        return (got.get("data") if isinstance(got, dict) else None) or {}

    async def revert_change(self, number: int, title: str | None = None) -> OpenedChange:
        pr = await self._call("GET", f"/pulls/{int(number)}")
        if not (pr.get("merged") or pr.get("merged_at")):
            raise VcsError("conflict", f"병합되지 않은 변경은 되돌리지 않는다: {number}")
        node = pr.get("node_id")
        if not node:
            raise VcsError("invalid", f"변경 {number}의 node_id가 없다")
        name = title or f'Revert "{pr.get("title") or number}"'
        data = await self._graphql(_REVERT, {"id": node, "title": name})
        made = (data.get("revertPullRequest") or {}).get("revertPullRequest") or {}
        if "number" not in made:
            raise VcsError("unavailable", "되돌리기 PR 번호를 받지 못했다")
        return OpenedChange(
            number=int(made["number"]), url=made.get("url"), branch=made.get("headRefName") or ""
        )

    async def _default_branch(self) -> str:
        repo = await self._call("GET", "")
        return str((repo or {}).get("default_branch") or "")

    async def _compare(self, name: str) -> tuple[int | None, int | None, datetime | None]:
        try:
            got = await self._call(
                "GET",
                f"/compare/{quote(self.base_branch, safe='')}...{quote(name, safe='')}",
            )
        except VcsError:
            return None, None, None
        got = got if isinstance(got, dict) else {}
        commits = got.get("commits") or []
        tip = commits[-1] if commits else got.get("merge_base_commit") or {}
        at = _time(((tip.get("commit") or {}).get("committer") or {}).get("date"))
        return got.get("ahead_by"), got.get("behind_by"), at

    async def _last_commit(self, sha: str) -> datetime | None:
        if not sha:
            return None
        try:
            got = await self._call("GET", f"/commits/{sha}")
        except VcsError:
            return None
        return _time((((got or {}).get("commit") or {}).get("committer") or {}).get("date"))

    async def list_branches(self, limit: int = BRANCH_LIMIT) -> list[Branch]:
        default = await self._default_branch()
        rows: list[dict] = []
        for page in range(1, -(-limit // PER_PAGE) + 1):
            got = await self._call("GET", "/branches", params={"per_page": PER_PAGE, "page": page})
            items = got if isinstance(got, list) else []
            rows.extend(items)
            if len(items) < PER_PAGE:
                break
        rows = rows[:limit]
        others = [r["name"] for r in rows if r.get("name") != self.base_branch][:BRANCH_COMPARE]
        found = await asyncio.gather(*(self._compare(n) for n in others))
        compared = dict(zip(others, found, strict=True))
        base_row = next((r for r in rows if r.get("name") == self.base_branch), None)
        base_at = (
            await self._last_commit(str((base_row.get("commit") or {}).get("sha") or ""))
            if base_row
            else None
        )
        out: list[Branch] = []
        for r in rows:
            name = str(r.get("name") or "")
            ahead, behind, at = compared.get(name, (None, None, None))
            if name == self.base_branch:
                ahead, behind, at = 0, 0, base_at
            out.append(
                Branch(
                    name=name,
                    sha=str((r.get("commit") or {}).get("sha") or ""),
                    protected=bool(r.get("protected")),
                    default=name == default,
                    last_commit_at=at,
                    ahead=ahead,
                    behind=behind,
                )
            )
        return out

    async def delete_branch(self, name: str) -> None:
        name = (name or "").strip()
        if not name or ".." in name or " " in name:
            raise VcsError("invalid", f"쓸 수 없는 브랜치 이름: {name}")
        if name == self.base_branch or name == await self._default_branch():
            raise VcsError("invalid", f"기준·기본 브랜치는 지우지 않는다: {name}")
        await self._call("DELETE", f"/git/refs/heads/{quote(name, safe='/')}")

    # ---- 커밋 그래프

    @staticmethod
    def _graph_commit(c: dict) -> GraphCommit:
        info = c.get("commit") or {}
        who = info.get("author") or {}
        committer = info.get("committer") or {}
        return GraphCommit(
            sha=str(c.get("sha") or ""),
            parents=tuple(
                str(p["sha"])
                for p in (c.get("parents") or [])
                if isinstance(p, dict) and p.get("sha")
            ),
            message=str(info.get("message") or "")[:GRAPH_MESSAGE_CHARS],
            author=(c.get("author") or {}).get("login") or who.get("name") or "",
            at=_time(committer.get("date") or who.get("date")),
        )

    async def _graph_pulls(self) -> list[GraphPr]:
        """최근 갱신된 PR 한 쪽(100개)만 본다. 막히면 꼬리표 없이 그린다."""
        try:
            rows = await self._call(
                "GET",
                "/pulls",
                params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 100},
            )
        except VcsError:
            return []
        out: list[GraphPr] = []
        for pr in rows if isinstance(rows, list) else []:
            if not isinstance(pr, dict) or "number" not in pr:
                continue
            merged = bool(pr.get("merged") or pr.get("merged_at"))
            head = pr.get("head") or {}
            out.append(
                GraphPr(
                    number=int(pr["number"]),
                    title=pr.get("title") or "",
                    state=state_of(merged, pr.get("state") == "closed"),
                    branch=str(head.get("ref") or ""),
                    head_sha=str(head.get("sha") or ""),
                    # 열린 PR의 merge_commit_sha는 시험 머지라 머지된 것만 쓴다
                    merge_sha=str(pr.get("merge_commit_sha") or "") if merged else "",
                )
            )
        return out

    async def _branch_commits(self, name: str, pages: int, sem: asyncio.Semaphore) -> list[dict]:
        out: list[dict] = []
        async with sem:
            for page in range(1, pages + 1):
                try:
                    got = await self._call(
                        "GET",
                        "/commits",
                        params={"sha": name, "per_page": PER_PAGE, "page": page},
                    )
                except VcsError as exc:
                    # 그사이 지운 브랜치·빈 레포
                    if exc.code in ("not_found", "invalid", "conflict"):
                        break
                    raise
                items = [c for c in (got if isinstance(got, list) else []) if isinstance(c, dict)]
                out.extend(items)
                if len(items) < PER_PAGE:
                    break
        return out

    async def list_graph(
        self, branches: list[str] | None = None, limit: int = GRAPH_LIMIT
    ) -> Graph:
        limit = max(1, min(int(limit), GRAPH_MAX))
        default, rows, prs = await asyncio.gather(
            self._default_branch(),
            self._call("GET", "/branches", params={"per_page": PER_PAGE}),
            self._graph_pulls(),
        )
        rows = [r for r in (rows if isinstance(rows, list) else []) if isinstance(r, dict)]
        rows = [r for r in rows if r.get("name")]
        main = (self.base_branch, default)
        rank = {n: i for i, n in enumerate(main)}
        rows.sort(key=lambda r: rank.get(str(r["name"]), len(main)))
        all_branches = tuple(
            GraphBranch(
                name=str(r["name"]),
                sha=str((r.get("commit") or {}).get("sha") or ""),
                default=str(r["name"]) == default,
            )
            for r in rows
        )
        names = [b.name for b in all_branches]
        chosen = set(branches or ())
        wanted = [n for n in names if n in chosen] if chosen else names
        picked = wanted[:GRAPH_BRANCHES]
        sem = asyncio.Semaphore(GRAPH_PARALLEL)
        deep = -(-limit // PER_PAGE)
        got = await asyncio.gather(
            *(self._branch_commits(n, deep if n in main else 1, sem) for n in picked)
        )
        seen: dict[str, GraphCommit] = {}
        for chunk in got:
            for raw in chunk:
                c = self._graph_commit(raw)
                if c.sha and c.sha not in seen:
                    seen[c.sha] = c
        ordered = order_commits(list(seen.values()))
        heads = {b.name: b.sha for b in all_branches if b.name in picked and b.sha}
        return Graph(
            commits=tuple(ordered[:limit]),
            heads=heads,
            branches=all_branches,
            prs=tuple(prs),
            truncated=len(ordered) > limit or len(wanted) > len(picked),
        )

    async def get_commit(self, sha: str) -> CommitDetail:
        sha = check_sha(sha)
        got = await self._call("GET", f"/commits/{sha}")
        got = got if isinstance(got, dict) else {}
        head = self._graph_commit(got)
        files = [f for f in (got.get("files") or []) if isinstance(f, dict)]
        return CommitDetail(
            sha=head.sha or sha,
            parents=head.parents,
            message=str((got.get("commit") or {}).get("message") or "")[:20_000],
            author=head.author,
            at=head.at,
            url=got.get("html_url"),
            files=tuple(
                FileDiff(
                    path=str(f.get("filename") or ""),
                    status=f.get("status") or "modified",
                    additions=int(f.get("additions", 0)),
                    deletions=int(f.get("deletions", 0)),
                    previous_path=f.get("previous_filename"),
                )
                for f in files[:DIFF_MAX_FILES]
            ),
            total_files=len(files),
        )
