"""소재 ↔ 회사 실제 상품 연결 (content_recommend가 쓴다). 읽기 전용, 숫자·판정은 코드.

- 상품: 회사 커넥터의 상품 목록(캣북스 shop-api `GET /api/shop/products`·`/categories`, 판매 중만).
  못 읽으면 연결하지 않고 `catalog_status: unknown` + 이유(0개로 지어내지 않음)
- 연결 근거 두 가지 (기준값은 회사 `trends.toml` `[recommend]`):
  1. 문자열 일치(`name`·`category`): 소재 이름(같이 본 이름 포함, 공백·대소문자 무시)이 상품 이름의
     낱말(2글자 이상)을 품거나 상품 이름이 소재 이름을 품으면 `name`. 카테고리 이름(예: 도서)을
     품거나 그 안에 들면 그 카테고리 상품이 `category`
  2. 임베딩 유사도(`embedding`): 소재 글(이름 + 근거 글 최대 3개)과 상품 글("이름 (카테고리)" +
     설명)의 코사인. `top_only`(기본)면 **1등 상품 하나만**, 유사도 ≥ `min_similarity`이고
     1등 − 2등 ≥ `min_margin`일 때 잇는다(`eval/product_link` 보정셋으로 고른 방식).
     임베딩은 agent-template `LLMClient.embed`(모델 `DATA_EMBED_MODEL` 또는 `EMBED_MODEL`, 주소·키는
     `*_EMBED_BASE_URL`·`*_EMBED_API_KEY` → LLM 설정). 재지 못하면 `similarity_status: unknown` +
     까닭(설정 없음·설정 오류·요청 실패) + 이유이고 문자열 일치만 쓴다
- 순서: 이름 일치 → 카테고리 일치 → 유사도 큰 순, 최대 `max_products`개
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from ax_agent_template.llm import LLMClient, LLMNotConfiguredError
from ax_contracts.schemas.data import LinkedProduct
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.llm_budget import LLMDailyCapReached

_SPACE = re.compile(r"\s+")
_WORD = re.compile(r"[^\W_]+")


class Catalog(Protocol):
    """상품 목록을 주는 커넥터 (캣북스: `ShopApiConnector`)."""

    async def products(self) -> list[CatalogProduct]: ...

    async def categories(self) -> dict[str, str]: ...


@dataclass(frozen=True)
class CatalogRead:
    products: list[CatalogProduct]
    categories: dict[str, str]  # 코드 → 이름
    reason: str | None = None  # 못 읽었으면 이유


async def read_catalog(catalog: Any) -> CatalogRead:
    """상품·카테고리를 읽는다. 못 읽으면 빈 목록 + 이유 (0개로 지어내지 않고 이유를 남긴다)."""
    if catalog is None or not hasattr(catalog, "products"):
        return CatalogRead([], {}, "회사 상품 목록을 주는 커넥터가 없음")
    try:
        products = [p for p in await catalog.products() if p.active]
    except (ConnectorUnavailable, ValidationError, TypeError) as error:
        code = getattr(error, "code", type(error).__name__)
        return CatalogRead([], {}, f"회사 상품 목록을 읽지 못함 [{code}]")
    categories: dict[str, str] = {}
    if hasattr(catalog, "categories"):
        try:
            categories = dict(await catalog.categories())
        except (ConnectorUnavailable, ValidationError, TypeError):
            categories = {}  # 카테고리 이름이 없으면 카테고리 일치만 못 본다
    if not products:
        return CatalogRead([], categories, "회사 상품 목록이 비어 있음")
    return CatalogRead(products, categories)


def _norm(text: str) -> str:
    return _SPACE.sub("", text.strip().lower())


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if len(w) >= 2 and not w.isdigit()]


@dataclass
class Link:
    product: CatalogProduct
    match: Literal["name", "category", "embedding"]
    similarity: float | None = None
    detail: str = ""
    margin: float | None = None  # 임베딩 연결이면 1등 − 2등


def string_links(
    names: Sequence[str], products: Sequence[CatalogProduct], categories: Mapping[str, str]
) -> list[Link]:
    """소재 이름들과 상품 이름·카테고리 이름의 글자 일치 (공백·대소문자 무시)."""
    mine = [n for n in (_norm(x) for x in names) if len(n) >= 2]
    out: list[Link] = []
    for product in products:
        full = _norm(product.name)
        hit = next((w for w in _words(product.name) if any(w in n for n in mine)), None)
        if hit is None:
            hit = next((n for n in mine if n in full), None)
        if hit is not None:
            out.append(Link(product, "name", None, f"이름 일치: {hit}"))
            continue
        label = categories.get(product.category_code or "", "")
        key = _norm(label)
        if len(key) >= 2 and any(key in n or n in key for n in mine):
            out.append(Link(product, "category", None, f"카테고리 일치: {label}"))
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def product_text(product: CatalogProduct, categories: Mapping[str, str]) -> str:
    """임베딩 상품 글: "이름 (카테고리)" + 설명(있으면). `eval/product_link` rich 모양과 같다."""
    label = categories.get(product.category_code or "", "")
    base = f"{product.name} ({label})" if label else product.name
    description = (product.description or "").strip()
    return f"{base}\n{description}" if description else base


MAX_EVIDENCE = 3  # 소재 글에 붙이는 근거 글 수


def topic_text(names: Sequence[str], evidence: Sequence[str]) -> str:
    """임베딩 소재 글: 이름(같이 본 이름 포함) + 근거 글 최대 3개. `eval/product_link` rich 모양."""
    head = ", ".join(n for n in names if n)
    return "\n".join([head, *[e.strip() for e in evidence if e and e.strip()][:MAX_EVIDENCE]])


NOT_CONFIGURED = "임베딩 설정이 없음 (DATA_EMBED_MODEL 또는 EMBED_MODEL)"


@dataclass
class Similarities:
    """소재 글마다 상품별 코사인. 재지 못했으면 `reason`."""

    by_topic: dict[str, list[float]] = field(default_factory=dict)
    reason: str | None = None
    # 재지 못한 까닭: not_configured = 설정 없음, config_error = 설정 오류(키 없음 등),
    # request_failed = 요청 실패. 쟀거나 끈 경우는 None
    failure: Literal["not_configured", "config_error", "request_failed"] | None = None


async def similarities(
    llm: LLMClient,
    topics: Mapping[str, str],
    products: Sequence[CatalogProduct],
    categories: Mapping[str, str],
    *,
    enabled: bool = True,
    context_id: str | None = None,
) -> Similarities:
    """임베딩 한 번(소재 글 + 상품 글)으로 소재·상품 코사인을 잰다."""
    if not enabled:
        return Similarities(reason="회사 설정에서 유사도를 끔")
    if not topics or not products:
        return Similarities()
    keys = list(topics)
    texts = [topics[k] for k in keys] + [product_text(p, categories) for p in products]
    try:
        vectors = await llm.embed(texts, context_id=context_id)
    except LLMDailyCapReached as error:  # data LLM 하루 상한: 부르지 않음 (이유 그대로)
        return Similarities(reason=f"임베딩을 부르지 않음: {error}", failure="request_failed")
    except LLMNotConfiguredError as error:
        settings = getattr(llm, "settings", None)
        if not getattr(settings, "embed_model", None):
            return Similarities(reason=NOT_CONFIGURED, failure="not_configured")
        # 모델은 있는데 주소·키가 맞지 않음. 메시지에는 빠진 설정 이름만 있다(값 없음)
        return Similarities(reason=f"임베딩 설정 오류: {error}", failure="config_error")
    except Exception as error:  # 호출 실패(HTTP 오류·시간 초과 등)
        return Similarities(
            reason=f"임베딩을 받지 못함 ({type(error).__name__})", failure="request_failed"
        )
    if len(vectors) != len(texts):
        return Similarities(reason="임베딩 응답 개수가 요청과 다름", failure="request_failed")
    head, tail = vectors[: len(keys)], vectors[len(keys) :]
    return Similarities(
        {k: [round(cosine(v, w), 4) for w in tail] for k, v in zip(keys, head, strict=True)}
    )


def pick_links(
    names: Sequence[str],
    products: Sequence[CatalogProduct],
    categories: Mapping[str, str],
    sims: Sequence[float] | None,
    *,
    min_similarity: float,
    limit: int,
    top_only: bool = False,
    min_margin: float = 0.0,
) -> list[LinkedProduct]:
    """문자열 일치와 유사도를 합쳐 연결할 상품 최대 `limit`개 (이름 → 카테고리 → 유사도 순).

    임베딩 연결은 `embedding_picks` 규칙을 따른다. `top_only`면 1등 하나만(유사도·여유 기준),
    아니면 예전처럼 유사도 기준 이상 전부. content_recommend는 회사 설정(기본 1등만)을 넘긴다.
    """
    by_id: dict[str, Link] = {}
    for link in string_links(names, products, categories):
        by_id.setdefault(link.product.product_id, link)
    if sims is not None:
        picks = embedding_picks(
            sims, min_similarity=min_similarity, top_only=top_only, min_margin=min_margin
        )
        for i, (product, sim) in enumerate(zip(products, sims, strict=True)):
            link = by_id.get(product.product_id)
            if link is not None:
                link.similarity = sim
            elif i in picks:
                margin = picks[i]
                detail = f"유사도 {sim:.2f}" + (
                    f", 2등과 차이 {margin:.2f}" if margin is not None else ""
                )
                by_id[product.product_id] = Link(product, "embedding", sim, detail, margin)
    order = {"name": 0, "category": 1, "embedding": 2}
    ranked = sorted(
        by_id.values(),
        key=lambda x: (order[x.match], -(x.similarity or 0.0), x.product.product_id),
    )
    return [
        LinkedProduct(
            product_id=x.product.product_id,
            name=x.product.name,
            category_code=x.product.category_code,
            category_name=categories.get(x.product.category_code or "") or None,
            match=x.match,
            similarity=x.similarity,
            reason=x.detail,
            margin=x.margin,
        )
        for x in ranked[:limit]
    ]


def embedding_picks(
    sims: Sequence[float], *, min_similarity: float, top_only: bool, min_margin: float
) -> dict[int, float | None]:
    """임베딩으로 이을 상품 위치 → 여유(1등 − 2등, top_only가 아니면 None).

    - top_only: 유사도 1등 하나만. 1등 ≥ `min_similarity`이고 1등 − 2등 ≥ `min_margin`이면 잇는다
      (상품이 하나뿐이면 여유는 1등 유사도 그대로로 본다)
    - top_only가 아니면 예전처럼 `min_similarity` 이상 전부(여유를 보지 않음)
    """
    if not sims:
        return {}
    if not top_only:
        return {i: None for i, s in enumerate(sims) if s >= min_similarity}
    order = sorted(range(len(sims)), key=lambda i: (-sims[i], i))
    best = order[0]
    second = sims[order[1]] if len(order) > 1 else 0.0
    margin = round(sims[best] - second, 4)
    if sims[best] >= min_similarity and margin >= min_margin - 1e-9:
        return {best: margin}
    return {}
