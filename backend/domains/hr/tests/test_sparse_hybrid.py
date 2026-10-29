"""Sparse(BM25)와 Hybrid(RRF) 검색."""

from hr_agent.retrieval.hybrid import fuse
from hr_agent.retrieval.sparse import SparseIndex, grams

DOCS = {
    "pay": "결제 승인 오류가 늘어 배포를 롤백했다",
    "fri": "금요일 오후에는 배포하지 않는다",
    "acct": "퇴사자 계정은 당일 회수한다",
    "cache": "상품 상세 캐시 유효시간을 늘렸다",
}


def test_grams_ignore_spaces_and_symbols() -> None:
    assert grams("결제 오류!") == ["결제", "제오", "오류"]
    assert grams("a") == ["a"] and grams("") == []


def test_exact_words_rank_first() -> None:
    hits = SparseIndex(DOCS).search("결제 오류 롤백")
    assert hits[0].key == "pay"


def test_two_character_words_are_searchable() -> None:
    assert SparseIndex(DOCS).search("롤백")[0].key == "pay"  # trigram 색인이면 놓친다


def test_rare_terms_weigh_more_than_common_ones() -> None:
    docs = {
        "a": "배포 배포 배포 점검",
        "b": "정산 점검",
        **{f"x{i}": "배포 안내" for i in range(6)},
    }
    index = SparseIndex(docs)
    scores = index.scores("정산 배포")
    assert scores["b"] > scores["x0"]  # 흔한 "배포"보다 드문 "정산"이 더 크게 반영된다


def test_search_limits_to_candidates_and_returns_nothing_for_no_overlap() -> None:
    index = SparseIndex(DOCS)
    assert [h.key for h in index.search("배포", candidates=["fri", "acct"])] == ["fri"]
    assert index.search("zzzz") == []
    assert index.search("") == []


def test_min_score_filters_weak_matches() -> None:
    index = SparseIndex(DOCS)
    strong = index.search("결제 승인 오류 롤백")[0].score
    assert index.search("결제", min_score=strong + 1) == []


def test_fuse_prefers_documents_found_by_both() -> None:
    cos = {"a": 0.9, "b": 0.8, "c": 0.7}
    sp = {"b": 5.0, "c": 4.0, "a": 0.0}
    assert [h.key for h in fuse(cos, sp)] == ["b", "c", "a"]  # b는 둘 다 상위, a는 Sparse에 없다


def test_fuse_gate_drops_candidates_below_both_thresholds() -> None:
    cos = {"a": 0.9, "b": 0.5}
    sp = {"a": 1.0, "b": 0.2}
    kept = fuse(cos, sp, min_cosine=0.7, min_sparse=3.0)
    assert [h.key for h in kept] == ["a"]  # b는 코사인도 BM25도 기준 미달
    assert fuse(cos, sp, min_cosine=0.95, min_sparse=3.0) == []


def test_fuse_keeps_keyword_match_with_low_cosine() -> None:
    kept = fuse({"a": 0.3}, {"a": 9.0}, min_cosine=0.7, min_sparse=3.0)
    assert [h.key for h in kept] == ["a"]  # 식별자처럼 단어는 맞지만 의미 점수가 낮은 경우


def test_fuse_limits_to_k_and_is_deterministic() -> None:
    cos = {f"k{i}": 1 - i / 100 for i in range(10)}
    first = fuse(cos, {}, k=3)
    assert [h.key for h in first] == ["k0", "k1", "k2"]
    assert first == fuse(cos, {}, k=3)
