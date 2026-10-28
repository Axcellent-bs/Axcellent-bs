"""코딩 에이전트 (개발 역할). 개발팀원이 개발 화면에 쓴 기획을 연결된 레포에 실제로 구현한다.

흐름: 파일 목록 읽기 → 고칠 파일 고르기(LLM) → 파일마다 새 글 쓰기(LLM) → 관문 뒤 커밋·PR →
완료 기준으로 리뷰(LLM) → 불일치면 한 번 고쳐 같은 브랜치에 수정 커밋 → 기존 자동 머지 규칙.

- 상태 잠금 밖에서 돈다. 단계마다 서비스의 작은 액션(`coding_*`)으로 진행 상태를 적는다 (SSE).
- 한도: 파일 5개, 바뀐 줄 합 1500줄, 고칠 파일 600줄. 넘으면 PR 없이 사람 확인(`waiting_human`).
- 줄 수는 difflib로 코드가 센다. LLM은 파일 고르기·글 쓰기·리뷰만 한다.
- 기획 글·파일 글은 데이터 블록으로만 넣는다. 토큰은 보내지 않는다.
- LLM은 `PLANNING_LLM_*`·`LLM_*` 설정. 테스트는 `set_llm()`으로 가짜를 끼운다.
- LLM·형상관리 오류는 `failed` + 짧은 이유 한 줄 (예외 내용·비밀 값은 넣지 않는다).
"""

from __future__ import annotations

import asyncio
import difflib
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from ax_agent_template.llm import LLMClient, LLMSettings
from planning_agent import vcs_bridge
from planning_agent.domain.models import CodingFile
from planning_agent.vcs.base import VcsAdapter, VcsError, check_path

if TYPE_CHECKING:
    from planning_agent.domain.service import PlanningService

MAX_FILES = 5  # 한 번에 고칠 파일 수
MAX_TOTAL_LINES = 1500  # 바뀐 줄(더한 줄 + 지운 줄) 합
MAX_FILE_LINES = 600  # 통째로 다시 쓸 수 있는 파일 길이
MAX_LISTING = 400  # LLM에 보여 줄 파일 경로 수
MAX_PLAN_CHARS = 8000
MAX_DONE_LINES = 20
MAX_ATTEMPTS = 2  # 첫 작성 + 리뷰 불일치 뒤 수정 1번
MAX_NOTES = 5
MAX_DIFF_CHARS = 12000  # 리뷰에 보낼 파일 하나의 diff 길이
LLM_TIMEOUT_SECONDS = 180.0
DEV_TEAM = "dev_team"
DEFAULT_DONE = "기획에 적은 동작이 그대로 구현되고 기존 동작은 바뀌지 않는다"

# 고를 파일 목록에서 뺄 것 (잠금 파일·빌드 결과·의존성·이미지 등)
_SKIP_DIRS = {
    "node_modules", "dist", "build", "out", "coverage", "vendor", ".git", ".venv", "venv",
    "__pycache__", ".next", ".nuxt", ".cache", ".idea", ".vscode", "target",
}  # fmt: skip
_SKIP_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "go.sum", "composer.lock", "Gemfile.lock", ".DS_Store",
}  # fmt: skip
_SKIP_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".pdf", ".zip", ".gz",
    ".tar", ".tgz", ".woff", ".woff2", ".ttf", ".otf", ".eot", ".mp4", ".mp3", ".mov", ".wav",
    ".exe", ".dll", ".so", ".dylib", ".jar", ".class", ".pyc", ".lock", ".map", ".min.js",
    ".min.css",
)  # fmt: skip


# ---------------------------------------------------------------- LLM 응답 모양


class FilePick(BaseModel):
    path: str
    action: Literal["modify", "create"] = "modify"
    reason: str = ""


class CodePlan(BaseModel):
    files: list[FilePick] = Field(default_factory=list)
    summary: str = ""


class FileWrite(BaseModel):
    content: str
    reason: str = ""


class CodeReview(BaseModel):
    verdict: Literal["통과", "불일치"]
    notes: list[str] = Field(default_factory=list)


PLAN_PROMPT = (
    "너는 개발팀의 코딩 에이전트다. 아래 데이터의 기획(plan)과 완료 기준(done)을 구현하려면 "
    "어떤 파일을 고쳐야 하는지 고른다.\n"
    f"- 파일은 {MAX_FILES}개 이하로, 꼭 필요한 것만 고른다.\n"
    "- 있는 파일을 고치면 action modify, 경로는 files 목록에 있는 값을 그대로 쓴다.\n"
    "- 새 파일이면 action create.\n"
    "- path_hints가 있으면 그 경로를 먼저 본다.\n"
    "- reason은 그 파일을 고치는 이유 한 줄, summary는 바꿀 내용 한 줄 요약이다.\n"
    '- JSON {"files": [{"path": "...", "action": "modify", "reason": "..."}], "summary": "..."}'
    " 로만 답한다."
)

WRITE_PROMPT = (
    "너는 개발팀의 코딩 에이전트다. 아래 데이터의 파일 하나를 기획(plan)과 완료 기준(done)에 "
    "맞게 고친다.\n"
    "- current는 지금 파일 글이다 (새 파일이면 빈 글). 필요한 곳만 바꾸고 나머지는 그대로 둔다.\n"
    "- review_notes가 있으면 지난 리뷰의 지적이다. 모두 반영한다.\n"
    "- content에는 파일 전체의 새 글만 넣는다 (설명·코드 블록 표시 없이).\n"
    "- reason은 바꾼 내용 한 줄이다.\n"
    '- JSON {"content": "...", "reason": "..."} 로만 답한다.'
)

REVIEW_PROMPT = (
    "너는 개발팀의 코드 리뷰어다. 아래 데이터의 변경(diffs)이 기획(plan)과 완료 기준(done)을 "
    "채우는지 본다.\n"
    "- 완료 기준을 모두 채우고 눈에 띄는 오류가 없으면 verdict 통과, 아니면 불일치.\n"
    f"- notes는 짧은 한국어 지적 줄이다 ({MAX_NOTES}줄 이하). 불일치면 고칠 점을 적는다.\n"
    '- JSON {"verdict": "통과", "notes": ["..."]} 로만 답한다.'
)


# ---------------------------------------------------------------- LLM 끼우기

_llm: Any = None
_llm_set = False


def set_llm(client: Any) -> None:
    """LLM 클라이언트를 바꿔 끼운다 (테스트용). None을 끼우면 LLM이 없는 것으로 본다."""
    global _llm, _llm_set
    _llm, _llm_set = client, True


def reset_llm() -> None:
    """바꿔 끼운 것을 빼고 설정을 다시 읽는다."""
    global _llm, _llm_set
    _llm, _llm_set = None, False


def get_llm() -> LLMClient | None:
    """바꿔 끼운 것, 아니면 planning LLM 설정(`PLANNING_LLM_*`·`LLM_*`)이 있을 때만 클라이언트."""
    if _llm_set:
        return _llm
    settings = LLMSettings.from_env("planning")
    if settings.model and settings.base_url:
        return LLMClient("planning", settings=settings)
    return None


def model_name(client: Any) -> str | None:
    settings = getattr(client, "settings", None)
    return getattr(settings, "model", None) or None


# ---------------------------------------------------------------- 실행 단위


@dataclass(frozen=True)
class Job:
    """코딩 작업 하나의 입력 (상태에서 꺼낸 값). `run_id`가 바뀌면 이 실행은 버린다."""

    run_id: int
    task_id: str
    spec_id: str
    request_id: str
    ctx: str
    title: str
    plan: str
    done: tuple[str, ...]
    hints: tuple[str, ...]
    connection: str | None


class CoderGone(Exception):
    """작업이 없어졌거나(초기화) 새 실행으로 바뀌었다. 조용히 멈춘다."""


@dataclass(eq=False)
class CoderStop(Exception):
    """여기서 멈춘다. status는 failed 또는 waiting_human, message는 짧은 이유 한 줄."""

    status: str
    message: str
    point: str | None = None
    text: str | None = None
    fields: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- 코드로 하는 일


def skip_path(path: str) -> bool:
    """고를 파일 목록에서 뺄 경로인가."""
    parts = path.split("/")
    name = parts[-1]
    low = name.lower()
    return (
        any(p in _SKIP_DIRS for p in parts[:-1]) or name in _SKIP_NAMES or low.endswith(_SKIP_EXT)
    )


def _under(path: str, prefix: str) -> bool:
    base = prefix.rstrip("/")
    return path == base or path.startswith(base + "/")


def listing_for(paths: list[str], hints: tuple[str, ...] = ()) -> list[str]:
    """LLM에 보여 줄 파일 경로: 뺄 것을 빼고, 힌트 경로 안을 앞에 두고, 400개까지."""
    rows = [p for p in paths if not skip_path(p)]
    hinted = [p for p in rows if any(_under(p, h) for h in hints)]
    rest = [p for p in rows if p not in set(hinted)]
    return (hinted + rest)[:MAX_LISTING]


def clean_path(raw: str) -> str | None:
    """LLM이 준 경로를 다듬는다. 쓸 수 없는 경로(`..`·절대 경로 등)면 None."""
    p = str(raw or "").strip()
    while p.startswith("./"):
        p = p[2:]
    try:
        return check_path(p)
    except VcsError:
        return None


def check_picks(picks: list[FilePick], existing: set[str]) -> tuple[list[FilePick], int]:
    """고른 파일을 검사한다. (쓸 파일, 뺀 개수).

    - `..`·절대 경로는 뺀다. 고칠 파일(modify)이 목록에 없으면 뺀다.
    - 새 파일(create)인데 이미 있으면 고칠 파일로 본다. 같은 경로는 한 번만.
    - 보호 경로도 그대로 둔다 (머지 때 기존 규칙이 사람 확인으로 보낸다)."""
    out: list[FilePick] = []
    seen: set[str] = set()
    dropped = 0
    for pick in picks:
        path = clean_path(pick.path)
        if path is None or path in seen:
            dropped += 1
            continue
        action = pick.action
        if action == "modify" and path not in existing:
            dropped += 1
            continue
        if action == "create" and path in existing:
            action = "modify"
        seen.add(path)
        out.append(FilePick(path=path, action=action, reason=" ".join(pick.reason.split())[:200]))
    return out, dropped


def _lines(text: str) -> list[str]:
    return text.splitlines()


def diff_counts(old: str, new: str) -> tuple[int, int]:
    """(더한 줄, 지운 줄). 줄 단위로 비교한다."""
    a, b = _lines(old), _lines(new)
    adds = dels = 0
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            dels += i2 - i1
        if tag in ("replace", "insert"):
            adds += j2 - j1
    return adds, dels


def unified(path: str, old: str, new: str) -> str:
    """리뷰에 보낼 diff (길면 자른다)."""
    text = "\n".join(
        difflib.unified_diff(_lines(old), _lines(new), f"a/{path}", f"b/{path}", lineterm="")
    )
    return text if len(text) <= MAX_DIFF_CHARS else text[:MAX_DIFF_CHARS] + "\n… (잘림)"


def file_rows(
    originals: dict[str, str], contents: dict[str, str], reasons: dict[str, str]
) -> list[CodingFile]:
    rows = []
    for path, new in contents.items():
        adds, dels = diff_counts(originals.get(path, ""), new)
        rows.append(
            CodingFile(path=path, additions=adds, deletions=dels, reason=reasons.get(path, ""))
        )
    return rows


def total_lines(rows: list[CodingFile]) -> int:
    return sum(r.additions + r.deletions for r in rows)


def write_text(rows: list[CodingFile]) -> str:
    adds = sum(r.additions for r in rows)
    dels = sum(r.deletions for r in rows)
    return f"{len(rows)}개 파일 · +{adds} −{dels}줄"


def area_of(paths: list[str]) -> str:
    """PR 영역 표시: 모든 경로의 공통 폴더 (없으면 첫 폴더들)."""
    if not paths:
        return "-"
    split = [p.split("/")[:-1] for p in paths]
    common: list[str] = []
    for parts in zip(*split, strict=False):
        if len(set(parts)) != 1:
            break
        common.append(parts[0])
    if common:
        return "/".join(common) + "/"
    tops = sorted({p.split("/")[0] for p in paths})
    return ", ".join(tops)


def pr_body(job: Job, rows: list[CodingFile], summary: str) -> str:
    """PR 본문: 기획·완료 기준·파일별 이유 + 역추적 줄 (요청·기획서·문단)."""
    plan = job.plan if len(job.plan) <= 2000 else job.plan[:2000] + "…"
    out = [
        "## 기획",
        plan,
        "",
        "## 완료 기준",
        *[f"- C{i}: {line}" for i, line in enumerate(job.done, 1)],
        "",
        "## 바꾼 파일",
        *[f"- `{r.path}` (+{r.additions} −{r.deletions}) {r.reason}".rstrip() for r in rows],
    ]
    if summary:
        out += ["", f"요약: {summary}"]
    out += [
        "",
        f"요청: {job.request_id}",
        f"기획서: {job.spec_id}",
        f"문단: {job.spec_id}:R1",
        f"작업: {job.task_id}",
        "",
        "planning 코딩 에이전트가 만든 PR이다 (라벨 generated-pr). 실행 관문을 지난 뒤 올렸다.",
    ]
    return "\n".join(out)


# ---------------------------------------------------------------- LLM·형상관리 부르기


async def _ask[T: BaseModel](
    llm: Any, step: str, prompt: str, schema: type[T], data: dict, ctx: str
) -> T:
    try:
        return await asyncio.wait_for(
            llm.structured(prompt, schema, untrusted=data, context_id=ctx),
            LLM_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        raise CoderStop("failed", f"LLM {step} 시간 초과") from None
    except Exception as exc:  # noqa: BLE001 - 예외 내용 대신 종류만 남긴다
        raise CoderStop("failed", f"LLM {step} 실패 ({type(exc).__name__})") from None


async def _vcs[R](step: str, call: Awaitable[R]) -> R:
    try:
        return await call
    except VcsError as exc:
        raise CoderStop("failed", f"형상관리 {step} 실패 ({exc.code})") from None


def _adapter(connection: str | None) -> VcsAdapter:
    try:
        adapter = vcs_bridge.active(connection)
    except VcsError as exc:
        raise CoderStop("failed", f"형상관리 연결 실패 ({exc.code})") from None
    if adapter is None:
        raise CoderStop("failed", "형상관리 연결이 없다")
    if adapter.capabilities.read_only:
        raise CoderStop("waiting_human", "읽기 전용 연결이라 커밋하지 않음 → 사람 반영 필요")
    return adapter


def _base(job: Job) -> dict[str, Any]:
    return {"title": job.title, "plan": job.plan, "done": list(job.done)}


async def _write_all(
    llm: Any,
    job: Job,
    picks: list[FilePick],
    current: dict[str, str],
    notes: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """파일마다 새 글을 받는다. (경로 → 새 글, 경로 → 이유)."""
    contents: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for pick in picks:
        data = {
            **_base(job),
            "path": pick.path,
            "action": pick.action,
            "why": pick.reason,
            "current": current.get(pick.path, ""),
            "review_notes": notes,
        }
        got = await _ask(llm, f"파일 쓰기 {pick.path}", WRITE_PROMPT, FileWrite, data, job.ctx)
        contents[pick.path] = got.content
        reasons[pick.path] = " ".join((got.reason or pick.reason).split())[:200]
    return contents, reasons


# ---------------------------------------------------------------- 흐름


async def run(svc: PlanningService, job: Job) -> None:
    """코딩 작업 하나를 끝까지 돌린다. 어떤 실패든 상태에 한 줄로 남기고 멈춘다."""
    from planning_agent.domain.service import GateRefused

    try:
        try:
            await _run(svc, job)
        except GateRefused as exc:
            status = "waiting_human" if exc.decision.status == "held" else "failed"
            raise CoderStop(
                status, f"실행 관문이 {exc.action}을 허락하지 않음 ({exc.decision.status})"
            ) from None
        except (CoderStop, CoderGone):
            raise
        except Exception as exc:  # noqa: BLE001 - 어떤 오류든 상태를 실패로 남긴다
            raise CoderStop("failed", f"코딩 에이전트 오류 ({type(exc).__name__})") from None
    except CoderGone:
        return
    except CoderStop as stop:
        try:
            await svc.coding_step(
                job,
                stop.status,
                error=stop.message,
                point=stop.point or ("code.failed" if stop.status == "failed" else None),
                text=stop.text or stop.message,
                **stop.fields,
            )
        except CoderGone:
            return


async def _run(svc: PlanningService, job: Job) -> None:
    llm = get_llm()
    if llm is None:
        raise CoderStop("failed", "LLM 설정이 없다")
    adapter = _adapter(job.connection)

    # 1. 읽기
    await svc.coding_step(job, "reading")
    listed = await _vcs("파일 목록", adapter.list_files())
    listing = listing_for(listed, job.hints)
    await svc.coding_step(job, "reading", point="code.read", text=f"파일 {len(listing)}개 읽음")

    # 2. 고칠 파일 고르기
    data = {**_base(job), "path_hints": list(job.hints), "files": listing}
    plan = await _ask(llm, "파일 고르기", PLAN_PROMPT, CodePlan, data, job.ctx)
    picks, dropped = check_picks(plan.files, set(listed))
    summary = " ".join(plan.summary.split())[:300] or None
    chosen = [CodingFile(path=p.path, reason=p.reason) for p in picks]
    text = f"고칠 파일 {len(picks)}개 선택" + (f" (뺀 경로 {dropped}개)" if dropped else "")
    if not picks:
        raise CoderStop("failed", "고칠 파일을 고르지 못함", "code.plan", text)
    if len(picks) > MAX_FILES:
        raise CoderStop(
            "waiting_human",
            f"고칠 파일 {len(picks)}개 > {MAX_FILES}개 → 사람 확인",
            "code.plan",
            f"{text} → 한도 {MAX_FILES}개 초과, 사람 확인",
            {"files": chosen, "summary": summary},
        )
    await svc.coding_step(
        job, "writing", point="code.plan", text=text, files=chosen, summary=summary
    )

    # 3. 쓰기
    originals: dict[str, str] = {}
    for pick in picks:
        if pick.action == "create":
            originals[pick.path] = ""
            continue
        body = await _vcs(f"파일 읽기 {pick.path}", adapter.read_file(pick.path))
        n = len(_lines(body))
        if n > MAX_FILE_LINES:
            raise CoderStop("waiting_human", f"{pick.path} {n}줄 > {MAX_FILE_LINES}줄 → 사람 확인")
        originals[pick.path] = body
    contents, reasons = await _write_all(llm, job, picks, originals, [])
    contents = {p: c for p, c in contents.items() if c != originals.get(p)}
    if not contents:
        raise CoderStop("failed", "바뀐 내용이 없음", "code.write", "바뀐 파일 0개")
    rows = file_rows(originals, contents, reasons)
    if total_lines(rows) > MAX_TOTAL_LINES:
        raise CoderStop(
            "waiting_human",
            f"바뀐 줄 {total_lines(rows)}줄 > {MAX_TOTAL_LINES}줄 → 사람 확인 (PR 없음)",
            "code.write",
            f"{write_text(rows)} → 한도 {MAX_TOTAL_LINES}줄 초과, 사람 확인",
            {"files": rows, "attempts": 1},
        )
    await svc.coding_step(
        job, "writing", point="code.write", text=write_text(rows), files=rows, attempts=1
    )

    # 4. 커밋·PR (관문 generate_pr 뒤)
    if not await svc.coding_open(job, contents, rows, pr_body(job, rows, summary or "")):
        return
    picks = [p for p in picks if p.path in contents]

    # 5. 리뷰 → 불일치면 한 번 고친다
    attempts = 1
    while True:
        await svc.coding_step(job, "reviewing")
        diffs = [
            {"path": p, "diff": unified(p, originals.get(p, ""), c)} for p, c in contents.items()
        ]
        review = await _ask(
            llm, "리뷰", REVIEW_PROMPT, CodeReview, {**_base(job), "diffs": diffs}, job.ctx
        )
        notes = [" ".join(n.split())[:200] for n in review.notes if str(n).strip()][:MAX_NOTES]
        if not await svc.coding_review(job, review.verdict, notes):
            return
        attempts += 1
        await svc.coding_step(job, "writing", attempts=attempts)
        fixed, fix_reasons = await _write_all(llm, job, picks, contents, notes)
        changed = {p: c for p, c in fixed.items() if c != contents.get(p)}
        if not changed:
            raise CoderStop("waiting_human", "리뷰 지적을 반영한 수정이 없음 → 사람 확인")
        contents.update(changed)
        reasons.update({p: fix_reasons[p] for p in changed})
        rows = file_rows(originals, contents, reasons)
        if total_lines(rows) > MAX_TOTAL_LINES:
            raise CoderStop(
                "waiting_human",
                f"수정 뒤 바뀐 줄 {total_lines(rows)}줄 > {MAX_TOTAL_LINES}줄 → 사람 확인",
            )
        await svc.coding_step(job, "writing", point="code.write", text=write_text(rows), files=rows)
        if not await svc.coding_fix(job, changed, rows):
            return
