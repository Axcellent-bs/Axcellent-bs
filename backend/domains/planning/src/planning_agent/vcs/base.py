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

import re
from dataclasses import dataclass, field
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
