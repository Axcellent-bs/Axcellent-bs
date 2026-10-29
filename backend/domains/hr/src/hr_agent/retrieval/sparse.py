"""Sparse 검색: 글자 2-gram BM25. 단어가 겹치는 정도를 흔한 조각은 낮게, 드문 조각은 높게 계산한다.

SQLite FTS5(trigram)를 쓰지 않은 이유: 한국어는 "결제", "롤백"처럼 두 글자 단어가 핵심인데
trigram 색인은 세 글자 미만을 찾지 못한다. 카드가 수천 장 이하라 파이썬에서 직접 계산해도 빠르다.
Dice와 같은 2-gram이지만, 조각마다 가중치(IDF)와 글 길이 보정이 있다.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_WORD = re.compile(r"\w+", re.UNICODE)


def grams(text: str) -> list[str]:
    """공백과 기호를 뺀 글자 2-gram 목록(중복 포함). 한 글자뿐이면 그 글자 하나."""
    compact = "".join(_WORD.findall(text.lower()))
    if len(compact) < 2:
        return [compact] if compact else []
    return [compact[i : i + 2] for i in range(len(compact) - 1)]


@dataclass(frozen=True)
class SparseHit:
    key: str
    score: float


class SparseIndex:
    """글(키 -> 글)을 BM25로 색인한다. 같은 색인에서 후보를 좁혀 검색할 수 있다."""

    def __init__(self, docs: Mapping[str, str], k1: float = 1.2, b: float = 0.75) -> None:
        self._k1, self._b = k1, b
        self._tf: dict[str, Counter[str]] = {k: Counter(grams(t)) for k, t in docs.items()}
        self._len = {k: sum(c.values()) for k, c in self._tf.items()}
        n = len(self._tf)
        self._avg = (sum(self._len.values()) / n) if n else 0.0
        df: Counter[str] = Counter()
        for counter in self._tf.values():
            df.update(counter.keys())
        self._idf = {g: math.log(1 + (n - d + 0.5) / (d + 0.5)) for g, d in df.items()}

    def scores(self, query: str, candidates: Sequence[str] | None = None) -> dict[str, float]:
        """후보(없으면 전체) 각각의 BM25 점수. 겹치는 조각이 없으면 0이다."""
        terms = set(grams(query))
        keys = list(self._tf) if candidates is None else [c for c in candidates if c in self._tf]
        out: dict[str, float] = {}
        for key in keys:
            tf, length = self._tf[key], self._len[key]
            norm = 1 - self._b + self._b * (length / self._avg if self._avg else 1.0)
            total = 0.0
            for term in terms:
                f = tf.get(term, 0)
                if f:
                    total += self._idf[term] * f * (self._k1 + 1) / (f + self._k1 * norm)
            out[key] = round(total, 4)
        return out

    def search(
        self,
        query: str,
        candidates: Sequence[str] | None = None,
        k: int = 5,
        min_score: float = 0.0,
    ) -> list[SparseHit]:
        scored = [SparseHit(key, s) for key, s in self.scores(query, candidates).items() if s > 0]
        scored.sort(key=lambda h: (-h.score, h.key))
        return [h for h in scored[:k] if h.score >= min_score]
