"""안전 로그 "비슷한 과거 결정 띄우기"(태윤 제안, 아직 없는 새 기능): 글자 2-그램 유사도
검색(표준 라이브러리만)을 골든셋으로 채점한다.

이 파일은 production 코드가 아니다 — 지금 이 기능은 코드에 없다(태윤님 본인 메모: "측정까지만
부탁드리고, 적용 PR은 결과 보고 저랑 같이 정해요"). 이 스크립트는 "검색만 할 때 가장 간단한
방식"의 기준선으로, 글자 2-그램 자카드 유사도로 과거 결정 목록(`corpus`)에서 지금 들어온 요청
글(query)과 가장 비슷한 것을 고른다. 한국어는 띄어쓰기 기준 낱말 겹침이 약해(조사·어미가 붙어
같은 낱말도 형태가 달라짐) 글자 2-그램을 썼다.

사용법:
    cd backend
    uv run python shared/safety/eval/similar_decision_bigram_score.py
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def bigrams(text: str) -> set[str]:
    squeezed = "".join(text.split())  # 띄어쓰기를 지우고 글자만 본다
    return {squeezed[i : i + 2] for i in range(len(squeezed) - 1)} or {squeezed}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def rank(query_text: str, corpus: list[dict]) -> list[tuple[str, float]]:
    """corpus를 query와 비슷한 순서로 (id, 점수) 목록으로 돌려준다."""
    q = bigrams(query_text)
    scored = [(c["id"], jaccard(q, bigrams(c["text"]))) for c in corpus]
    return sorted(scored, key=lambda t: t[1], reverse=True)


def evaluate(cases: dict, split: str) -> dict[str, float | None]:
    corpus = cases["corpus"]
    queries = [q for q in cases["queries"] if q["split"] == split]
    if not queries:
        return {"n": 0, "top1": None, "top3": None}
    top1_hit = top3_hit = 0
    for q in queries:
        ranked = rank(q["text"], corpus)
        ids = [i for i, _ in ranked]
        if ids[0] == q["expected"]:
            top1_hit += 1
        if q["expected"] in ids[:3]:
            top3_hit += 1
    n = len(queries)
    return {"n": n, "top1": top1_hit / n, "top3": top3_hit / n}


def _fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.2f}"


def main() -> None:
    cases = json.loads((HERE / "similar_decision_cases.json").read_text(encoding="utf-8"))
    print("| 집합 | 표본 | top-1 정확도 | top-3 정확도 |")
    print("| --- | --- | --- | --- |")
    for split in ("dev", "holdout"):
        m = evaluate(cases, split)
        print(f"| {split} | {m['n']} | {_fmt(m['top1'])} | {_fmt(m['top3'])} |")

    wrong = []
    for q in cases["queries"]:
        ranked = rank(q["text"], cases["corpus"])
        if ranked[0][0] != q["expected"]:
            wrong.append(f"{q['id']} (정답 {q['expected']}, 1위 {ranked[0][0]})")
    if wrong:
        print(f"\ntop-1 틀린 케이스: {', '.join(wrong)}")


if __name__ == "__main__":
    main()
