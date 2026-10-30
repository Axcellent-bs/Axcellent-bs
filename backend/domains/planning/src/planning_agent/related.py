"""비슷한 요청·충돌하는 요청 찾기 (요청·기획서 의미 검색 + LLM 판단).

새 요청이 들어오면 이미 있는 요청(기획서가 있으면 그 문제·목표까지)과 의미가 가까운 것을
찾고, LLM이 후보마다 관계를 정한다. 묶기·보류는 사람이 정한다(여기서는 제안만).

| 관계 | 뜻 |
|---|---|
| `same` | 같은 일을 다른 말로 요청함 (하나로 처리할 수 있음) |
| `conflict` | 같은 대상을 반대 방향으로 바꾸려 함 |
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
from planning_agent.domain.models import RelatedItem, Request, State

TOP_K = 5
MIN_SCORE = {"embed": 0.6, "lexical": 0.3}  # 이 아래는 후보로 보지 않는다
LEXICAL_DIM = 512
LLM_TIMEOUT_SECONDS = 60.0

JUDGE_PROMPT = (
    "너는 기획개발팀의 요청 정리 담당이다. 아래 데이터의 새 요청(new)과 이미 있는 요청 후보"
    "(candidates)를 비교해 후보마다 관계를 정한다.\n"
    "- same: 같은 일을 다른 말로 요청함. 하나로 묶어 처리할 수 있다.\n"
    "- conflict: 같은 대상(화면·값·규칙)을 서로 반대 방향이나 양립할 수 없게 바꾸려 한다.\n"
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
    relation: Literal["same", "conflict", "related", "none"]
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
            "new": {"id": req.id, "text": text_of(state, req)},
            "candidates": [
                {"id": r.id, "state": r.state, "text": text_of(state, r)} for r, _ in found
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


def default_relater() -> Relater | None:
    """목 모드면 None (찾지 않는다). 아니면 planning 임베딩·LLM 설정이 있는 것만 쓴다."""
    if template_config.is_mock("planning"):
        return None
    settings = LLMSettings.from_env("planning")
    embedder: Embedder | None = None
    if settings.embed_model and settings.base_url:
        embedder = LLMEmbedder(LLMClient("planning", settings=settings))
    llm = (
        LLMClient("planning", settings=settings) if settings.model and settings.base_url else None
    )
    return Relater(embedder=embedder, llm=llm)
