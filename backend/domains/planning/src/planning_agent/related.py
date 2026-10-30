"""비슷한 요청·충돌하는 요청 찾기 (요청·기획서 의미 검색 + LLM 판단).

새 요청이 들어오면 이미 있는 요청(기획서가 있으면 그 문제·목표까지)과 의미가 가까운 것을
찾고, LLM이 후보마다 관계를 정한다. 묶기·보류는 사람이 정한다(여기서는 제안만).

| 관계 | 뜻 |
|---|---|
| `same` | 같은 일을 다른 말로 요청함 (하나로 처리할 수 있음) |
| `conflict` | 둘 다 아직 진행 중인데 같은 대상을 양립할 수 없게 바꾸려 함 |
| `update` | 앞 요청(끝났거나 같은 요청자)의 결과를 다시 바꿈. 나중 요청이 앞 요청을 대체 |
| `related` | 같은 화면·기능을 다루지만 따로 처리해도 됨 |

- 검색: 임베딩(`PLANNING_EMBED_MODEL`·`EMBED_MODEL`)이 있으면 그것, 없거나 실패하면 글자 2-gram
  벡터. 어느 쪽으로 찾았는지 `by`에 남긴다.
- 판단: LLM이 있으면 후보 안에서만 고른다. 후보에 없는 id는 버린다(지어내지 않는다).
  LLM이 없거나 실패하면 관계를 정하지 않고 `related`(비슷함)만 남긴다.
- 저장소는 이 프로세스 메모리의 벡터 캐시다. 나중에 회사 기억(memory) 카드로 옮길 수 있게
  검색은 `Embedder` 뒤에 둔다.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from ax_agent_template import config as template_config
from ax_agent_template.llm import LLMClient, LLMSettings
from planning_agent.domain.models import PR, RelatedItem, Request, State

TOP_K = 5
MIN_SCORE = {"embed": 0.6, "lexical": 0.3}  # 이 아래는 후보로 보지 않는다
LEXICAL_DIM = 512
LLM_TIMEOUT_SECONDS = 60.0

JUDGE_PROMPT = (
    "너는 기획개발팀의 요청 정리 담당이다. 아래 데이터의 새 요청(new)과 이미 있는 요청 후보"
    "(candidates)를 비교해 후보마다 관계를 정한다.\n"
    "- same: 같은 일을 다른 말로 요청함. 하나로 묶어 처리할 수 있다.\n"
    "- conflict: 둘 다 아직 끝나지 않았고(state가 완료·기각이 아님), 같은 대상(화면·값·규칙)을 "
    "양립할 수 없게 바꾸려 한다. 누구 것을 따를지 사람이 정해야 한다.\n"
    "- update: 새 요청이 이미 끝난(state 완료) 후보의 결과를 다시 바꾼다(예: 3만 원 → 5만 원으로 "
    "수정). 후보가 아직 진행 중이면 update가 아니라 conflict다. from은 부서 단위라 같아도 같은 "
    "사람인지 알 수 없다.\n"
    "- related: 같은 화면·기능을 다루지만 따로 처리해도 된다.\n"
    "- none: 관계없다.\n"
    "- id는 candidates에 있는 값만 쓴다. reason은 판단 근거 한 줄(어느 부분이 같거나 반대인지).\n"
    '- JSON {"items": [{"id": "...", "relation": "same", "reason": "..."}]} 로만 답한다.'
)


class Embedder(Protocol):
    method: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class LexicalEmbedder:
    """네트워크 없는 글자 2-gram 해시 벡터 (한국어는 띄어쓰기가 달라도 맞게 공백을 뺀다)."""

    method = "lexical"

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [_lexical(t) for t in texts]


class LLMEmbedder:
    method = "embed"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return await self._llm.embed(texts)


def _lexical(text: str) -> list[float]:
    s = re.sub(r"\s+", "", text.lower())
    vec = [0.0] * LEXICAL_DIM
    for a, b in zip(s, s[1:], strict=False):
        h = int(hashlib.md5((a + b).encode()).hexdigest()[:8], 16)
        vec[h % LEXICAL_DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return max(0.0, min(1.0, dot / (na * nb)))


def text_of(state: State, req: Request) -> str:
    """검색 대상 문장: 요청 제목, 기획서가 있으면 문제·목표와 요구 문단."""
    parts = [req.title]
    spec = next((s for s in state.specs if s.id == req.spec), None)
    if spec is not None:
        parts += [spec.problem, spec.goal, *(p.text for p in spec.paras if p.kind == "req")]
    return "\n".join(p for p in parts if p)


class _Judged(BaseModel):
    id: str
    relation: Literal["same", "conflict", "update", "related", "none"]
    reason: str = ""


class _Judgement(BaseModel):
    items: list[_Judged] = Field(default_factory=list)


class Relater:
    """요청 하나에 대해 비슷한·충돌하는 요청을 찾는다. 벡터는 문장이 같으면 다시 만들지 않는다."""

    def __init__(self, embedder: Embedder | None = None, llm: Any = None) -> None:
        self._embedder: Embedder = embedder or LexicalEmbedder()
        self._llm = llm
        self._cache: dict[str, list[float]] = {}
        self._fallback = LexicalEmbedder()
        self._fallback_cache: dict[str, list[float]] = {}

    async def _vectors(self, texts: list[str]) -> tuple[list[list[float]], str]:
        try:
            return await self._embed(self._embedder, self._cache, texts), self._embedder.method
        except Exception:  # noqa: BLE001 - 임베딩이 안 되면 글자 검색으로
            vecs = await self._embed(self._fallback, self._fallback_cache, texts)
            return vecs, self._fallback.method

    @staticmethod
    async def _embed(
        embedder: Embedder, cache: dict[str, list[float]], texts: list[str]
    ) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in cache]
        if missing:
            for text, vec in zip(missing, await embedder.embed(missing), strict=True):
                cache[text] = vec
        return [cache[t] for t in texts]

    async def relate(self, state: State, req: Request) -> list[RelatedItem]:
        pool = [r for r in state.requests if r.id != req.id and r.id != req.duplicate_of]
        if not pool:
            return []
        texts = [text_of(state, req), *(text_of(state, r) for r in pool)]
        vecs, method = await self._vectors(texts)
        scored = sorted(
            ((r, _cosine(vecs[0], v)) for r, v in zip(pool, vecs[1:], strict=True)),
            key=lambda x: -x[1],
        )
        found = [(r, sc) for r, sc in scored if sc >= MIN_SCORE[method]][:TOP_K]
        if not found:
            return []
        judged = await self._judge(state, req, found)
        out: list[RelatedItem] = []
        for r, sc in found:
            j = judged.get(r.id) if judged is not None else None
            if judged is not None and (j is None or j.relation == "none"):
                continue  # LLM이 관계없다고 본 후보는 뺀다
            out.append(
                RelatedItem(
                    id=r.id,
                    title=r.title,
                    score=round(sc, 3),
                    relation=j.relation if j else "related",
                    reason=(j.reason if j and j.reason else f"의미가 비슷함 ({sc:.2f})")[:200],
                    by="llm" if j else method,
                )
            )
        return out

    async def _judge(
        self, state: State, req: Request, found: list[tuple[Request, float]]
    ) -> dict[str, _Judged] | None:
        """LLM 판단. 없거나 실패하면 None (관계는 정하지 않는다)."""
        if self._llm is None:
            return None
        data = {
            "new": {"id": req.id, "from": req.from_, "text": text_of(state, req)},
            "candidates": [
                {"id": r.id, "state": r.state, "from": r.from_, "text": text_of(state, r)}
                for r, _ in found
            ],
        }
        try:
            got = await asyncio.wait_for(
                self._llm.structured(
                    JUDGE_PROMPT, _Judgement, untrusted=data, context_id=req.context_id
                ),
                LLM_TIMEOUT_SECONDS,
            )
        except Exception:  # noqa: BLE001 - 판단이 안 되면 검색 결과만 남긴다
            return None
        ids = {r.id for r, _ in found}
        return {j.id: j for j in got.items if j.id in ids}


FINISHED = ("완료", "기각", "중복 연결")


STOPPED = ("merged", "failed", "canceled")  # 코딩 작업이 더 바뀌지 않는 상태


def settled(state: State, req_id: str) -> bool:
    """요청이 끝났나: 완료·기각·중복 연결, 또는 그 기획서의 코딩 작업이 모두 머지·실패·중단."""
    req = next((r for r in state.requests if r.id == req_id), None)
    if req is None or req.state in FINISHED:
        return True
    tasks = [t for t in state.tasks if t.spec and t.spec == req.spec]
    return bool(tasks) and all(t.coding and t.coding.status in STOPPED for t in tasks)


def open_conflicts(state: State, req: Request) -> list[RelatedItem]:
    """이 요청의 충돌 가능 중 상대 요청이 아직 안 끝난 것 (끝났으면 이어서 바꾼 것과 같다)."""
    return [x for x in req.related if x.relation == "conflict" and not settled(state, x.id)]


def path_overlaps(state: State, pr: PR) -> list[RelatedItem]:
    """이 PR과 같은 파일을 고치는 다른 열린 PR의 요청 (검색 없이 경로 비교).

    머지 순서에 따라 한쪽이 다른 쪽을 덮을 수 있어 충돌 가능으로 둔다."""
    mine = set(pr.changed_paths)
    out: list[RelatedItem] = []
    for other in state.prs:
        if other.id == pr.id or other.merged or other.closed:
            continue
        both = sorted(mine & set(other.changed_paths))
        req = request_of_pr(state, other)
        if not both or req is None or any(x.id == req.id for x in out):
            continue
        out.append(
            RelatedItem(
                id=req.id,
                title=req.title,
                score=1.0,
                relation="conflict",
                reason=f"{other.id}와 같은 파일 수정 중: {', '.join(both[:3])}"[:200],
                by="path",
            )
        )
    return out


def request_of_pr(state: State, pr: PR) -> Request | None:
    """PR → 작업 → 기획서 → 요청."""
    task = next((t for t in state.tasks if t.id == pr.task), None)
    spec = next((s for s in state.specs if task and s.id == task.spec), None)
    return next((r for r in state.requests if spec and r.id == spec.request), None)


def default_relater() -> Relater | None:
    """목 모드면 None (찾지 않는다). 아니면 planning 임베딩·LLM 설정이 있는 것만 쓴다."""
    if template_config.is_mock("planning"):
        return None
    settings = LLMSettings.from_env("planning")
    embedder: Embedder | None = None
    if settings.embed_model and settings.base_url:
        embedder = LLMEmbedder(LLMClient("planning", settings=settings))
    llm = LLMClient("planning", settings=settings) if settings.model and settings.base_url else None
    return Relater(embedder=embedder, llm=llm)
