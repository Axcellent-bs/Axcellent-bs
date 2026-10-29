"""형상관리 연결 계층의 공통 인터페이스 (FR-AG-011, AS-T16).

기획개발이 형상관리에 바라는 일 5가지 + CI 상태 조회 1가지를 정한다.
① 구간 안 변경 찾기 ② 변경 하나의 바뀐 파일·줄 ③ 브랜치 만들고 병합 요청 올리기
④ 병합 ⑤ 역추적 메타데이터 (+ CI 상태, IF-CUS-002)
코딩 에이전트용으로 파일 목록·파일 읽기와 열린 병합 요청에 커밋 더하기(③-2)가 있다.

- 프로그램 이름은 연결 부품(이 폴더)만 안다. 도메인은 이 인터페이스만 부른다.
- 쓰기(③④)는 `execute()` 관문을 지난 뒤에만 부른다. 부품은 관문을 모른다.
- 결과는 작은 불변 데이터클래스로 돌려준다. 프로그램 고유 응답은 밖으로 내보내지 않는다.
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal, Protocol, runtime_checkable

# 생성 PR 브랜치는 이 접두사로만 만든다 (FR-AG-008)
BRANCH_PREFIX = "gen/"
# 파일 읽기 한도 (이보다 크면 읽지 않는다)
MAX_READ_BYTES = 100 * 1024
LIST_LIMIT = 2000

CheckState = Literal["passed", "failed", "pending"]


class VcsError(Exception):
    """연결 부품 오류. 메시지에 토큰을 넣지 않는다.

    code: `not_found`·`unauthorized`·`forbidden`·`conflict`·`invalid`·`rate_limited`·
    `unavailable`·`read_only`·`config`
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message

    def __repr__(self) -> str:
        return f"VcsError(code={self.code!r}, message={self.message!r})"


@dataclass(frozen=True)
class Capabilities:
    """부품이 할 수 있는 일. 읽기 전용(SVN 등, FR-AG-013)이면 ③④를 부르지 않는다."""

    kind: str
    read_only: bool = False
    checks: bool = True
    # 이슈 칸이 있는지. 없는 프로그램(SVN·Perforce 등)은 나중에 게시판·요청란으로 대신한다
    issues: bool = False


@dataclass(frozen=True)
class ChangedFile:
    path: str
    additions: int = 0
    deletions: int = 0
    # 바뀐 줄 번호 (새 파일 기준). 모르면 빈 값
    lines: tuple[int, ...] = ()


@dataclass(frozen=True)
class Change:
    """병합 요청 하나 (GitHub PR, GitLab MR 등)."""

    number: int
    title: str
    branch: str
    base: str
    merged: bool
    merged_at: datetime | None = None
    url: str | None = None
    files: tuple[ChangedFile, ...] = ()

    @property
    def changed_paths(self) -> list[str]:
        return [f.path for f in self.files]

    @property
    def additions(self) -> int:
        return sum(f.additions for f in self.files)

    @property
    def deletions(self) -> int:
        return sum(f.deletions for f in self.files)


@dataclass(frozen=True)
class OpenedChange:
    number: int
    url: str | None
    branch: str


@dataclass(frozen=True)
class CheckRun:
    name: str
    state: CheckState


@dataclass(frozen=True)
class Checks:
    """CI 상태. 하나라도 실패면 failed, 끝나지 않은 게 있으면 pending, 아니면 passed."""

    state: CheckState
    runs: tuple[CheckRun, ...] = ()


@dataclass(frozen=True)
class TraceMeta:
    """변경 → 기획서 역추적용 ID (제목·본문에서 읽는다)."""

    number: int
    title: str
    requests: tuple[str, ...] = ()
    specs: tuple[str, ...] = ()
    # "SPEC-5:R3" 형식. 기획서 없이 "R3"만 있으면 "R3"
    paras: tuple[str, ...] = ()


@dataclass(frozen=True)
class OpenRequest:
    """③ 병합 요청 올리기 입력."""

    branch: str
    title: str
    body: str
    base: str | None = None
    files: dict[str, str] = field(default_factory=dict)


# ---- 레포 작업 공간 (개발 화면에서 PR·브랜치를 직접 다룬다)

ChangeState = Literal["open", "merged", "closed"]
# 목록·상세 한도 (응답 크기를 묶는다)
CHANGE_LIST_LIMIT = 50
DIFF_MAX_FILES = 300
DIFF_FILE_CHARS = 20_000
DIFF_TOTAL_CHARS = 300_000
COMMIT_LIMIT = 100
COMMENT_LIMIT = 100
ISSUE_LIST_LIMIT = 50
ISSUE_STATES = ("open", "closed", "all")
BRANCH_LIMIT = 200
MERGE_METHODS = ("merge", "squash", "rebase")


@dataclass(frozen=True)
class ChangeSummary:
    """병합 요청 하나의 머리 정보 (목록·상세 머리). 사람이 직접 올린 것도 같다."""

    number: int
    title: str
    state: ChangeState
    branch: str
    base: str
    author: str = ""
    body: str = ""
    draft: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    url: str | None = None
    comments: int | None = None
    # CI 상태 (목록에서는 열린 것 몇 개만 본다. 모르면 None)
    checks: CheckState | None = None


@dataclass(frozen=True)
class FileDiff:
    """파일 하나의 unified diff. 너무 길면 자르고 truncated."""

    path: str
    status: str = "modified"  # added · modified · removed · renamed
    additions: int = 0
    deletions: int = 0
    patch: str | None = None
    previous_path: str | None = None
    truncated: bool = False


@dataclass(frozen=True)
class Diff:
    number: int
    files: tuple[FileDiff, ...] = ()
    # 파일 수·전체 글자 한도를 넘어 뒤를 뺐다
    truncated: bool = False
    total_files: int = 0


@dataclass(frozen=True)
class Commit:
    sha: str
    message: str
    author: str = ""
    at: datetime | None = None


@dataclass(frozen=True)
class Comment:
    id: str
    author: str
    body: str
    at: datetime | None = None


@dataclass(frozen=True)
class IssueSummary:
    """이슈 하나의 머리 정보 (목록·상세 머리). 병합 요청은 들어가지 않는다."""

    number: int
    title: str
    state: Literal["open", "closed"]
    author: str = ""
    body: str = ""
    labels: tuple[str, ...] = ()
    assignees: tuple[str, ...] = ()
    comments: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    url: str | None = None
    # 닫힌 이유: completed(완료) · not_planned(안 함). 모르면 None
    reason: str | None = None


@dataclass(frozen=True)
class Branch:
    name: str
    sha: str = ""
    protected: bool = False
    default: bool = False
    last_commit_at: datetime | None = None
    ahead: int | None = None
    behind: int | None = None


# ---- 커밋 그래프 (SourceTree·`git log --graph`처럼 레포 상태를 한눈에)

GRAPH_LIMIT = 200
GRAPH_MAX = 500
# 그래프에 올릴 브랜치 수 (GitHub는 브랜치마다 커밋 목록을 한 번씩 부른다)
GRAPH_BRANCHES = 30
GRAPH_MESSAGE_CHARS = 500


@dataclass(frozen=True)
class GraphCommit:
    sha: str
    parents: tuple[str, ...] = ()
    message: str = ""
    author: str = ""
    at: datetime | None = None


@dataclass(frozen=True)
class GraphPr:
    """그래프 위 PR 꼬리표: head 끝 커밋·머지 커밋 → PR 번호."""

    number: int
    title: str
    state: ChangeState
    branch: str
    head_sha: str = ""
    merge_sha: str = ""


@dataclass(frozen=True)
class GraphBranch:
    name: str
    sha: str = ""
    default: bool = False


@dataclass(frozen=True)
class Graph:
    """커밋 그래프. commits는 최신이 먼저이고 자식이 늘 부모보다 앞이다."""

    commits: tuple[GraphCommit, ...] = ()
    # 그래프에 올린 브랜치의 끝 커밋 (이름 → sha)
    heads: dict[str, str] = field(default_factory=dict)
    # 고를 수 있는 브랜치 전체 (필터용)
    branches: tuple[GraphBranch, ...] = ()
    prs: tuple[GraphPr, ...] = ()
    # 한도(커밋 수·브랜치 수)에 걸려 뒤를 뺐다
    truncated: bool = False


@dataclass(frozen=True)
class CommitDetail:
    sha: str
    parents: tuple[str, ...] = ()
    message: str = ""
    author: str = ""
    at: datetime | None = None
    url: str | None = None
    files: tuple[FileDiff, ...] = ()
    total_files: int = 0


def order_commits(commits: list[GraphCommit]) -> list[GraphCommit]:
    """최신 순으로 늘어놓되 자식이 늘 부모보다 앞에 오게 한다 (시계가 어긋나도).

    같은 시각이면 나중에 넣은 것이 먼저다. 목록에 없는 부모는 무시한다."""
    if not commits:
        return []
    seq = {c.sha: i for i, c in enumerate(commits)}
    by = {c.sha: c for c in commits}

    def key(c: GraphCommit) -> tuple[float, int]:
        stamp = c.at.timestamp() if c.at is not None else float("-inf")
        return (-stamp, -seq[c.sha])

    children: dict[str, int] = dict.fromkeys(by, 0)
    for c in by.values():
        for p in set(c.parents):
            if p in children:
                children[p] += 1

    ready = [(key(c), c.sha) for c in by.values() if children[c.sha] == 0]
    heapq.heapify(ready)
    out: list[GraphCommit] = []
    while ready:
        _, sha = heapq.heappop(ready)
        c = by[sha]
        out.append(c)
        for p in set(c.parents):
            if p in children:
                children[p] -= 1
                if children[p] == 0:
                    heapq.heappush(ready, (key(by[p]), p))
    if len(out) < len(by):  # 고리가 있으면(있을 수 없지만) 남은 것을 뒤에 붙인다
        seen = {c.sha for c in out}
        out += sorted((c for c in by.values() if c.sha not in seen), key=key)
    return out


def reachable(commits: list[GraphCommit], tips: list[str]) -> set[str]:
    by = {c.sha: c for c in commits}
    seen: set[str] = set()
    stack = [t for t in tips if t in by]
    while stack:
        sha = stack.pop()
        if sha in seen:
            continue
        seen.add(sha)
        stack.extend(p for p in by[sha].parents if p in by and p not in seen)
    return seen


def cap_diff(number: int, files: list[FileDiff]) -> Diff:
    """diff 크기를 묶는다: 파일 300개, 파일당 2만 자, 합 30만 자. 넘으면 잘라 truncated."""
    out: list[FileDiff] = []
    used = 0
    cut = False
    for f in files[:DIFF_MAX_FILES]:
        patch = f.patch
        trunc = f.truncated
        if patch is not None:
            room = max(0, min(DIFF_FILE_CHARS, DIFF_TOTAL_CHARS - used))
            if len(patch) > room:
                patch = patch[:room]
                # 줄 중간에서 끊지 않는다
                patch = patch[: patch.rfind("\n") + 1] if "\n" in patch else ""
                trunc = True
            used += len(patch)
        if used >= DIFF_TOTAL_CHARS:
            cut = True
        out.append(replace(f, patch=patch, truncated=trunc))
    return Diff(
        number=number,
        files=tuple(out),
        truncated=cut or len(files) > DIFF_MAX_FILES,
        total_files=len(files),
    )


def match_query(c: ChangeSummary, query: str | None) -> bool:
    """제목·브랜치·번호(`12`, `#12`)·작성자로 찾는다. 대소문자 무시."""
    q = (query or "").strip().lower()
    if not q:
        return True
    num = q.lstrip("#")
    if num.isdigit() and int(num) == c.number:
        return True
    return any(q in (x or "").lower() for x in (c.title, c.branch, c.author))


def match_issue(i: IssueSummary, query: str | None) -> bool:
    """제목·번호(`12`, `#12`)·작성자·라벨로 찾는다. 대소문자 무시."""
    q = (query or "").strip().lower()
    if not q:
        return True
    num = q.lstrip("#")
    if num.isdigit() and int(num) == i.number:
        return True
    return any(q in (x or "").lower() for x in (i.title, i.author, *i.labels))


def state_of(merged: bool, closed: bool) -> ChangeState:
    return "merged" if merged else ("closed" if closed else "open")


@runtime_checkable
class VcsAdapter(Protocol):
    """형상관리 연결 부품. 프로그램마다 하나씩 만든다."""

    @property
    def capabilities(self) -> Capabilities: ...

    async def find_changes(
        self, since: datetime, until: datetime, path_prefix: str | None = None
    ) -> list[Change]:
        """① 구간 안에 병합된 변경 (최근 병합 순)."""
        ...

    async def get_change(self, number: int) -> Change:
        """② 변경 하나와 바뀐 파일·줄."""
        ...

    async def find_change_by_branch(self, branch: str) -> Change | None:
        """브랜치로 이미 올라간 변경(열림·병합 모두)을 찾는다. 없으면 None.
        다시 올리다 브랜치가 겹칠 때(conflict) 새로 만들지 않고 이어받는 데 쓴다."""
        ...

    async def open_change(
        self,
        branch: str,
        title: str,
        body: str,
        base: str | None = None,
        files: dict[str, str] | None = None,
    ) -> OpenedChange:
        """③ 브랜치 만들고 파일을 커밋하고 병합 요청을 올린다. `execute()` 뒤에만 부른다."""
        ...

    async def update_change(self, number: int, files: dict[str, str], message: str) -> OpenedChange:
        """③-2 열린 병합 요청 브랜치에 커밋 하나를 더한다. `execute()` 뒤에만 부른다."""
        ...

    async def merge_change(self, number: int, method: str = "squash") -> Change:
        """④ 병합. `execute()` 뒤에만 부른다."""
        ...

    async def close_change(self, number: int) -> None:
        """병합 안 된 변경을 닫고 브랜치를 지운다 (작업 중단 되돌리기). 병합됐으면 conflict."""
        ...

    async def list_files(self, prefix: str | None = None, limit: int = LIST_LIMIT) -> list[str]:
        """기준 브랜치의 파일 경로 (정렬, 최대 limit개)."""
        ...

    async def read_file(self, path: str, ref: str | None = None) -> str:
        """파일 하나의 글 (기본은 기준 브랜치). 바이너리·한도 초과는 `VcsError`(invalid)."""
        ...

    async def get_trace_meta(self, number: int) -> TraceMeta:
        """⑤ 제목·본문의 요청·기획서·문단 ID."""
        ...

    async def get_checks(self, number: int) -> Checks:
        """CI 상태 (IF-CUS-002)."""
        ...

    # ---- 레포 작업 공간 (읽기는 그냥, 쓰기는 `execute()` 뒤에만)

    async def list_changes(
        self, state: str = "open", query: str | None = None, limit: int = CHANGE_LIST_LIMIT
    ) -> list[ChangeSummary]:
        """병합 요청 목록 (사람이 직접 올린 것 포함, 최근 갱신 순). state open·merged·closed·all."""
        ...

    async def describe_change(self, number: int) -> ChangeSummary:
        """병합 요청 하나의 머리 정보 (본문·작성자·상태)."""
        ...

    async def get_diff(self, number: int) -> Diff:
        """파일별 unified diff (`cap_diff` 한도)."""
        ...

    async def list_commits(self, number: int, limit: int = COMMIT_LIMIT) -> list[Commit]: ...

    async def list_comments(self, number: int, limit: int = COMMENT_LIMIT) -> list[Comment]:
        """대화 코멘트 (줄 코멘트는 아직 안 본다)."""
        ...

    async def add_comment(self, number: int, body: str) -> Comment: ...

    async def list_issues(
        self, state: str = "open", query: str | None = None, limit: int = ISSUE_LIST_LIMIT
    ) -> list[IssueSummary]:
        """이슈 목록 (최근 바뀐 순). `capabilities.issues`가 False면 부르지 않는다."""
        ...

    async def describe_issue(self, number: int) -> IssueSummary:
        """이슈 하나. 코멘트는 `list_comments`로 읽고 `add_comment`로 단다 (번호를 같이 쓴다)."""
        ...

    async def reopen_change(self, number: int) -> ChangeSummary:
        """닫힌(병합 안 된) 변경을 다시 연다. 병합됐으면 conflict."""
        ...

    async def revert_change(self, number: int, title: str | None = None) -> OpenedChange:
        """병합된 변경을 되돌리는 새 병합 요청을 연다. 병합 안 됐으면 conflict."""
        ...

    async def list_branches(self, limit: int = BRANCH_LIMIT) -> list[Branch]: ...

    async def delete_branch(self, name: str) -> None:
        """브랜치 지우기. 기준·기본 브랜치는 invalid."""
        ...

    async def list_graph(
        self, branches: list[str] | None = None, limit: int = GRAPH_LIMIT
    ) -> Graph:
        """커밋 그래프 (고른 브랜치에서 닿는 커밋, 최신 순 limit개). None이면 브랜치 전체."""
        ...

    async def get_commit(self, sha: str) -> CommitDetail:
        """커밋 하나의 전체 메시지·부모·바뀐 파일 (+/−만, diff 글은 빼도 된다)."""
        ...


# ---------------------------------------------------------------- 공용 도우미

_REQ = re.compile(r"\bREQ-\d+\b")
_SPEC_PARA = re.compile(r"\b(SPEC-\d+):([RCQ]\d+)\b")
_SPEC = re.compile(r"\bSPEC-\d+\b")
_PARA = re.compile(r"(?<![:\w-])([RC]\d+)\b")


def _uniq(items: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(items))


def parse_trace(number: int, title: str, body: str | None) -> TraceMeta:
    """제목·본문에서 `REQ-n`·`SPEC-n`·`SPEC-n:R3`·`R3`을 읽는다 (나온 순서, 중복 없이)."""
    text = f"{title}\n{body or ''}"
    paras = [f"{m.group(1)}:{m.group(2)}" for m in _SPEC_PARA.finditer(text)]
    known = {p.split(":", 1)[1] for p in paras}
    bare = _SPEC_PARA.sub(" ", text)
    # 기획서와 같이 적힌 문단은 한 번만 (제목 "R1: …" + 본문 "SPEC-7:R1" → "SPEC-7:R1")
    paras += [m.group(1) for m in _PARA.finditer(bare) if m.group(1) not in known]
    return TraceMeta(
        number=number,
        title=title,
        requests=_uniq(_REQ.findall(text)),
        specs=_uniq(_SPEC.findall(text)),
        paras=_uniq(paras),
    )


def check_branch(branch: str) -> str:
    """생성 브랜치는 `gen/`으로 시작해야 한다."""
    if not branch.startswith(BRANCH_PREFIX) or len(branch) <= len(BRANCH_PREFIX):
        raise VcsError("invalid", f"브랜치는 {BRANCH_PREFIX}로 시작해야 한다: {branch}")
    if ".." in branch or " " in branch:
        raise VcsError("invalid", f"쓸 수 없는 브랜치 이름: {branch}")
    return branch


_SHA = re.compile(r"^[0-9A-Za-z]{4,64}$")


def check_sha(sha: str) -> str:
    s = (sha or "").strip()
    if not _SHA.match(s):
        raise VcsError("invalid", f"커밋 sha가 아니다: {sha[:80]}")
    return s


def check_path(path: str) -> str:
    """읽고 쓸 파일 경로 검사: 상대 경로, `..`·빈 조각 없음."""
    p = (path or "").strip()
    parts = p.split("/")
    if not p or p.startswith("/") or "\\" in p or any(x in ("", ".", "..") for x in parts):
        raise VcsError("invalid", f"쓸 수 없는 파일 경로: {path}")
    return p


def decode_text(data: bytes, path: str) -> str:
    """파일 바이트를 글로 바꾼다. 한도 초과·바이너리면 `VcsError`(invalid)."""
    if len(data) > MAX_READ_BYTES:
        raise VcsError("invalid", f"파일이 너무 크다 ({len(data)}바이트): {path}")
    if b"\x00" in data:
        raise VcsError("invalid", f"바이너리 파일: {path}")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        raise VcsError("invalid", f"UTF-8 글이 아니다: {path}") from None


def combine_checks(runs: list[CheckRun]) -> Checks:
    """검사가 하나도 없으면 아직 안 돈 것으로 보고 pending (통과로 치지 않는다)."""
    states = {r.state for r in runs}
    if not runs:
        state: CheckState = "pending"
    elif "failed" in states:
        state = "failed"
    elif "pending" in states:
        state = "pending"
    else:
        state = "passed"
    return Checks(state=state, runs=tuple(runs))
