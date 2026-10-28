"""목 연결 부품. 프로세스 메모리에 변경을 두고 공통 인터페이스를 그대로 따른다.

- 기본값이자 모든 테스트가 쓰는 부품이다. 외부 호출이 없다.
- 번호는 `first_number`부터 1씩 늘어난다 (같은 순서로 부르면 같은 번호).
- `seed()`로 병합된 변경·CI 상태를 미리 넣을 수 있다.
- `seed_files()`로 기준 브랜치 파일을 넣는다 (코딩 에이전트 읽기용). 병합하면 그 변경 파일이
  기준 브랜치에 들어간다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from planning_agent.vcs.base import (
    LIST_LIMIT,
    Capabilities,
    Change,
    ChangedFile,
    CheckRun,
    Checks,
    OpenedChange,
    TraceMeta,
    VcsError,
    check_branch,
    check_path,
    combine_checks,
    decode_text,
    parse_trace,
)

MERGE_METHODS = ("merge", "squash", "rebase")


@dataclass
class _Entry:
    change: Change
    body: str
    runs: list[CheckRun] = field(default_factory=list)
    contents: dict[str, str] = field(default_factory=dict)


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
        self._files: dict[str, str] = {}  # 기준 브랜치 파일 (경로 → 글)
        self._caps = Capabilities(kind="mock", read_only=read_only)
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
        entry.contents.update(files)
        entry.change = replace(
            entry.change,
            files=tuple(
                ChangedFile(path=p, additions=len(c.splitlines()), lines=_added_lines(c))
                for p, c in entry.contents.items()
            ),
        )
        self.writes.append(("update_change", number))
        return OpenedChange(number=number, url=entry.change.url, branch=entry.change.branch)

    async def merge_change(self, number: int, method: str = "squash") -> Change:
        self._writable()
        if method not in MERGE_METHODS:
            raise VcsError("invalid", f"모르는 병합 방식: {method}")
        entry = self._entry(number)
        if entry.change.merged:
            raise VcsError("conflict", f"이미 병합된 변경: {number}")
        entry.change = replace(entry.change, merged=True, merged_at=self._clock())
        if entry.change.base == self.base_branch:
            self._files.update(entry.contents)
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
