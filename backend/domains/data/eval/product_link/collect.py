"""소재 ↔ 상품 연결 보정셋 재료 모으기. **사람이 한 번만** 돌린다 (README "모으기").

    cd backend
    # run_all.sh로 서버를 띄운 상태에서 (shop-api 주소·data 토큰·임베딩 설정이 환경에 있어야 한다)
    uv run python domains/data/eval/product_link/collect.py

하는 일 (외부 호출: 인기 검색어 피드 1번, 임베딩 1~2번. 네이버·LLM 문장은 부르지 않는다)
1. 회사 상품 목록(shop-api) → `catalog.json` (id·이름·카테고리 코드·이름·설명)
2. 질의·소재를 README "질의 만드는 법" 규칙으로만 만든다 → `topics.json`
   - 관련 질의: 상품 이름 핵심어·카테고리 이름 + 정해진 꼬리말
   - 무관 소재: 지금 인기 검색어(피드) + 최신 YouTube 인기 영상 태그 상위. 상품 낱말과 겹치면 뺀다
3. 두 가지 글 모양(basic·rich)으로 임베딩해 소재 × 상품 코사인만 `sims/<모양>.json`에 저장한다
   - 근거 글(영상 제목·뉴스 제목)은 남의 글이라 커밋하지 않는다. 개수만 `topics.json`에 남긴다
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from rules import (  # noqa: E402
    CATEGORY_TEMPLATES,
    MAX_EVIDENCE,
    NAME_TEMPLATES,
    TRENDING_MAX,
    YOUTUBE_MAX,
    YOUTUBE_MIN_VIDEOS,
    basic_product_text,
    name_core,
    overlaps_catalog,
    rich_product_text,
    rich_topic_text,
)

from ax_agent_template.llm import LLMClient  # noqa: E402
from data_agent import handlers  # noqa: E402
from data_agent.connectors.google_trends import DEFAULT_URL, HT, URL_ENV  # noqa: E402
from data_agent.skills.trend_candidates import clean_tag, load_config  # noqa: E402
from data_agent.youtube_store import DB_ENV, DEFAULT_DB, latest_popular, video_titles  # noqa: E402


def _norm(text: str) -> str:
    return "".join(text.lower().split())


async def read_feed() -> list[tuple[str, list[str]]]:
    """인기 검색어 피드 1번: (검색어, 뉴스 제목 최대 MAX_EVIDENCE개)."""
    url = (os.environ.get(URL_ENV) or DEFAULT_URL).rstrip("/")
    async with httpx.AsyncClient(timeout=20.0) as http:
        response = await http.get(url, params={"geo": "KR"})
        response.raise_for_status()
    body = response.content
    if b"<!DOCTYPE" in body[:2048].upper():
        raise SystemExit("피드에 DTD가 있어 읽지 않음")
    channel = ET.fromstring(body).find("channel")
    out: list[tuple[str, list[str]]] = []
    for item in channel.findall("item") if channel is not None else []:
        keyword = (item.findtext("title") or "").strip()
        news = [
            (n.findtext(f"{HT}news_item_title") or "").strip()
            for n in item.findall(f"{HT}news_item")
        ]
        if keyword:
            out.append((keyword, [t for t in news if t][:MAX_EVIDENCE]))
    return out


def youtube_topics(excluded: set[str]) -> list[tuple[str, list[str]]]:
    """최신 YouTube 인기 스냅샷 태그 → (태그, 제목 최대 MAX_EVIDENCE개), 영상 수 많은 순."""
    snap = latest_popular(os.environ.get(DB_ENV) or DEFAULT_DB)
    if snap is None:
        return []
    by_tag: dict[str, list[str]] = {}
    shown: dict[str, str] = {}
    for video in snap.videos:  # 순위 순
        for tag in {_norm(t): t for t in video.tags}.items():
            key, raw = tag
            if len(key) < 2 or key in excluded:
                continue
            by_tag.setdefault(key, []).append(video.video_id)
            shown.setdefault(key, raw.strip())
    counts = Counter({k: len(v) for k, v in by_tag.items()})
    picked = [k for k, n in counts.most_common() if n >= YOUTUBE_MIN_VIDEOS]
    titles = video_titles(
        os.environ.get(DB_ENV) or DEFAULT_DB, sorted({v for k in picked for v in by_tag[k]})
    )
    return [
        (shown[k], [titles[v] for v in by_tag[k] if titles.get(v)][:MAX_EVIDENCE]) for k in picked
    ]


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


async def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    shop = handlers.get_catalog()
    products = [p for p in await shop.products() if p.active]
    categories = dict(await shop.categories())
    products.sort(key=lambda p: p.product_id)
    catalog = [
        {
            "product_id": p.product_id,
            "name": p.name,
            "category_code": p.category_code,
            "category_name": categories.get(p.category_code or ""),
            "description": getattr(p, "description", None),
        }
        for p in products
    ]

    topics: list[dict] = []
    evidence: dict[str, list[str]] = {}

    def add(kind: str, text: str, ev: list[str] | None = None, **extra: object) -> None:
        topic_id = f"t{len(topics) + 1:03d}"
        topics.append(
            {"id": topic_id, "kind": kind, "text": text, "evidence_count": len(ev or []), **extra}
        )
        evidence[topic_id] = list(ev or [])

    for p in catalog:
        core = name_core(p["name"])
        for template in NAME_TEMPLATES:
            add("related_name", template.format(core), source_product=p["product_id"])
    for code, label in sorted(
        {(p["category_code"], p["category_name"]) for p in catalog if p["category_name"]}
    ):
        for template in CATEGORY_TEMPLATES:
            add("related_category", template.format(label), source_category=code)

    config = load_config(os.environ)
    excluded = {_norm(clean_tag(t)) for t in config.exclude_tags}
    seen: set[str] = set()
    dropped: list[str] = []
    feed = await read_feed()
    for keyword, news in feed[:TRENDING_MAX]:
        if overlaps_catalog(keyword, catalog):
            dropped.append(keyword)
        elif _norm(keyword) not in seen:
            seen.add(_norm(keyword))
            add("unrelated_trending", keyword, news)
    yt_added = 0
    for tag, titles in youtube_topics(excluded):
        if yt_added >= YOUTUBE_MAX:
            break
        if overlaps_catalog(tag, catalog):
            dropped.append(tag)
        elif _norm(tag) not in seen:
            seen.add(_norm(tag))
            add("unrelated_youtube", tag, titles)
            yt_added += 1

    forms = {
        "basic": (
            [t["text"] for t in topics],
            [basic_product_text(p) for p in catalog],
        ),
        "rich": (
            [rich_topic_text(t["text"], evidence[t["id"]]) for t in topics],
            [rich_product_text(p) for p in catalog],
        ),
    }
    unique = sorted({s for tt, pp in forms.values() for s in (*tt, *pp)})
    llm = LLMClient("data")
    vectors: dict[str, list[float]] = {}
    for start in range(0, len(unique), 50):
        chunk = unique[start : start + 50]
        for text, vec in zip(chunk, await llm.embed(chunk), strict=True):
            vectors[text] = vec

    collected_at = datetime.now(UTC).replace(microsecond=0).isoformat()
    model = llm.settings.embed_model
    (HERE / "sims").mkdir(exist_ok=True)
    for form, (topic_texts, product_texts) in forms.items():
        out = {
            "form": form,
            "model": model,
            "collected_at": collected_at,
            "products": [p["product_id"] for p in catalog],
            "sims": {
                t["id"]: [round(cosine(vectors[tt], vectors[pt]), 4) for pt in product_texts]
                for t, tt in zip(topics, topic_texts, strict=True)
            },
        }
        _write(HERE / "sims" / f"{form}.json", out)
    _write(HERE / "catalog.json", {"collected_at": collected_at, "products": catalog})
    _write(
        HERE / "topics.json",
        {"collected_at": collected_at, "dropped_overlap": dropped, "topics": topics},
    )
    kinds = Counter(t["kind"] for t in topics)
    print(
        f"상품 {len(catalog)}개, 소재 {len(topics)}개 {dict(kinds)}, 겹쳐서 뺀 것 {len(dropped)}개"
    )
    print(f"임베딩 모델 {model}, 글 {len(unique)}개, 피드 1번")
    return 0


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
