"""Dense 검색: 카드 벡터를 SQLite에 저장해 두고 코사인 유사도로 가까운 카드를 찾는다.

- 벡터는 L2 정규화한 float32 BLOB이라 코사인 유사도는 내적이다. 카드가 수십~수백 장이라
  별도 벡터 DB 없이 파이썬에서 계산해도 충분하다.
- 같은 글·같은 모델이면 다시 임베딩하지 않는다(글 해시와 모델 이름을 함께 저장).
- 질의는 임베딩하기 전에 개인정보를 가린다. 결과 점수가 기준(`min_cosine`) 미만이면 돌려주지 않는다.
- 임베딩 호출은 `Embedder`로만 한다. 운영은 `LLMEmbedder`이고,
  테스트는 `MockLLMClient`를 감싼 임베더를 쓴다.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import sqlite3
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

QUERY_PREFIX = "q:"  # 질의 벡터의 키 접두사(카드 ID와 섞이지 않게)


class EmbedError(RuntimeError):
    """임베딩 호출이 재시도 후에도 실패했을 때. 메시지에 키·응답 원문을 넣지 않는다."""


class Embedder(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class _Client(Protocol):
    async def embed(self, texts: list[str], context_id: str | None = None) -> list[list[float]]: ...


class LLMEmbedder:
    """`LLMClient.embed`를 작은 묶음으로 나눠 부르고, 한도 초과(429 등)는 기다렸다 다시 보낸다."""

    def __init__(
        self,
        client: _Client,
        batch_size: int = 32,
        pause_sec: float = 0.5,
        retries: int = 4,
        retry_base_sec: float = 5.0,
    ) -> None:
        self._client = client
        self._batch = batch_size
        self._pause = pause_sec
        self._retries = retries
        self._retry_base = retry_base_sec

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), self._batch):
            chunk = texts[start : start + self._batch]
            for attempt in range(self._retries + 1):
                try:
                    out.extend(await self._client.embed(chunk))
                    break
                except Exception as exc:  # noqa: BLE001 - 원문 예외는 노출하지 않는다
                    if attempt == self._retries:
                        raise EmbedError(f"임베딩 실패: {type(exc).__name__}") from None
                    await asyncio.sleep(self._retry_base * (2**attempt))
            if start + self._batch < len(texts):
                await asyncio.sleep(self._pause)
        return out


# --- 질의 마스킹 -----------------------------------------------------------

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"\b0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def mask(text: str) -> str:
    """질의 속 이메일·전화번호·카드번호를 가린다. 카드번호가 전화번호보다 먼저다(자릿수가 길다)."""
    text = _EMAIL.sub("[이메일]", text)
    text = _CARD.sub("[번호]", text)
    return _PHONE.sub("[전화]", text)


# --- 벡터 ------------------------------------------------------------------


def normalize(vector: Sequence[float]) -> array:
    norm = math.sqrt(sum(x * x for x in vector))
    return array("f", (x / norm for x in vector) if norm else vector)


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """정규화된 두 벡터의 코사인 유사도(내적). 차원이 다르면 비교할 수 없다."""
    if len(a) != len(b):
        raise ValueError(f"벡터 차원이 다르다: {len(a)} != {len(b)}")
    return float(sum(x * y for x, y in zip(a, b, strict=True)))


def text_key(text: str) -> str:
    """질의 벡터를 저장할 키."""
    return QUERY_PREFIX + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Hit:
    key: str
    score: float


class DenseIndex:
    """키(카드 ID 또는 질의 키)별 벡터를 SQLite에 두고 검색한다."""

    def __init__(self, path: str, embedder: Embedder, model: str) -> None:
        self._path = path
        self._embedder = embedder
        self._model = model
        self._cache: dict[str, array] = {}
        with self._connect() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS vectors ("
                " key TEXT NOT NULL, model TEXT NOT NULL, text_hash TEXT NOT NULL,"
                " dim INTEGER NOT NULL, vector BLOB NOT NULL, PRIMARY KEY (key, model))"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def index(self, items: Sequence[tuple[str, str]], chunk: int = 32) -> int:
        """(키, 글) 목록을 색인한다. 글이 바뀌었거나 처음인 것만 임베딩하고 그 개수를 돌려준다.

        `chunk`개씩 임베딩해 바로 저장한다. 중간에 실패해도 앞선 묶음은 남아서, 다시 부르면
        나머지만 이어서 한다(한도가 있는 API에서 이미 받은 벡터를 버리지 않는다).
        """
        with self._connect() as db:
            known = dict(
                db.execute(
                    "SELECT key, text_hash FROM vectors WHERE model = ?", (self._model,)
                ).fetchall()
            )
        todo = [(k, t) for k, t in items if known.get(k) != self._hash(t)]
        for start in range(0, len(todo), chunk):
            part = todo[start : start + chunk]
            vectors = await self._embedder.embed([t for _, t in part])
            if len(vectors) != len(part):
                raise EmbedError(f"임베딩 개수가 입력과 다르다: {len(vectors)} != {len(part)}")
            rows = []
            for (key, text), vec in zip(part, vectors, strict=True):
                unit = normalize(vec)
                self._cache[key] = unit
                rows.append((key, self._model, self._hash(text), len(unit), unit.tobytes()))
            with self._connect() as db:
                db.executemany(
                    "INSERT OR REPLACE INTO vectors (key, model, text_hash, dim, vector)"
                    " VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
        return len(todo)

    def vector(self, key: str) -> array | None:
        if key in self._cache:
            return self._cache[key]
        with self._connect() as db:
            row = db.execute(
                "SELECT vector FROM vectors WHERE key = ? AND model = ?", (key, self._model)
            ).fetchone()
        if row is None:
            return None
        vec = array("f")
        vec.frombytes(row[0])
        self._cache[key] = vec
        return vec

    def search_key(
        self, query_key: str, candidates: Sequence[str], k: int = 5, min_cosine: float = 0.0
    ) -> list[Hit]:
        """저장된 질의 벡터로 후보 키들 중 가까운 것을 찾는다. 점수가 `min_cosine` 미만이면 뺀다."""
        query = self.vector(query_key)
        if query is None:
            raise KeyError(query_key)
        scored = []
        for key in candidates:
            vec = self.vector(key)
            if vec is not None:
                scored.append(Hit(key, round(cosine(query, vec), 4)))
        scored.sort(key=lambda h: (-h.score, h.key))
        return [h for h in scored[:k] if h.score >= min_cosine]

    async def search(
        self, query: str, candidates: Sequence[str], k: int = 5, min_cosine: float = 0.0
    ) -> list[Hit]:
        """질의를 가린 뒤 임베딩(같은 질의는 한 번만)하고 후보 중 가까운 카드를 찾는다."""
        masked = mask(query)
        key = text_key(masked)
        await self.index([(key, masked)])
        return self.search_key(key, candidates, k, min_cosine)
