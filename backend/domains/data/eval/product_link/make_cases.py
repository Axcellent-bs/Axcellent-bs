"""`catalog.json`·`topics.json`만 읽어 정답과 나눔(dev·holdout)을 `cases.json`에 쓴다.

네트워크 없음.

    cd backend
    uv run python domains/data/eval/product_link/make_cases.py

정답은 만든 방식에서만 정한다(README "정답").
- related_name: 질의를 만든 상품 하나
- related_category: 그 카테고리 상품 전부
- unrelated_*: 없음(어느 상품과도 잇지 않아야 한다)
나눔: sha256("<SEED>:<소재 id>")를 10으로 나눈 나머지가 3 미만이면 holdout(약 30%).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SEED = "2026110302"
HOLDOUT_BUCKETS = 3  # 10개 중


def split_of(topic_id: str) -> str:
    digest = hashlib.sha256(f"{SEED}:{topic_id}".encode()).digest()
    return "holdout" if int.from_bytes(digest[:4], "big") % 10 < HOLDOUT_BUCKETS else "dev"


def relevant_of(topic: dict, catalog: list[dict]) -> list[str]:
    kind = topic["kind"]
    if kind == "related_name":
        return [topic["source_product"]]
    if kind == "related_category":
        return [p["product_id"] for p in catalog if p["category_code"] == topic["source_category"]]
    if kind.startswith("unrelated_"):
        return []
    raise ValueError(f"모르는 종류: {kind}")


def build(catalog: list[dict], topics: list[dict]) -> list[dict]:
    return [
        {
            "id": t["id"],
            "kind": t["kind"],
            "text": t["text"],
            "split": split_of(t["id"]),
            "relevant": relevant_of(t, catalog),
        }
        for t in topics
    ]


def main() -> int:
    catalog = json.loads((HERE / "catalog.json").read_text(encoding="utf-8"))["products"]
    topics = json.loads((HERE / "topics.json").read_text(encoding="utf-8"))["topics"]
    cases = build(catalog, topics)
    out = {"seed": SEED, "cases": cases}
    (HERE / "cases.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    dev = sum(c["split"] == "dev" for c in cases)
    print(f"케이스 {len(cases)}개 (dev {dev}, holdout {len(cases) - dev})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
