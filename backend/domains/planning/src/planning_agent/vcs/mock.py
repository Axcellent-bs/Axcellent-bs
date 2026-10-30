"""목 연결 부품. 프로세스 메모리에 변경을 두고 공통 인터페이스를 그대로 따른다.

- 기본값이자 모든 테스트가 쓰는 부품이다. 외부 호출이 없다.
- 번호는 `first_number`부터 1씩 늘어난다 (같은 순서로 부르면 같은 번호).
- `seed()`로 병합된 변경·CI 상태를 미리 넣을 수 있다.
- `seed_files()`로 기준 브랜치 파일을 넣는다 (코딩 에이전트 읽기용). 병합하면 그 변경 파일이
  기준 브랜치에 들어간다.
"""

from __future__ import annotations

import difflib
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from planning_agent.vcs.base import (
    BRANCH_LIMIT,
    CHANGE_LIST_LIMIT,
    COMMENT_LIMIT,
    COMMIT_LIMIT,
    GRAPH_LIMIT,
    GRAPH_MAX,
    ISSUE_LIST_LIMIT,
    ISSUE_STATES,
    LIST_LIMIT,
    Branch,
    Capabilities,
    Change,
    ChangedFile,
    ChangeSummary,
    CheckRun,
    Checks,
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
    reachable,
    state_of,
)

MERGE_METHODS = ("merge", "squash", "rebase")
# 커밋 그래프의 첫 커밋 (기준 브랜치 시작점)
ROOT_SHA = "a" * 40
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass
class _Entry:
    change: Change
    body: str
    closed: bool = False
    runs: list[CheckRun] = field(default_factory=list)
    contents: dict[str, str] = field(default_factory=dict)
    author: str = "axcellent-bot"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    # 브랜치를 만들 때의 기준 파일 (diff 왼쪽). None = 그때 없던 파일
    before: dict[str, str | None] = field(default_factory=dict)
    # 이 변경이 지우는 파일 (되돌리기 PR에서 새로 만든 파일을 지울 때)
    deleted: set[str] = field(default_factory=set)
    commits: list[Commit] = field(default_factory=list)
    comments: list[Comment] = field(default_factory=list)


def _added_lines(content: str) -> tuple[int, ...]:
    return tuple(range(1, len(content.splitlines()) + 1))


class MockVcsAdapter:
    """메모리 형상관리. 새 변경의 CI는 기본으로 `ci` 하나 통과."""

    def __init__(
        self,
        *,
        base_branch: str = "dev",
        first_number: int = 1,
        clock: Callable[[], datetime] | None = None,
        default_checks: tuple[CheckRun, ...] = (CheckRun("ci", "passed"),),
        read_only: bool = False,
    ):
        self.base_branch = base_branch
        self._next = first_number
        self._clock = clock or (lambda: datetime.now(UTC))
        self._default_checks = default_checks
        self._entries: dict[int, _Entry] = {}
        self._branches: set[str] = set()
        self._gone: set[str] = set()  # 지운 브랜치
        self._comment_seq = 0
        self._files: dict[str, str] = {}  # 기준 브랜치 파일 (경로 → 글)
        self._caps = Capabilities(kind="mock", read_only=read_only, issues=True)
        # 이슈 (번호는 변경과 같이 센다). 코멘트는 _issue_comments
        self._issues: dict[int, IssueSummary] = {}
        self._issue_comments: dict[int, list[Comment]] = {}
        # 쓰기 호출 기록 (테스트에서 execute 뒤에만 불렀는지 본다)
        self.writes: list[tuple[str, int]] = []

    def __repr__(self) -> str:
        return f"MockVcsAdapter(base_branch={self.base_branch!r}, changes={len(self._entries)})"

    @property
    def capabilities(self) -> Capabilities:
        return self._caps

    # ---- 목 전용

    def seed(
        self,
        *,
        title: str,
        body: str = "",
        branch: str | None = None,
        files: dict[str, tuple[int, int]] | None = None,
        merged_at: datetime | None = None,
        checks: list[CheckRun] | None = None,
        base: str | None = None,
        author: str = "dev-user",
        closed: bool = False,
    ) -> int:
        """변경 하나를 미리 넣는다. files는 `{경로: (추가 줄, 지운 줄)}`."""
        number = self._take_number()
        changed = tuple(
            ChangedFile(path=p, additions=a, deletions=d) for p, (a, d) in (files or {}).items()
        )
        self._entries[number] = _Entry(
            change=Change(
                number=number,
                title=title,
                branch=branch or f"gen/seed-{number}",
                base=base or self.base_branch,
                merged=merged_at is not None,
                merged_at=merged_at,
                url=f"mock://change/{number}",
                files=changed,
            ),
            body=body,
            runs=list(checks if checks is not None else self._default_checks),
            author=author,
            closed=closed,
            created_at=merged_at or self._clock(),
            updated_at=merged_at or self._clock(),
            closed_at=merged_at or (self._clock() if closed else None),
            commits=[Commit(sha=f"seed{number:04d}", message=title, author=author, at=merged_at)],
        )
        return number

    def set_checks(self, number: int, runs: list[CheckRun]) -> None:
        self._entry(number).runs = list(runs)

    def seed_files(self, files: dict[str, str]) -> None:
        """기준 브랜치에 파일을 넣는다 (`{경로: 글}`)."""
        self._files.update(files)

    def contents(self, number: int) -> dict[str, str]:
        """변경 하나가 커밋한 파일 (테스트용)."""
        return dict(self._entry(number).contents)

    # ---- 공통 인터페이스

    async def find_changes(
        self, since: datetime, until: datetime, path_prefix: str | None = None
    ) -> list[Change]:
        found = [
            e.change
            for e in self._entries.values()
            if e.change.merged
            and e.change.merged_at is not None
            and e.change.base == self.base_branch
            and since <= e.change.merged_at <= until
            and (
                path_prefix is None
                or any(p.startswith(path_prefix) for p in e.change.changed_paths)
            )
        ]
        return sorted(found, key=lambda c: (c.merged_at, c.number), reverse=True)

    async def get_change(self, number: int) -> Change:
        return self._entry(number).change

    async def find_change_by_branch(self, branch: str) -> Change | None:
        found = [e.change for e in self._entries.values() if e.change.branch == branch]
        return max(found, key=lambda c: c.number) if found else None

    async def open_change(
        self,
        branch: str,
        title: str,
        body: str,
        base: str | None = None,
        files: dict[str, str] | None = None,
    ) -> OpenedChange:
        self._writable()
        check_branch(branch)
        if not files:
            raise VcsError("invalid", "커밋할 파일이 없다")
        if branch in self._branches:
            raise VcsError("conflict", f"이미 있는 브랜치: {branch}")
        number = self._take_number()
        self._branches.add(branch)
        self._entries[number] = _Entry(
            change=Change(
                number=number,
                title=title,
                branch=branch,
                base=base or self.base_branch,
                merged=False,
                url=f"mock://change/{number}",
                files=tuple(
                    ChangedFile(path=p, additions=len(c.splitlines()), lines=_added_lines(c))
                    for p, c in files.items()
                ),
            ),
            body=body,
            runs=list(self._default_checks),
            contents=dict(files),
            created_at=self._clock(),
            updated_at=self._clock(),
            before={p: self._files.get(p) for p in files},
            commits=[self._commit(number, 1, title)],
        )
        self.writes.append(("open_change", number))
        return OpenedChange(number=number, url=f"mock://change/{number}", branch=branch)

    async def update_change(self, number: int, files: dict[str, str], message: str) -> OpenedChange:
        self._writable()
        if not files:
            raise VcsError("invalid", "커밋할 파일이 없다")
        entry = self._entry(number)
        if entry.change.merged:
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        for p in files:
            entry.before.setdefault(p, self._files.get(p))
        entry.contents.update(files)
        entry.commits.append(self._commit(number, len(entry.commits) + 1, message))
        entry.updated_at = self._clock()
        entry.change = replace(
            entry.change,
            files=tuple(
                ChangedFile(path=p, additions=len(c.splitlines()), lines=_added_lines(c))
                for p, c in entry.contents.items()
            ),
        )
        self.writes.append(("update_change", number))
        return OpenedChange(number=number, url=entry.change.url, branch=entry.change.branch)

    async def close_change(self, number: int) -> None:
        self._writable()
        entry = self._entry(number)
        if entry.change.merged:
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        entry.closed = True
        entry.closed_at = entry.updated_at = self._clock()
        self.writes.append(("close_change", number))

    async def merge_change(self, number: int, method: str = "squash") -> Change:
        self._writable()
        if method not in MERGE_METHODS:
            raise VcsError("invalid", f"모르는 병합 방식: {method}")
        entry = self._entry(number)
        if entry.change.merged:
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        entry.change = replace(entry.change, merged=True, merged_at=self._clock())
        entry.closed_at = entry.updated_at = self._clock()
        if entry.change.base == self.base_branch:
            # 되돌리기에 쓰려고 병합 직전 기준 파일을 남긴다
            entry.before = {p: self._files.get(p) for p in (*entry.contents, *entry.deleted)}
            self._files.update(entry.contents)
            for p in entry.deleted:
                self._files.pop(p, None)
        self.writes.append(("merge_change", number))
        return entry.change

    async def list_files(self, prefix: str | None = None, limit: int = LIST_LIMIT) -> list[str]:
        rows = sorted(p for p in self._files if prefix is None or p.startswith(prefix))
        return rows[: max(0, limit)]

    async def read_file(self, path: str, ref: str | None = None) -> str:
        path = check_path(path)
        files = self._files
        if ref and ref != self.base_branch:
            entry = next((e for e in self._entries.values() if e.change.branch == ref), None)
            if entry is None:
                raise VcsError("not_found", f"모르는 브랜치: {ref}")
            files = {**self._files, **entry.contents}
        if path not in files:
            raise VcsError("not_found", f"파일이 없다: {path}")
        return decode_text(files[path].encode("utf-8"), path)

    async def get_trace_meta(self, number: int) -> TraceMeta:
        entry = self._entry(number)
        return parse_trace(number, entry.change.title, entry.body)

    async def get_checks(self, number: int) -> Checks:
        return combine_checks(self._entry(number).runs)

    # ---- 내부

    def _take_number(self) -> int:
        number = self._next
        self._next += 1
        return number

    def _entry(self, number: int) -> _Entry:
        entry = self._entries.get(number)
        if entry is None:
            raise VcsError("not_found", f"변경 {number}이 없다")
        return entry

    def _writable(self) -> None:
        if self._caps.read_only:
            raise VcsError("read_only", "읽기 전용 모드라 쓰기를 하지 않는다 (사람 반영 필요)")

    # ---- 레포 작업 공간

    def _commit(self, number: int, seq: int, message: str) -> Commit:
        return Commit(
            sha=f"{number:04d}{seq:04d}".ljust(40, "0"),
            message=message,
            author="axcellent-bot",
            at=self._clock(),
        )

    def _summary(self, e: _Entry) -> ChangeSummary:
        c = e.change
        return ChangeSummary(
            number=c.number,
            title=c.title,
            state=state_of(c.merged, e.closed),
            branch=c.branch,
            base=c.base,
            author=e.author,
            body=e.body,
            created_at=e.created_at,
            updated_at=e.updated_at,
            merged_at=c.merged_at,
            closed_at=e.closed_at,
            url=c.url,
            comments=len(e.comments),
            checks=combine_checks(e.runs).state,
        )

    async def list_changes(
        self, state: str = "open", query: str | None = None, limit: int = CHANGE_LIST_LIMIT
    ) -> list[ChangeSummary]:
        if state not in ("open", "merged", "closed", "all"):
            raise VcsError("invalid", f"모르는 상태: {state}")
        rows = [self._summary(e) for e in self._entries.values()]
        rows = [r for r in rows if (state == "all" or r.state == state) and match_query(r, query)]
        rows.sort(key=lambda r: r.number, reverse=True)
        return rows[: max(1, limit)]

    async def describe_change(self, number: int) -> ChangeSummary:
        return self._summary(self._entry(number))

    async def get_diff(self, number: int) -> Diff:
        e = self._entry(number)
        files: list[FileDiff] = []
        if not e.contents and not e.deleted:
            # 줄 수만 넣은 시드 변경은 글이 없다
            files = [
                FileDiff(path=f.path, additions=f.additions, deletions=f.deletions)
                for f in e.change.files
            ]
        for path in sorted({*e.contents, *e.deleted}):
            old = e.before.get(path)
            new = None if path in e.deleted else e.contents.get(path)
            rows = list(
                difflib.unified_diff(
                    (old or "").splitlines(), (new or "").splitlines(), lineterm="", n=3
                )
            )[2:]
            files.append(
                FileDiff(
                    path=path,
                    status="added" if old is None else "removed" if new is None else "modified",
                    additions=sum(1 for r in rows if r.startswith("+")),
                    deletions=sum(1 for r in rows if r.startswith("-")),
                    patch="\n".join(rows) + "\n" if rows else "",
                )
            )
        return cap_diff(number, files)

    async def list_commits(self, number: int, limit: int = COMMIT_LIMIT) -> list[Commit]:
        return list(self._entry(number).commits[:limit])

    async def list_comments(self, number: int, limit: int = COMMENT_LIMIT) -> list[Comment]:
        if number in self._issues:
            return list(self._issue_comments.get(number, [])[:limit])
        return list(self._entry(number).comments[:limit])

    # ---- 이슈

    def seed_issue(
        self,
        *,
        title: str,
        body: str = "",
        state: str = "open",
        labels: tuple[str, ...] = (),
        assignees: tuple[str, ...] = (),
        author: str = "dev-user",
    ) -> int:
        n = self._next
        self._next += 1
        now = self._clock()
        self._issues[n] = IssueSummary(
            number=n,
            title=title,
            state="closed" if state == "closed" else "open",
            author=author,
            body=body,
            labels=labels,
            assignees=assignees,
            comments=0,
            created_at=now,
            updated_at=now,
            closed_at=now if state == "closed" else None,
        )
        return n

    async def list_issues(
        self, state: str = "open", query: str | None = None, limit: int = ISSUE_LIST_LIMIT
    ) -> list[IssueSummary]:
        if state not in ISSUE_STATES:
            raise VcsError("invalid", f"모르는 상태: {state}")
        rows = [
            replace(i, comments=len(self._issue_comments.get(i.number, [])))
            for i in self._issues.values()
            if (state == "all" or i.state == state) and match_issue(i, query)
        ]
        rows.sort(key=lambda i: i.number, reverse=True)
        return rows[: max(1, limit)]

    async def describe_issue(self, number: int) -> IssueSummary:
        i = self._issues.get(number)
        if i is None:
            raise VcsError("not_found", f"없는 이슈: {number}")
        return replace(i, comments=len(self._issue_comments.get(number, [])))

    async def add_comment(self, number: int, body: str) -> Comment:
        self._writable()
        text = (body or "").strip()
        if not text:
            raise VcsError("invalid", "코멘트가 비었다")
        if number in self._issues:
            self._comment_seq += 1
            ic = Comment(id=str(self._comment_seq), author="human", body=text, at=self._clock())
            self._issue_comments.setdefault(number, []).append(ic)
            self.writes.append(("add_comment", number))
            return ic
        e = self._entry(number)
        self._comment_seq += 1
        c = Comment(id=str(self._comment_seq), author="human", body=text, at=self._clock())
        e.comments.append(c)
        e.updated_at = self._clock()
        self.writes.append(("add_comment", number))
        return c

    async def reopen_change(self, number: int) -> ChangeSummary:
        self._writable()
        e = self._entry(number)
        if e.change.merged:
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        e.closed = False
        e.closed_at = None
        e.updated_at = self._clock()
        self.writes.append(("reopen_change", number))
        return self._summary(e)

    async def revert_change(self, number: int, title: str | None = None) -> OpenedChange:
        """병합 직전 기준 파일로 되돌리는 변경을 연다 (그때 없던 파일은 지운다)."""
        self._writable()
        src = self._entry(number)
        if not src.change.merged:
            raise VcsError("conflict", f"병합되지 않은 변경은 되돌리지 않는다: {number}")
        branch = f"revert-{number}-{src.change.branch}"
        n = 2
        while branch in self._branches:
            branch = f"revert-{number}-{src.change.branch}-{n}"
            n += 1
        new = self._take_number()
        self._branches.add(branch)
        contents = {p: c for p, c in src.before.items() if c is not None}
        deleted = {p for p, c in src.before.items() if c is None}
        before = {p: self._files.get(p) for p in src.before}
        name = title or f'Revert "{src.change.title}"'
        self._entries[new] = _Entry(
            change=Change(
                number=new,
                title=name,
                branch=branch,
                base=src.change.base,
                merged=False,
                url=f"mock://change/{new}",
                files=tuple(
                    ChangedFile(path=p, additions=len(c.splitlines()), lines=_added_lines(c))
                    for p, c in contents.items()
                )
                + tuple(ChangedFile(path=p) for p in sorted(deleted)),
            ),
            body=f"Reverts #{number}",
            runs=list(self._default_checks),
            contents=contents,
            deleted=deleted,
            author="human",
            created_at=self._clock(),
            updated_at=self._clock(),
            before=before,
            commits=[self._commit(new, 1, name)],
        )
        self.writes.append(("revert_change", new))
        return OpenedChange(number=new, url=f"mock://change/{new}", branch=branch)

    async def list_branches(self, limit: int = BRANCH_LIMIT) -> list[Branch]:
        rows: dict[str, Branch] = {
            self.base_branch: Branch(
                name=self.base_branch, default=True, protected=True, ahead=0, behind=0
            )
        }
        for e in sorted(self._entries.values(), key=lambda x: x.change.number):
            name = e.change.branch
            if name in self._gone or name == self.base_branch:
                continue
            rows[name] = Branch(
                name=name,
                sha=e.commits[-1].sha if e.commits else "",
                last_commit_at=e.updated_at,
                ahead=0 if e.change.merged else len(e.commits),
                behind=0,
            )
        return list(rows.values())[: max(1, limit)]

    async def delete_branch(self, name: str) -> None:
        self._writable()
        if not name or name == self.base_branch:
            raise VcsError("invalid", f"기준·기본 브랜치는 지우지 않는다: {name}")
        if name in self._gone or not any(e.change.branch == name for e in self._entries.values()):
            raise VcsError("not_found", f"모르는 브랜치: {name}")
        self._gone.add(name)
        self._branches.discard(name)
        self.writes.append(("delete_branch", 0))

    # ---- 커밋 그래프 (변경·브랜치로 히스토리를 지어 낸다)

    @staticmethod
    def merge_sha(number: int) -> str:
        return f"{number:04d}ffff".ljust(40, "0")

    def _history(self) -> tuple[list[GraphCommit], dict[str, str], dict[str, int]]:
        """(만든 순 커밋, 브랜치 끝, 커밋 → 변경 번호). 브랜치는 만든 때 기준 끝에서 갈라지고,
        머지하면 머지 커밋(부모 둘)이 기준 쪽에 생긴다."""
        commits = [GraphCommit(sha=ROOT_SHA, message="init", author="dev-user")]
        owner: dict[str, int] = {}
        tips: dict[str, str] = {self.base_branch: ROOT_SHA}
        events: list[tuple[datetime, int, int]] = []
        for n, e in self._entries.items():
            events.append((e.created_at or _EPOCH, 0, n))
            if e.change.merged:
                events.append((e.change.merged_at or e.created_at or _EPOCH, 1, n))
        for _, kind, n in sorted(events):
            e = self._entries[n]
            c = e.change
            if kind == 0:
                parent = tips.get(c.base, tips[self.base_branch])
                for cm in e.commits:
                    commits.append(
                        GraphCommit(
                            sha=cm.sha,
                            parents=(parent,),
                            message=cm.message,
                            author=cm.author,
                            at=cm.at,
                        )
                    )
                    owner[cm.sha] = n
                    parent = cm.sha
                tips[c.branch] = parent
                continue
            into = tips.get(c.base, tips[self.base_branch])
            side = tips.get(c.branch)
            sha = self.merge_sha(n)
            commits.append(
                GraphCommit(
                    sha=sha,
                    parents=(into, side) if side and side != into else (into,),
                    message=f"Merge pull request #{n} from {c.branch}",
                    author=e.author,
                    at=c.merged_at,
                )
            )
            owner[sha] = n
            tips[c.base] = sha
        heads = {
            name: sha
            for name, sha in tips.items()
            if name == self.base_branch or name not in self._gone
        }
        return commits, heads, owner

    async def list_graph(
        self, branches: list[str] | None = None, limit: int = GRAPH_LIMIT
    ) -> Graph:
        limit = max(1, min(int(limit), GRAPH_MAX))
        commits, heads, _ = self._history()
        names = [self.base_branch, *(n for n in heads if n != self.base_branch)]
        chosen = set(branches or ())
        picked = [n for n in names if n in chosen] if chosen else names
        keep = reachable(commits, [heads[n] for n in picked])
        ordered = order_commits([c for c in commits if c.sha in keep])
        prs = tuple(
            GraphPr(
                number=n,
                title=e.change.title,
                state=state_of(e.change.merged, e.closed),
                branch=e.change.branch,
                head_sha=e.commits[-1].sha if e.commits else "",
                merge_sha=self.merge_sha(n) if e.change.merged else "",
            )
            for n, e in sorted(self._entries.items(), reverse=True)
        )
        return Graph(
            commits=tuple(ordered[:limit]),
            heads={n: heads[n] for n in picked},
            branches=tuple(
                GraphBranch(name=n, sha=heads[n], default=n == self.base_branch) for n in names
            ),
            prs=prs,
            truncated=len(ordered) > limit,
        )

    async def get_commit(self, sha: str) -> CommitDetail:
        sha = check_sha(sha)
        commits, _, owner = self._history()
        c = next((x for x in commits if x.sha == sha), None)
        if c is None:
            raise VcsError("not_found", f"커밋이 없다: {sha}")
        if sha in owner:
            files = [
                FileDiff(path=f.path, additions=f.additions, deletions=f.deletions)
                for f in self._entries[owner[sha]].change.files
            ]
        else:  # 첫 커밋: 기준 브랜치 파일
            files = [
                FileDiff(path=p, status="added", additions=len(t.splitlines()))
                for p, t in sorted(self._files.items())
            ]
        return CommitDetail(
            sha=c.sha,
            parents=c.parents,
            message=c.message,
            author=c.author,
            at=c.at,
            url=None,
            files=tuple(files),
            total_files=len(files),
        )
