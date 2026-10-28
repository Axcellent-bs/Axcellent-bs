"""도메인 → 형상관리 연결 계층 다리 (FR-AG-011).

- `PLANNING_VCS_KIND`가 `mock`(기본)이면 아무것도 하지 않는다. 목 모드 상태·활동은 그대로다.
- 그 밖이면(또는 테스트가 부품을 바꿔 끼우면) 관문 `execute()`를 지난 PR을 실제로 올리고 병합한다.
- 부품이 실패해도 예외를 밖으로 내지 않는다. 활동 한 줄(`Note`)만 돌려주고 PR은 병합하지 않는다.
- 연결이 여러 개면 `propose()`가 올릴 레포를 제안하고(`repo_routing.py`), 사람이 승인한 연결로만
  올린다. 연결이 하나면 바로 승인한다 ("자동 (연결 1개)").
- 이 파일과 도메인은 프로그램 이름을 모른다 (`rg` 0건, NFR-EXT-003).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from planning_agent import repo_routing, vcs
from planning_agent.domain.models import PR, RepoCandidate, RepoTarget
from planning_agent.vcs.base import VcsAdapter, VcsError

AUTO_ONE = "자동 (연결 1개)"

NOTE_FILE_DIR = "docs/generated"


@dataclass(frozen=True)
class Note:
    """서비스가 활동 로그에 남길 한 줄. ok가 False면 그 단계는 실패다."""

    ok: bool
    point: str
    text: str


def enabled() -> bool:
    """실제로 올릴 연결이 있나. 연결이 없고 바꿔 끼운 부품도 없으면 False (목 설정)."""
    return vcs.is_overridden() or bool(vcs.connections())


def targets() -> list[vcs.Connection]:
    """PR을 보낼 수 있는 연결 (목 설정이면 빈 목록)."""
    return vcs.routing_connections() if enabled() else []


def active(connection_id: str | None = None) -> VcsAdapter | None:
    """실제로 부를 부품. 목 설정이면 None (도메인은 지금처럼 가짜 PR만 쓴다).

    연결을 주면 그 연결의 부품, 안 주면 첫 연결의 부품이다.
    설정이 잘못됐거나 모르는 연결이면 `VcsError`를 던진다. 부르는 쪽이 실패 한 줄로 바꾼다."""
    if not enabled():
        return None
    if connection_id is None:
        return vcs.get_vcs()
    return vcs.get_adapter(connection_id)


def _connection_of(pr: PR) -> str | None:
    return pr.target.connection_id if pr.target is not None else None


async def propose(pr: PR, inp: repo_routing.RouteInput, now: datetime) -> RepoTarget | None:
    """올릴 레포를 정한다. 목 설정이면 None. 연결이 하나면 승인, 여럿이면 제안."""
    conns = targets()
    if not conns:
        return None
    cands = [repo_routing.Candidate(c.id, c.repo or c.id, c.description, c.paths) for c in conns]
    if len(cands) == 1:
        j = repo_routing.score_rules(inp, cands)
        return RepoTarget(
            connection_id=j.connection_id,
            repo=j.repo,
            status="approved",
            confidence=j.confidence,
            reasons=j.reasons,
            candidates=[RepoCandidate(**c) for c in j.candidates],
            decided_by=AUTO_ONE,
            decided_at=now,
        )
    j = await repo_routing.decide(inp, cands)
    return RepoTarget(
        connection_id=j.connection_id,
        repo=j.repo,
        status="proposed",
        confidence=j.confidence,
        reasons=j.reasons,
        candidates=[RepoCandidate(**c) for c in j.candidates],
    )


def proposed_text(pr: PR) -> str:
    """제안 활동 한 줄."""
    t = pr.target
    assert t is not None
    return f"{pr.id} → {t.repo} 제안 (확신 {t.confidence}) · 근거 {' · '.join(t.reasons)}"


def change_body(pr: PR, request_id: str | None, spec_id: str | None, para_id: str | None) -> str:
    """⑤ 역추적용 본문. 요청·기획서·문단 ID를 한 줄씩 적는다."""
    rows = [
        f"요청: {request_id or '-'}",
        f"기획서: {spec_id or '-'}",
        f"문단: {f'{spec_id}:{para_id}' if spec_id and para_id else (para_id or '-')}",
        f"작업: {pr.task}",
        f"바꿀 경로: {', '.join(pr.changed_paths) or '-'}",
        "",
        "planning 에이전트가 만든 PR이다 (라벨 generated-pr). 실행 관문을 지난 뒤 올렸다.",
    ]
    return "\n".join(rows)


def change_files(pr: PR, body: str, spec_id: str | None) -> dict[str, str]:
    """커밋할 파일. 코드 생성 전까지는 작업 기록 파일 하나만 올린다."""
    folder = f"{NOTE_FILE_DIR}/{(spec_id or 'spec').lower()}"
    return {f"{folder}/{pr.task.lower()}.md": f"# {pr.title}\n\n{body}\n"}


async def open_pr(
    pr: PR,
    request_id: str | None,
    spec_id: str | None,
    para_id: str | None,
    connection_id: str | None = None,
    files: dict[str, str] | None = None,
    body: str | None = None,
) -> Note | None:
    """③ PR을 실제로 올리고 번호·주소를 PR에 적는다. 목 설정이면 None.

    연결을 안 주면 PR의 승인된 레포(`target`), 그것도 없으면 첫 연결로 올린다.
    `files`·`body`를 주면 그것을 커밋·본문으로 쓴다 (코딩 에이전트). 안 주면 작업 기록 파일 하나."""
    try:
        adapter = active(connection_id or _connection_of(pr))
    except VcsError as exc:
        return Note(False, "vcs.open_failed", f"{pr.id} 형상관리에 올리지 못함 ({exc.code})")
    if adapter is None:
        return None
    if adapter.capabilities.read_only:
        return Note(False, "vcs.read_only", f"{pr.id} 읽기 전용 모드 → 사람 반영 필요")
    body = body or change_body(pr, request_id, spec_id, para_id)
    try:
        opened = await adapter.open_change(
            branch=pr.branch,
            title=pr.title,
            body=body,
            files=files or change_files(pr, body, spec_id),
        )
    except VcsError as exc:
        return Note(False, "vcs.open_failed", f"{pr.id} 형상관리에 올리지 못함 ({exc.code})")
    pr.vcs_number = opened.number
    pr.url = opened.url
    return Note(True, "vcs.opened", f"{pr.id} → 형상관리 변경 #{opened.number} ({opened.branch})")


async def update_pr(pr: PR, files: dict[str, str], message: str) -> Note | None:
    """③-2 올린 PR 브랜치에 수정 커밋을 더한다. 목 설정이면 None. 실패하면 ok False."""
    try:
        adapter = active(_connection_of(pr))
    except VcsError as exc:
        return Note(False, "vcs.update_failed", f"{pr.id} 수정 커밋 실패 ({exc.code})")
    if adapter is None:
        return None
    if adapter.capabilities.read_only:
        return Note(False, "vcs.read_only", f"{pr.id} 읽기 전용 모드 → 사람 반영 필요")
    if pr.vcs_number is None:
        return Note(False, "vcs.update_failed", f"{pr.id} 형상관리 변경 번호가 없어 커밋하지 않음")
    try:
        await adapter.update_change(pr.vcs_number, files, message)
    except VcsError as exc:
        return Note(False, "vcs.update_failed", f"{pr.id} 수정 커밋 실패 ({exc.code})")
    return Note(True, "vcs.updated", f"{pr.id} → 형상관리 변경 #{pr.vcs_number} 수정 커밋")


async def merge_pr(pr: PR, connection_id: str | None = None) -> Note | None:
    """④ 실제로 병합한다. 목 설정이면 None. 실패하면 ok False (PR은 병합하지 않은 채로 둔다).

    PR을 올린 연결(`target`)에서 병합한다."""
    try:
        adapter = active(connection_id or _connection_of(pr))
    except VcsError as exc:
        return Note(False, "vcs.merge_failed", f"{pr.id} 형상관리 병합 실패 ({exc.code})")
    if adapter is None:
        return None
    if adapter.capabilities.read_only:
        return Note(False, "vcs.read_only", f"{pr.id} 읽기 전용 모드 → 사람 반영 필요")
    if pr.vcs_number is None:
        return Note(False, "vcs.merge_failed", f"{pr.id} 형상관리 변경 번호가 없어 병합하지 않음")
    try:
        await adapter.merge_change(pr.vcs_number)
    except VcsError as exc:
        # 앞선 시도에서 병합만 되고 상태 반영이 버려졌으면 이미 병합된 것으로 본다
        if exc.code != "conflict" or not await _already_merged(adapter, pr.vcs_number):
            return Note(False, "vcs.merge_failed", f"{pr.id} 형상관리 병합 실패 ({exc.code})")
    return Note(True, "vcs.merged", f"{pr.id} → 형상관리 변경 #{pr.vcs_number} 병합")


async def _already_merged(adapter: VcsAdapter, number: int) -> bool:
    try:
        return (await adapter.get_change(number)).merged
    except VcsError:
        return False
