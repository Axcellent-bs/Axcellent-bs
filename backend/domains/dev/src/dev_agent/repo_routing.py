"""PR을 어느 레포(연결)에 올릴지 고른다. 결과는 제안이고, 사람이 승인한 뒤에만 올린다.

- 규칙 점수(늘 쓴다, 같은 입력이면 같은 결과): 바꿀 경로가 연결의 담당 경로 안이면 +3(경로마다),
  작업·요청 글의 낱말이 레포 이름·설명에 있으면 +1(낱말마다).
- 확신: high = 1등이 혼자이고 경로가 맞음, mid = 1등 점수가 0보다 큼, low = 근거 없음(첫 연결 제안).
- LLM 판단(선택, "AI가 판단"): planning LLM이 설정돼 있으면 후보 id 하나와 근거 3줄 이하를 JSON으로
  받는다. 후보에 없는 id·오류면 규칙 결과를 쓴다. 토큰은 보내지 않는다
  (레포 이름·설명·경로·작업 글만).
- 테스트는 `set_llm()`으로 가짜 클라이언트를 끼운다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from ax_agent_template.llm import LLMClient, LLMSettings

PATH_SCORE = 3
WORD_SCORE = 1
MAX_REASONS = 4
MAX_AI_REASONS = 3
NO_SIGNAL = "근거 없음 — 사람 확인 필요"
_WORD = re.compile(r"[A-Za-z0-9가-힣]+")
# 어디에나 나와서 근거가 안 되는 낱말
_STOP = {"the", "and", "for", "src", "app", "dev", "main", "repo", "docs", "md", "py", "ts", "tsx"}


@dataclass(frozen=True)
class RouteInput:
    """판단 입력: PR·작업·요청에서 꺼낸 글과 바꿀 경로."""

    title: str = ""
    text: str = ""  # 문단 글
    changed_paths: tuple[str, ...] = ()
    request_title: str = ""
    area: str = ""  # 작업 영역 (예: shop-web)


@dataclass(frozen=True)
class Candidate:
    """후보 연결. 토큰은 넣지 않는다."""

    connection_id: str
    repo: str
    description: str = ""
    paths: tuple[str, ...] = ()


@dataclass
class Judgement:
    connection_id: str
    repo: str
    confidence: str  # high · mid · low
    reasons: list[str]
    candidates: list[dict[str, Any]] = field(default_factory=list)  # {connection_id, repo, score}
    by: str = "rules"  # rules · llm


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) >= 2 and w.lower() not in _STOP}


def _under(path: str, prefix: str) -> bool:
    base = prefix.rstrip("/")
    return path == base or path.startswith(base + "/")


def _score_one(inp: RouteInput, c: Candidate) -> tuple[int, bool, list[str]]:
    score, path_hit, reasons = 0, False, []
    for p in inp.changed_paths:
        hit = next((x for x in sorted(c.paths, key=len, reverse=True) if _under(p, x)), None)
        if hit is not None:
            score += PATH_SCORE
            path_hit = True
            shown = hit if hit.endswith("/") else f"{hit}/"
            reasons.append(f"바꿀 경로 {p} → 담당 경로 {shown}")
    task_words = _words(" ".join([inp.title, inp.text, inp.request_title, inp.area]))
    for p in inp.changed_paths:
        task_words |= _words(re.sub(r"[_.-]", " ", p.split("/", 1)[0]))
    repo_words = _words(re.sub(r"[/_.-]", " ", c.repo))
    desc_words = _words(c.description)
    for w in sorted(task_words & (repo_words | desc_words)):
        score += WORD_SCORE
        where = "레포 이름" if w in repo_words else "레포 설명"
        reasons.append(f"낱말 '{w}' → {where}")
    return score, path_hit, reasons


def score_rules(inp: RouteInput, cands: list[Candidate]) -> Judgement:
    """규칙 점수로 하나를 고른다. 후보가 비면 `ValueError`."""
    if not cands:
        raise ValueError("후보 연결이 없다")
    scored = [(c, *_score_one(inp, c)) for c in cands]
    ranked = sorted(enumerate(scored), key=lambda x: (-x[1][1], x[0]))
    order = [row for _, row in ranked]
    top, top_score, top_path, top_reasons = order[0]
    second = order[1][1] if len(order) > 1 else -1
    if top_score <= 0:
        confidence, reasons = "low", [NO_SIGNAL]
    elif top_path and top_score > second:
        confidence, reasons = "high", top_reasons[:MAX_REASONS]
    else:
        confidence, reasons = "mid", top_reasons[:MAX_REASONS]
        if top_score == second:
            reasons.append("점수가 같은 후보가 있음 — 사람 확인 필요")
    return Judgement(
        connection_id=top.connection_id,
        repo=top.repo,
        confidence=confidence,
        reasons=reasons,
        candidates=[
            {"connection_id": c.connection_id, "repo": c.repo, "score": s} for c, s, _, _ in order
        ],
    )


# ---------------------------------------------------------------- LLM 판단


class RepoPick(BaseModel):
    """LLM 응답 모양."""

    connection_id: str
    reasons: list[str] = Field(default_factory=list)


_PROMPT = (
    "너는 개발팀의 PR 배치 담당이다. 아래 데이터의 PR 하나를 올릴 레포를 후보 중 하나로 고른다.\n"
    "- connection_id는 candidates에 있는 값을 그대로 쓴다. 다른 값을 만들지 않는다.\n"
    "- reasons는 3줄 이하의 짧은 한국어 사실 문장이다 (예: 바꿀 경로가 그 레포 담당 경로 안).\n"
    '- JSON {"connection_id": "...", "reasons": ["..."]} 로만 답한다.'
)

_llm: LLMClient | None = None
_llm_set = False


def set_llm(client: LLMClient | None) -> None:
    """LLM 클라이언트를 바꿔 끼운다 (테스트용). None을 끼우면 LLM을 쓰지 않는다."""
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


def _clip(text: str, n: int = 80) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


async def llm_judge(
    inp: RouteInput, cands: list[Candidate], rules: Judgement, client: LLMClient
) -> Judgement | None:
    """LLM에 하나를 고르게 한다. 후보 밖 id·빈 답·오류면 None (규칙 결과를 쓴다)."""
    data = {
        "pr": {
            "title": inp.title,
            "paragraph": inp.text,
            "changed_paths": list(inp.changed_paths),
            "request_title": inp.request_title,
            "area": inp.area,
        },
        "candidates": [
            {
                "connection_id": c.connection_id,
                "repo": c.repo,
                "description": c.description,
                "paths": list(c.paths),
            }
            for c in cands
        ],
        "rule_scores": rules.candidates,
    }
    try:
        pick = await client.structured(_PROMPT, RepoPick, untrusted=data)
    except Exception:  # noqa: BLE001 - 어떤 실패든 규칙 결과로 돌아간다
        return None
    chosen = next((c for c in cands if c.connection_id == pick.connection_id), None)
    if chosen is None:
        return None
    ai = [f"AI: {_clip(r)}" for r in pick.reasons if str(r).strip()][:MAX_AI_REASONS]
    if not ai:
        ai = ["AI: 근거 없이 고름"]
    agree = chosen.connection_id == rules.connection_id
    if agree:
        confidence = rules.confidence if rules.confidence != "low" else "mid"
        extra = [r for r in rules.reasons if r != NO_SIGNAL]
    else:
        # 규칙과 다르면 확신을 낮춘다 (규칙이 경로까지 맞췄으면 low)
        confidence = "low" if rules.confidence == "high" else "mid"
        extra = [f"규칙 1등은 {rules.repo or rules.connection_id} — 사람 확인 필요"]
    return Judgement(
        connection_id=chosen.connection_id,
        repo=chosen.repo,
        confidence=confidence,
        reasons=(ai + extra)[: MAX_AI_REASONS + MAX_REASONS],
        candidates=rules.candidates,
        by="llm",
    )


async def decide(
    inp: RouteInput, cands: list[Candidate], client: LLMClient | None = None
) -> Judgement:
    """규칙으로 고르고, LLM이 있으면 LLM 판단을 쓴다 (실패하면 규칙)."""
    rules = score_rules(inp, cands)
    llm = client if client is not None else get_llm()
    if llm is None or len(cands) < 2:
        return rules
    return await llm_judge(inp, cands, rules, llm) or rules
