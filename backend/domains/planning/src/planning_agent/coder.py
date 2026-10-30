"""코딩 에이전트 (개발 역할). 개발팀원이 개발 화면에 쓴 기획을 연결된 레포에 실제로 구현한다.

흐름: 파일 목록 읽기 → 후보 파일 고르기(LLM, 이름만) → 후보 내용을 보고 확정(LLM) →
파일마다 새 글 쓰기(LLM) → 완료 기준으로 리뷰(LLM) → 불일치면 한 번 고쳐(리뷰가 짚은 새
파일도 더해) 다시 리뷰 → 관문 뒤 마지막 결과만 커밋 하나로 PR → 리뷰 결과를 PR에 남기고
기존 자동 머지 규칙.

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
import re
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
MAX_CANDIDATES = 8  # 내용을 보고 확정할 후보 파일 수
MAX_PREVIEW_CHARS = 3000  # 후보 파일 하나의 미리보기 길이
MAX_OUTLINE_LINES = 60  # 긴 파일은 앞부분 + 뼈대 줄(선택자·함수·export 등)만
LLM_TIMEOUT_SECONDS = 400.0  # 단계 하나 전체 (요청 재시도 포함)
LLM_HTTP_TIMEOUT_SECONDS = 180.0  # 요청 하나. 기본 60초면 긴 파일을 쓰다 끊겨 다시 요청한다
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


class Edit(BaseModel):
    """바꿀 부분 하나. find는 지금 파일에 꼭 한 번 나오는 글이다."""

    find: str
    replace: str


class FileWrite(BaseModel):
    """고친 파일. 고칠 때는 edits(바꿀 부분만), 새 파일이거나 edits로 못 쓸 때만 content(전체)."""

    edits: list[Edit] = Field(default_factory=list)
    content: str = ""
    reason: str = ""


class CodeReview(BaseModel):
    verdict: Literal["통과", "불일치"]
    notes: list[str] = Field(default_factory=list)
    fix_files: list[str] = Field(default_factory=list)  # 다시 고칠 파일 (비면 전부)


PLAN_PROMPT = (
    "너는 개발팀의 코딩 에이전트다. 아래 데이터의 기획(plan)과 완료 기준(done)을 구현하려면 "
    "어떤 파일을 고쳐야 하는지 후보를 고른다. 다음 단계에서 후보의 내용을 보고 확정한다.\n"
    f"- 후보는 {MAX_CANDIDATES}개 이하. 고칠 파일과 함께 바뀌어야 할 수 있는 파일(설정·테마·"
    "공용 스타일·같은 값을 쓰는 곳)도 넣는다.\n"
    "- 있는 파일을 고치면 action modify, 경로는 files 목록에 있는 값을 그대로 쓴다.\n"
    "- 새 파일이면 action create.\n"
    "- path_hints가 있으면 그 경로를 먼저 본다.\n"
    "- reason은 그 파일을 고치는 이유 한 줄, summary는 바꿀 내용 한 줄 요약이다.\n"
    '- JSON {"files": [{"path": "...", "action": "modify", "reason": "..."}], "summary": "..."}'
    " 로만 답한다."
)

CONFIRM_PROMPT = (
    "너는 개발팀의 코딩 에이전트다. 아래 데이터의 후보 파일(candidates)에는 지금 내용의 "
    "미리보기(preview)가 있다. 기획(plan)과 완료 기준(done)을 구현할 파일을 확정한다.\n"
    f"- 파일은 {MAX_FILES}개 이하로, 내용을 보고 실제로 고쳐야 하는 것만 남긴다.\n"
    "- 같은 값이 여러 곳(예: :root와 테마 클래스, 설정 파일)에 있으면 기획이 말한 화면에 실제로 "
    "적용되는 곳을 고른다. reason에 어느 부분을 고치는지 적는다.\n"
    "- 후보 밖의 파일이 필요하면 files 목록의 경로 그대로 action modify로 더한다. "
    "새 파일은 create.\n"
    '- JSON {"files": [{"path": "...", "action": "modify", "reason": "..."}], "summary": "..."}'
    " 로만 답한다."
)

WRITE_PROMPT = (
    "너는 개발팀의 코딩 에이전트다. 아래 데이터의 파일 하나를 기획(plan)과 완료 기준(done)에 "
    "맞게 고친다.\n"
    "- current는 지금 파일 글이다 (새 파일이면 빈 글). 필요한 곳만 바꾸고 나머지는 그대로 둔다.\n"
    "- instruction이 있으면 사람이 직접 준 수정 지시다. 무엇보다 먼저 따른다.\n"
    "- review_notes가 있으면 지난 리뷰의 지적이다. 모두 반영한다.\n"
    "- why는 이 파일에서 고칠 부분이다. others는 같은 작업에서 다른 파일이 맡은 일이니 "
    "여기서 겹쳐 하지 않는다.\n"
    "- 기획이 말한 값·화면만 바꾼다. 기획에 없는 값(다른 색·변수·문구)은 그대로 둔다.\n"
    "- 같은 값이 여러 곳(예: :root와 테마 클래스)에 있으면 기획이 말한 화면에 실제로 "
    "적용되는 곳을 고친다.\n"
    "- 있는 파일(action modify)은 바꿀 부분만 edits로 준다. find는 current에 꼭 한 번 나오는 "
    "글을 그대로 복사하고(앞뒤 몇 줄 포함), replace는 바꾼 글이다. content는 비운다.\n"
    "- 새 파일(action create)이거나 edits로 쓰기 어려울 때만 content에 파일 전체 글을 넣는다 "
    "(설명·코드 블록 표시 없이).\n"
    "- reason은 바꾼 내용 한 줄이다.\n"
    '- JSON {"edits": [{"find": "...", "replace": "..."}], "content": "", "reason": "..."} 로만 '
    "답한다."
)

FULL_PROMPT = (
    "너는 개발팀의 코딩 에이전트다. 바꿀 부분(edits)을 지금 파일에 맞출 수 없었다. "
    "이번에는 파일 전체의 새 글을 content에 넣는다 (edits는 비운다). 필요한 곳만 바꾸고 "
    "나머지는 그대로 둔다.\n"
    '- JSON {"edits": [], "content": "...", "reason": "..."} 로만 답한다.'
)

REVIEW_PROMPT = (
    "너는 개발팀의 코드 리뷰어다. 아래 데이터의 변경(diffs)이 기획(plan)과 완료 기준(done)을 "
    "채우는지 본다.\n"
    "- 너는 diff만 볼 수 있다. 빌드·테스트·화면 결과는 볼 수 없으니 그 증거가 없다는 이유로 "
    "불일치를 주지 않는다. diff로 확인할 수 있는 것만 판단한다.\n"
    "- 완료 기준을 모두 채우고 눈에 띄는 오류가 없으면 verdict 통과, 아니면 불일치.\n"
    "- 기획에 없는 값·동작을 바꿨으면 불일치로 짚는다.\n"
    f"- notes는 짧은 한국어 지적 줄이다 ({MAX_NOTES}줄 이하). 불일치면 고칠 점을 적는다.\n"
    "- 불일치면 fix_files에 다시 고쳐야 하는 파일 경로를 적는다. diff에 없는 파일을 고치거나 "
    "새로 만들어야 하면 그 경로도 적는다 (있는 파일은 레포 경로 그대로).\n"
    '- JSON {"verdict": "통과", "notes": ["..."], "fix_files": []} 로만 답한다.'
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
        return LLMClient("planning", settings=settings, timeout=LLM_HTTP_TIMEOUT_SECONDS)
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
    instruction: str | None = None  # 사람이 대기 중인 PR에 준 수정 지시
    skip_queue: bool = False  # '지금 진행': 같은 파일 작업이 있어도 기다리지 않는다


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


_OUTLINE = re.compile(
    r"^\s*(export\s|function\s|class\s|def\s|async\s|interface\s|type\s|const\s+[A-Z_]"
    r"|@media|@layer|:root|[.#\[][^;]*\{\s*$|[a-zA-Z_-]+\s*:\s*\{\s*$)"
)


def preview_of(body: str) -> str:
    """후보 파일 미리보기. 짧으면 전체, 길면 앞부분 + 뼈대 줄(줄 번호 붙임)."""
    if len(body) <= MAX_PREVIEW_CHARS:
        return body
    lines = _lines(body)
    head = body[: MAX_PREVIEW_CHARS // 2]
    shown = head.count("\n")
    marks = [
        f"{i + 1}: {line.strip()[:120]}"
        for i, line in enumerate(lines)
        if i >= shown and _OUTLINE.match(line)
    ][:MAX_OUTLINE_LINES]
    return f"{head}\n… ({len(lines)}줄 중 앞부분, 아래는 뼈대 줄)\n" + "\n".join(marks)


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
        raise CoderStop(
            "failed", f"형상관리 {step} 실패 ({exc.code}){vcs_hint(exc.code)}"
        ) from None


def vcs_hint(code: str) -> str:
    """형상관리 오류마다 사람이 할 일 (없으면 빈 글)."""
    if code == "unauthorized":
        return " → 토큰 만료·권한 확인, 레포 연결 화면에서 다시 연결"
    if code == "rate_limited":
        return " → 호출 한도, 잠시 뒤 다시 시도"
    return ""


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


def apply_edits(current: str, edits: list[Edit]) -> str | None:
    """바꿀 부분을 차례로 적용한다. find가 꼭 한 번 나오지 않으면 None (전체 쓰기로 다시)."""
    text = current
    for e in edits:
        if not e.find or text.count(e.find) != 1:
            return None
        text = text.replace(e.find, e.replace, 1)
    return text


async def _write_one(
    llm: Any,
    job: Job,
    pick: FilePick,
    current: str,
    notes: list[str],
    others: list[FilePick] | None = None,
) -> tuple[str, str]:
    """파일 하나의 새 글과 이유. 바꿀 부분(edits)을 적용하고, 못 맞추면 전체를 다시 받는다."""
    data = {
        **_base(job),
        "path": pick.path,
        "action": pick.action,
        "why": pick.reason,
        "others": [{"path": o.path, "why": o.reason} for o in others or [] if o.path != pick.path],
        "current": current,
        "review_notes": notes,
        "instruction": job.instruction,
    }
    got = await _ask(llm, f"파일 쓰기 {pick.path}", WRITE_PROMPT, FileWrite, data, job.ctx)
    if got.edits and pick.action != "create":
        applied = apply_edits(current, got.edits)
        if applied is not None:
            return applied, _one_line(got.reason or pick.reason)
        got = await _ask(llm, f"파일 다시 쓰기 {pick.path}", FULL_PROMPT, FileWrite, data, job.ctx)
    reason = _one_line(got.reason or pick.reason)
    if got.content:
        return got.content, reason
    if got.edits:  # 새 파일인데 edits만 왔다: 바꿀 글을 이어 붙인다
        return "".join(e.replace for e in got.edits), reason
    return current, reason


async def _read_all(adapter: VcsAdapter, paths: list[str]) -> dict[str, str]:
    """파일 여러 개를 동시에 읽는다. (경로 → 글)."""
    bodies = await asyncio.gather(*(_vcs(f"파일 읽기 {p}", adapter.read_file(p)) for p in paths))
    return dict(zip(paths, bodies, strict=True))


async def _originals(
    adapter: VcsAdapter, picks: list[FilePick], known: dict[str, str]
) -> dict[str, str]:
    """고칠 파일의 지금 글 (새 파일은 빈 글). 읽어 둔 건 다시 읽지 않는다. 너무 길면 사람 확인."""
    need = [p.path for p in picks if p.action == "modify" and p.path not in known]
    bodies = {**known, **(await _read_all(adapter, need))}
    out: dict[str, str] = {}
    for pick in picks:
        if pick.action == "create":
            out[pick.path] = ""
            continue
        body = bodies[pick.path]
        n = len(_lines(body))
        if n > MAX_FILE_LINES:
            raise CoderStop("waiting_human", f"{pick.path} {n}줄 > {MAX_FILE_LINES}줄 → 사람 확인")
        out[pick.path] = body
    return out


async def _fix_targets(
    job: Job,
    picks: list[FilePick],
    fix_files: list[str],
    notes: list[str],
    originals: dict[str, str],
) -> list[FilePick]:
    """리뷰가 짚은 파일. 짚은 게 없으면 전부. diff에 없던 파일은 레포에 있으면 고칠 파일,
    없으면 새 파일로 더한다 (파일 한도 안에서). 더한 파일의 지금 글은 originals에 넣는다."""
    known = {p.path: p for p in picks}
    flagged: list[str] = []
    for raw in fix_files:
        path = clean_path(raw)
        if path and path not in flagged and not skip_path(path):
            flagged.append(path)
    if not flagged:
        return picks
    targets = [known[p] for p in flagged if p in known]
    extra = [p for p in flagged if p not in known][: max(0, MAX_FILES - len(picks))]
    if extra:
        adapter = _adapter(job.connection)
        existing = set(await _vcs("파일 목록", adapter.list_files()))
        why = "리뷰 지적: " + (notes[0] if notes else "추가로 고칠 파일")
        added = [
            FilePick(path=p, action="modify" if p in existing else "create", reason=why[:200])
            for p in extra
        ]
        originals.update(await _originals(adapter, added, {}))
        targets += added
    return targets or picks


def _one_line(text: str) -> str:
    return " ".join(text.split())[:200]


async def _write_all(
    llm: Any,
    job: Job,
    picks: list[FilePick],
    current: dict[str, str],
    notes: list[str],
) -> tuple[dict[str, str], dict[str, str]]:
    """파일마다 새 글을 받는다 (동시에). (경로 → 새 글, 경로 → 이유)."""
    results = await asyncio.gather(
        *(_write_one(llm, job, pick, current.get(pick.path, ""), notes, picks) for pick in picks)
    )
    contents = {pick.path: res[0] for pick, res in zip(picks, results, strict=True)}
    reasons = {pick.path: res[1] for pick, res in zip(picks, results, strict=True)}
    return contents, reasons


# ---------------------------------------------------------------- 흐름


@dataclass
class Draft:
    """리뷰까지 끝낸 코드. PR을 못 올리고 멈췄을 때 다시 시도하면
    LLM을 다시 부르지 않고 이걸로 올린다."""

    connection: str | None
    picks: list[FilePick]
    originals: dict[str, str]
    contents: dict[str, str]
    rows: list[CodingFile]
    summary: str | None
    verdicts: list[str] = field(default_factory=list)  # 리뷰 결과 차례대로
    notes: list[str] = field(default_factory=list)  # 마지막 리뷰의 지적
    attempts: int = 1
    freshened: bool = False  # PR 직전 확인에서 최신 글로 한 번 다시 썼나


def _drafts(svc: PlanningService) -> dict[str, Draft]:
    """서비스마다 작업 id → 작성해 둔 코드 (프로세스 메모리). PR이 열리면 지운다."""
    return svc.__dict__.setdefault("_coding_drafts", {})


def draft_of(svc: PlanningService, task_id: str) -> Draft | None:
    return _drafts(svc).get(task_id)


def drop_draft(svc: PlanningService, task_id: str) -> None:
    _drafts(svc).pop(task_id, None)


QUEUE_POLL_SECONDS = 2.0  # 대기열 차례 확인 간격
CI_POLL_SECONDS = 10.0  # PR CI 결과 확인 간격
CI_WAIT_SECONDS = 600.0  # CI를 기다리는 최대 시간 (넘으면 사람 확인)
NO_CI_GRACE_SECONDS = 30.0  # 이만큼 지나도 검사가 하나도 없으면 CI 없는 레포로 본다


async def _wait_turn(svc: PlanningService, job: Job, paths: list[str]) -> bool:
    """작업 대기열. 먼저 만든 작업이 같은 파일을 고치는 중이거나 아직 파일을 안 골랐으면
    끝날(고를) 때까지 기다린다. 기다렸으면 True. 먼저 만든 작업은 뒤 작업을 기다리지 않아
    서로 기다리다 멈추지 않는다. '지금 진행'(skip_queue)이면 기다리지 않는다."""
    if job.skip_queue:
        return False
    waited = False
    while True:
        if not svc.coding_alive(job):
            raise CoderGone(job.task_id)
        same, undecided = svc.queue_blockers(job, paths)
        if not same and not undecided:
            break
        ids = sorted({*same, *undecided})
        if not waited:
            why = (
                f"앞 작업 {', '.join(same)}가 같은 파일을 고치는 중"
                if same
                else f"앞 작업 {', '.join(undecided)}가 아직 고칠 파일을 고르는 중"
            )
            await svc.coding_step(
                job, "queued", point="code.queue", text=f"{why} → 끝나면 이어서",
                error=f"{why} → 끝나면 최신 코드로 이어서 해요", waiting_for=ids,
            )  # fmt: skip
            waited = True
        await asyncio.sleep(QUEUE_POLL_SECONDS)
    if waited:
        await svc.coding_step(
            job, "writing", point="code.queue", text="앞 작업이 끝나 최신 코드로 이어서 씀",
            error=None, waiting_for=[],
        )  # fmt: skip
    return waited


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

    # 사람 수정 지시: 열린 PR 브랜치의 지금 글 위에서 고쳐 같은 PR에 커밋한다
    if job.instruction and svc.coding_pr(job) is not None:
        await _revise(svc, job, llm, adapter)
        return

    # 다시 시도: 같은 연결로 리뷰까지 끝낸 코드가 있으면 LLM을 다시 부르지 않고 PR부터 다시 한다
    draft = draft_of(svc, job.task_id)
    if draft is not None and draft.connection == job.connection:
        await svc.coding_step(
            job,
            "writing",
            point="code.write",
            text=f"작성해 둔 코드로 PR 다시 시도 · {write_text(draft.rows)}",
            files=draft.rows,
            summary=draft.summary,
            attempts=draft.attempts,
        )
        await _open(svc, job, draft)
        return

    # 1. 읽기
    await svc.coding_step(job, "reading")
    listed = await _vcs("파일 목록", adapter.list_files())
    listing = listing_for(listed, job.hints)
    await svc.coding_step(job, "reading", point="code.read", text=f"파일 {len(listing)}개 읽음")

    # 2. 고칠 파일 고르기: 이름으로 후보 → 후보 내용(미리보기)을 보고 확정
    data = {**_base(job), "path_hints": list(job.hints), "files": listing}
    first = await _ask(llm, "후보 고르기", PLAN_PROMPT, CodePlan, data, job.ctx)
    candidates = check_picks(first.files, set(listed))[0][:MAX_CANDIDATES]
    bodies = await _read_all(adapter, [p.path for p in candidates if p.action == "modify"])
    plan = first
    if bodies:
        data["candidates"] = [
            {
                "path": p.path,
                "action": p.action,
                "reason": p.reason,
                "preview": preview_of(bodies.get(p.path, "")),
            }
            for p in candidates
        ]
        plan = await _ask(llm, "파일 확정", CONFIRM_PROMPT, CodePlan, data, job.ctx)
    picks, dropped = check_picks(plan.files, set(listed))
    summary = " ".join((plan.summary or first.summary).split())[:300] or None
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
    if await _wait_turn(svc, job, [p.path for p in picks]):
        bodies = {}  # 기다리는 동안 앞 작업이 같은 파일을 바꿨을 수 있어 최신 글을 다시 읽는다

    # 3. 쓰기
    originals = await _originals(adapter, picks, bodies)
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

    # 4. 리뷰 → 불일치면 한 번 고쳐 다시 리뷰 (PR 전)
    draft = Draft(job.connection, picks, originals, contents, rows, summary)
    draft = await _review(svc, job, llm, draft)
    _drafts(svc)[job.task_id] = draft

    # 5. 마지막 결과만 커밋 하나로 PR
    await _open(svc, job, draft)


async def _review(svc: PlanningService, job: Job, llm: Any, d: Draft) -> Draft:
    """완료 기준으로 리뷰한다. 불일치면 짚은 파일만 고쳐 다시 리뷰 (모두 PR 전, 커밋 없음).
    두 번째도 불일치거나 고친 게 없으면 그대로 돌려준다 (PR 뒤 사람 확인으로 간다)."""
    reasons = {r.path: r.reason for r in d.rows}
    d.picks = [p for p in d.picks if p.path in d.contents]
    while True:
        await svc.coding_step(job, "reviewing")
        diffs = [
            {"path": p, "diff": unified(p, d.originals.get(p, ""), c)}
            for p, c in d.contents.items()
        ]
        review = await _ask(
            llm, "리뷰", REVIEW_PROMPT, CodeReview, {**_base(job), "diffs": diffs}, job.ctx
        )
        d.notes = [" ".join(n.split())[:200] for n in review.notes if str(n).strip()][:MAX_NOTES]
        d.verdicts.append(review.verdict)
        if review.verdict == "통과" or d.attempts >= MAX_ATTEMPTS:
            return d
        d.attempts += 1
        await svc.coding_step(job, "writing", attempts=d.attempts)
        # 리뷰가 짚은 파일만 다시 쓴다 (짚은 게 없으면 전부, 새로 짚은 파일은 더한다)
        targets = await _fix_targets(job, d.picks, review.fix_files, d.notes, d.originals)
        d.picks += [t for t in targets if t not in d.picks]
        current = {t.path: d.contents.get(t.path, d.originals.get(t.path, "")) for t in targets}
        fixed, fix_reasons = await _write_all(llm, job, targets, current, d.notes)
        changed = {p: c for p, c in fixed.items() if c != current[p]}
        if not changed:
            return d
        d.contents.update(changed)
        reasons.update({p: fix_reasons[p] for p in changed})
        d.rows = file_rows(d.originals, d.contents, reasons)
        if total_lines(d.rows) > MAX_TOTAL_LINES:
            raise CoderStop(
                "waiting_human",
                f"수정 뒤 바뀐 줄 {total_lines(d.rows)}줄 > {MAX_TOTAL_LINES}줄"
                " → 사람 확인 (PR 없음)",
            )
        await svc.coding_step(
            job,
            "writing",
            point="code.write",
            text=f"리뷰 지적 반영 · {write_text(d.rows)}",
            files=d.rows,
        )


async def _freshen(svc: PlanningService, job: Job, d: Draft) -> Draft:
    """PR 올리기 직전 한 곳에서만 다시 쓴다: 읽었던 파일이 기준 브랜치에서 바뀌었으면 최신 글
    위에서 그 파일만 다시 쓰고 다시 리뷰한다 (리뷰 결과가 옛 코드 기준으로 남지 않게).
    한 번 다시 쓴 뒤에도 또 바뀌면 사람 확인. 고치기 횟수는 리뷰 고치기와 같이 센다."""
    paths = [p.path for p in d.picks if p.action == "modify" and p.path in d.contents]
    if not paths:
        return d
    latest = await _read_all(_adapter(job.connection), paths)
    stale = [p for p in paths if latest[p] != d.originals.get(p)]
    if not stale:
        return d
    if d.freshened:
        raise CoderStop(
            "waiting_human",
            f"다시 쓴 뒤에도 기준 코드가 또 바뀜 ({', '.join(stale)}) → 사람 확인 (PR 없음)",
            "code.rebase",
        )
    llm = get_llm()
    if llm is None:
        raise CoderStop("failed", "LLM 설정이 없다")
    d.freshened = True
    await svc.coding_step(
        job, "writing", point="code.rebase",
        text=f"기준 코드가 바뀐 파일 {len(stale)}개 → 최신 글 위에서 다시 씀",
    )  # fmt: skip
    reasons = {r.path: r.reason for r in d.rows}
    d.originals.update({p: latest[p] for p in stale})
    targets = [p for p in d.picks if p.path in stale]
    fixed, fix_reasons = await _write_all(llm, job, targets, {p: latest[p] for p in stale}, [])
    for path in stale:
        if fixed.get(path, latest[path]) == latest[path]:
            d.contents.pop(path, None)  # 최신 글에 이미 들어가 있으면 고칠 게 없다
        else:
            d.contents[path] = fixed[path]
            reasons[path] = fix_reasons.get(path, reasons.get(path, ""))
    if not d.contents:
        raise CoderStop("failed", "최신 코드에 이미 반영돼 바뀐 내용이 없음", "code.rebase")
    d.rows = file_rows(d.originals, d.contents, reasons)
    if total_lines(d.rows) > MAX_TOTAL_LINES:
        raise CoderStop(
            "waiting_human",
            f"다시 쓴 뒤 바뀐 줄 {total_lines(d.rows)}줄 > {MAX_TOTAL_LINES}줄"
            " → 사람 확인 (PR 없음)",
        )
    await svc.coding_step(
        job, "writing", point="code.write", text=f"최신 글 위에서 다시 씀 · {write_text(d.rows)}",
        files=d.rows,
    )  # fmt: skip
    d.verdicts.append("다시 씀")
    d = await _review(svc, job, llm, d)
    return await _freshen(svc, job, d)


async def _wait_ci(svc: PlanningService, job: Job) -> str | None:
    """PR CI가 끝날 때까지 기다린다. 통과·CI 없는 레포면 None, 아니면 사람 확인 이유.

    자동 머지·교차검증은 CI가 끝난 뒤에 본다 (교차검증 대기 시간도 그때부터 센다)."""
    pr = svc.coding_pr(job)
    if pr is None or pr.vcs_number is None:
        return None  # 목 설정: 형상관리에 올리지 않았다
    adapter = _adapter(job.connection)
    await svc.coding_step(job, "reviewing", point="code.ci", text=f"{pr.id} CI 기다림")
    loop = asyncio.get_running_loop()
    start = loop.time()
    while True:
        if not svc.coding_alive(job):
            raise CoderGone(job.task_id)
        try:
            checks = await adapter.get_checks(pr.vcs_number)
        except VcsError as exc:
            return f"{pr.id} CI 상태를 읽지 못함 ({exc.code}) → 사람 확인"
        waited = loop.time() - start
        if checks.state == "passed":
            await svc.coding_step(
                job, "reviewing", point="code.ci", text=f"{pr.id} CI 통과 ({len(checks.runs)}개)"
            )
            return None
        if checks.state == "failed":
            names = ", ".join(r.name for r in checks.runs if r.state == "failed")
            return f"{pr.id} CI 실패 ({names[:120]}) → 사람 확인"
        if not checks.runs and waited >= NO_CI_GRACE_SECONDS:
            await svc.coding_step(
                job, "reviewing", point="code.ci", text=f"{pr.id} CI 없음 → 검사 없이 이어감"
            )
            return None
        if waited >= CI_WAIT_SECONDS:
            return f"{pr.id} CI가 {int(CI_WAIT_SECONDS // 60)}분 넘게 안 끝남 → 사람 확인"
        await asyncio.sleep(CI_POLL_SECONDS)


async def _finish(svc: PlanningService, job: Job, d: Draft) -> None:
    """PR에 리뷰 결과를 남긴다. 통과면 CI를 기다린 뒤 자동 머지 규칙으로 잇는다."""
    verdict = d.verdicts[-1] if d.verdicts else "불일치"
    hold = await _wait_ci(svc, job) if verdict == "통과" else None
    earlier = [v for v in d.verdicts[:-1] if v != "다시 씀"]
    if await svc.coding_review(job, verdict, d.notes, earlier=earlier, ci_hold=hold):
        raise CoderStop("waiting_human", "리뷰 불일치 → 사람 확인")


async def _open(svc: PlanningService, job: Job, d: Draft) -> None:
    """PR 직전 최신 코드 확인 → 관문 generate_pr 뒤 커밋 하나로 PR → CI 기다림 → 리뷰 결과
    (통과·CI 통과면 자동 머지)."""
    d = await _freshen(svc, job, d)
    _drafts(svc)[job.task_id] = d
    if not await svc.coding_open(job, d.contents, d.rows, pr_body(job, d.rows, d.summary or "")):
        return
    drop_draft(svc, job.task_id)
    await _finish(svc, job, d)


async def _read_or_empty(adapter: VcsAdapter, path: str, ref: str | None = None) -> str:
    """파일 글. 없으면(새로 만든 파일 등) 빈 글."""
    try:
        return await adapter.read_file(path, ref)
    except VcsError as exc:
        if exc.code == "not_found":
            return ""
        raise CoderStop("failed", f"형상관리 파일 읽기 {path} 실패 ({exc.code})") from None


async def _revise(svc: PlanningService, job: Job, llm: Any, adapter: VcsAdapter) -> None:
    """사람 지시 → PR 브랜치 글 위에서 고치기 → 리뷰(불일치면 한 번 더) → 같은 PR에 커밋.

    리뷰가 통과하면 기존 자동 머지 규칙으로 잇는다 (걸리면 다시 사람 확인)."""
    pr = svc.coding_pr(job)
    if pr is None:
        raise CoderStop("failed", "고칠 PR이 없다")
    await svc.coding_step(job, "reading")
    paths = list(pr.changed_paths)
    base_rows = await asyncio.gather(*(_read_or_empty(adapter, p) for p in paths))
    head_rows = await asyncio.gather(*(_read_or_empty(adapter, p, pr.branch) for p in paths))
    base = dict(zip(paths, base_rows, strict=True))
    current = dict(zip(paths, head_rows, strict=True))
    picks = [
        FilePick(path=p, action="modify" if current[p] else "create", reason="사람 지시 반영")
        for p in paths
    ]
    await svc.coding_step(
        job, "writing", point="code.plan", text=f"사람 지시 · {pr.id} 파일 {len(picks)}개",
        attempts=1,
    )  # fmt: skip

    fixed, reasons = await _write_all(llm, job, picks, current, [])
    changed = {p: c for p, c in fixed.items() if c != current[p]}
    if not changed:
        raise CoderStop("waiting_human", "지시를 반영한 수정이 없음 → 사람 확인")
    contents = {**current, **changed}
    rows = file_rows(base, contents, reasons)
    if total_lines(rows) > MAX_TOTAL_LINES:
        raise CoderStop(
            "waiting_human", f"바뀐 줄 {total_lines(rows)}줄 > {MAX_TOTAL_LINES}줄 → 사람 확인"
        )
    await svc.coding_step(
        job, "writing", point="code.write", text=f"사람 지시 반영 · {write_text(rows)}", files=rows
    )

    d = await _review(svc, job, llm, Draft(job.connection, picks, base, contents, rows, None))
    final = {p: c for p, c in d.contents.items() if c != current.get(p, "")}
    if not final:
        raise CoderStop("waiting_human", "지시를 반영한 수정이 없음 → 사람 확인")
    message = f"사람 지시 반영: {_one_line(job.instruction or '')[:60]}"
    if not await svc.coding_commit(job, final, d.rows, message):
        return
    await _finish(svc, job, d)
