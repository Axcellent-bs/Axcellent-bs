"""Hybrid 검색: Dense와 Sparse의 순위를 RRF(1/(상수+순위))로 합친다.

점수 크기가 서로 달라도 순위만 쓰므로 가중치를 정할 필요가 없다. 다만 RRF 점수는 질의가
얼마나 관련 있는지를 말해 주지 않아서(정답이 없는 질의도 순위는 매겨진다), "결과 없음"은
두 점수로 따로 정한다: 코사인이 `min_cosine` 이상이거나 BM25가 `min_sparse` 이상인 후보만 남긴다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

RRF_K = 60


@dataclass(frozen=True)
class HybridHit:
    key: str
    score: float  # RRF 점수
    cosine: float
    sparse: float


def _ranks(scores: Mapping[str, float]) -> dict[str, int]:
    """점수가 있는(0 초과) 키만 점수 내림차순으로 1부터 순위를 준다. 같으면 키 순."""
    ordered = sorted((k for k, s in scores.items() if s > 0), key=lambda k: (-scores[k], k))
    return {k: i for i, k in enumerate(ordered, 1)}


def fuse(
    cosines: Mapping[str, float],
    sparse: Mapping[str, float],
    *,
    k: int = 5,
    min_cosine: float = 0.0,
    min_sparse: float = 0.0,
    rrf_k: int = RRF_K,
) -> list[HybridHit]:
    """후보별 코사인과 BM25 점수를 합쳐 상위 k개를 돌려준다. 기준에 못 미친 후보는 뺀다."""
    dense_rank, sparse_rank = _ranks(cosines), _ranks(sparse)
    hits = []
    for key in sorted(set(cosines) | set(sparse)):
        cos, sp = cosines.get(key, 0.0), sparse.get(key, 0.0)
        if not (cos >= min_cosine and cos > 0 or sp >= min_sparse and sp > 0):
            continue
        score = sum(1 / (rrf_k + r[key]) for r in (dense_rank, sparse_rank) if key in r)
        hits.append(HybridHit(key, round(score, 6), cos, sp))
    hits.sort(key=lambda h: (-h.score, h.key))
    return hits[:k]
