"""변환기 AI 짝 제안 (11/25): 규칙(`propose`)으로 못 채운 칸을 회사 AI가 채운다.

- 회사 AI 설정(시스템 관리 › 회사 연결 › 공통 AI 도구 연결)을 쓴다. 부서 이름은 `site_adapter`
  (`SITE_ADAPTER_LLM_*` → `LLM_*`, 회사 키가 있으면 회사 키, `company_ai.settings_for`)
- AI에게는 칸 경로와 값의 종류(글자·숫자·참거짓·목록·묶음·빈 값)만 보낸다. 고객 이름·금액 같은
  값은 보내지 않는다. 예외는 상태 칸의 상태 낱말(예: `deposit_paid`)뿐이다(상태 표를 고르려면 필요)
- AI 답은 그대로 쓰지 않고 검사한다: 비어 있는 우리 칸만, 응답에 실제로 있는 경로만, 짝 식은
  점 경로·`sum:`·`first:`만(`const:`로 값을 지어내지 않는다), 상태는 shop-api 주문 상태만.
  어긋난 제안은 버린다. 저장은 지금처럼 사람이 하고 저장 전에 첫 쪽을 실제로 바꿔 본다
- 우리 형식에 자리가 없는 고객사 칸은 AI가 뜻만 적어 `unplaced`로 준다
  (계약에 칸을 더할지는 사람이 정함)
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from ax_agent_template.company_ai import settings_for
from ax_agent_template.llm import LLMClient
from ax_dashboard_api.site_adapter import propose
from ax_dashboard_api.site_adapter.mapping import Resource

DEPT = "site_adapter"
MAX_PATHS = 120  # AI에게 보내는 칸 경로 상한
SHOP_ORDER_STATUSES = ("pending_payment", "paid", "cancelled")

# 우리 칸의 뜻 (AI가 고객사 칸과 맞출 때 읽는다)
MEANINGS: dict[Resource, dict[str, str]] = {
    "orders": {
        "order_id": "주문(예약·결제) 번호. 주문마다 다른 값",
        "status": "주문 상태 낱말",
        "created_at": "주문한 시각",
        "buyer_id": "산 사람(회원) id. 비회원이면 비어 있을 수 있음",
        "product_id": "산 상품(투어·강의) 번호",
        "qty": "수량 또는 인원 수. 여러 칸을 더해야 하면 sum:a,b",
        "total_amount": "주문 총액(낼 금액)",
        "paid_amount": "실제로 낸 금액",
        "updated_at": "주문이 마지막으로 바뀐 시각",
        "refunded_amount": "환불한 금액 합",
    },
    "products": {
        "product_id": "상품 번호(주문의 상품 번호와 같은 값)",
        "name": "상품 이름",
        "price": "상품 가격",
        "category_code": "상품 분류(지역·과목 등)",
        "active": "판매 중인지(참·거짓)",
    },
}

PROMPT = """너는 고객사 API 응답의 칸을 우리 형식 칸에 잇는 변환기다.
아래 데이터 블록에 고객사 응답의 칸 경로와 값의 종류, 우리 칸과 뜻, 아직 비어 있는 우리 칸이 있다.

규칙:
- 비어 있는 우리 칸마다, 뜻이 맞는 고객사 칸 경로를 골라 짝 식(expr)으로 적는다
- 짝 식은 셋 중 하나만 쓴다: 경로 그대로(예: contact.name), 숫자 칸 더하기(sum:a,b),
  처음 비어 있지 않은 값(first:a,b). 경로는 데이터 블록에 있는 것만 쓴다. 값을 지어내지 않는다
- 맞는 칸이 없으면 그 우리 칸은 적지 않는다(억지로 고르지 않는다)
- 상태 낱말(status_values)마다 pending_payment·paid·cancelled 중 뜻이 맞는 것을 고른다.
  모르면 적지 않는다
- 우리 칸 어디에도 이어지지 않는 고객사 칸 중 매출·취소·환불·문의·고객 분석에 쓸 만한 칸은
  unplaced에 경로와 뜻을 한 줄로 적는다(최대 10개)
- why는 한국어 한 문장
"""


class FieldPick(BaseModel):
    name: str
    expr: str
    why: str = ""


class StatusPick(BaseModel):
    value: str
    to: str


class Unplaced(BaseModel):
    path: str
    meaning: str = Field(default="", max_length=200)


class Suggestion(BaseModel):
    fields: list[FieldPick] = []
    statuses: list[StatusPick] = []
    unplaced: list[Unplaced] = []


def make_client() -> LLMClient:
    """회사 AI 설정으로 부르는 LLM (시험은 이 함수를 바꿔 끼운다)."""
    return LLMClient(DEPT, settings_for(DEPT))


ClientFactory = Callable[[], LLMClient]


def _kind(value: Any) -> str:
    if value is None:
        return "빈 값"
    if isinstance(value, bool):
        return "참거짓"
    if isinstance(value, int | float):
        return "숫자"
    if isinstance(value, list):
        return "목록"
    if isinstance(value, dict):
        return "묶음"
    return "글자"


def shape(rows: list[dict[str, Any]]) -> dict[str, str]:
    """칸 경로(두 단계 안쪽까지) → 값의 종류. 값은 담지 않는다."""
    out: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, dict):
                out.setdefault(key, "묶음")
                for inner, v in value.items():
                    path = f"{key}.{inner}"
                    if out.get(path) in (None, "빈 값"):
                        out[path] = _kind(v)
            elif out.get(key) in (None, "빈 값"):
                out[key] = _kind(value)
            if len(out) >= MAX_PATHS:
                return out
    return out


def _expr_ok(expr: str, paths: dict[str, str]) -> bool:
    for prefix in ("sum:", "first:"):
        if expr.startswith(prefix):
            parts = [p.strip() for p in expr[len(prefix) :].split(",")]
            if len(parts) < 2 or not all(p in paths for p in parts):
                return False
            return prefix != "sum:" or all(paths[p] in ("숫자", "빈 값") for p in parts)
    return ":" not in expr and expr in paths


async def fill(
    kind: Resource,
    rows: list[dict[str, Any]],
    proposal: dict[str, Any],
    factory: ClientFactory | None = None,
) -> dict[str, Any]:
    """`proposal`(규칙 제안)의 빈 칸·모르는 상태를 AI 제안으로 채운다(제자리). 채운 내역을 돌려준다.

    돌려주는 값: `filled`(칸 → 짝 식·이유), `statuses`(상태 낱말 → 상태), `unplaced`(경로·뜻).
    빈 칸·모르는 상태가 없으면 AI를 부르지 않는다.
    """
    fields: dict[str, str] = proposal["fields"]
    lines = proposal.get("items") is not None
    meanings = MEANINGS[kind]
    skip = ("product_id", "qty", "paid_amount") if lines else ()  # 줄 목록이 있으면 줄에서 받는다
    required = [n for n in propose.SYNONYMS[kind] if n not in skip and not fields.get(n)]
    optional = [n for n in propose.OPTIONAL[kind] if not fields.get(n)]
    status_table: dict[str, str] = proposal.get("values", {}).get("status", {})
    unknown_status = [v for v, to in status_table.items() if not to]
    paths = shape(rows)
    report: dict[str, Any] = {"filled": {}, "statuses": {}, "unplaced": []}
    if not paths or not (required or unknown_status):
        return report  # 규칙으로 다 찼으면 AI를 부르지 않는다(회사 AI가 없어도 연결된다)
    empty = required + optional
    client = (factory or make_client)()
    suggestion = await client.structured(
        PROMPT,
        Suggestion,
        untrusted={
            "resource": kind,
            "site_paths": paths,
            "our_fields": meanings,
            "empty_fields": empty,
            "status_values": unknown_status,
            "already_used_paths": sorted(_used(fields)),
        },
        context_id=f"site_adapter:propose:{kind}",
    )
    for pick in suggestion.fields:
        expr = pick.expr.strip()
        if pick.name in empty and not fields.get(pick.name) and _expr_ok(expr, paths):
            fields[pick.name] = expr
            report["filled"][pick.name] = {"expr": expr, "why": pick.why[:200]}
    _take_statuses(suggestion.statuses, unknown_status, status_table, report)
    # AI가 상태 칸을 새로 찾았으면 그 칸의 상태 낱말로 표를 만든다(규칙 먼저, 모르는 것만 AI)
    status_expr = fields.get("status", "")
    if kind == "orders" and "status" in report["filled"] and ":" not in status_expr:
        status_table = propose.statuses(propose.status_values(rows, status_expr))
        proposal.setdefault("values", {})["status"] = status_table
        unknown = [v for v, to in status_table.items() if not to]
        if unknown:
            again = await client.structured(
                PROMPT,
                Suggestion,
                untrusted={"resource": kind, "status_values": unknown},
                context_id=f"site_adapter:propose:{kind}:status",
            )
            _take_statuses(again.statuses, unknown, status_table, report)
    taken = _used(fields)
    report["unplaced"] = [
        {"path": u.path, "meaning": u.meaning}
        for u in suggestion.unplaced[:10]
        if u.path in paths and u.path not in taken
    ]
    return report


def _used(fields: dict[str, str]) -> set[str]:
    return {p for e in fields.values() if e for p in e.split(":")[-1].split(",")}


def _take_statuses(
    picks: list[StatusPick], unknown: list[str], table: dict[str, str], report: dict[str, Any]
) -> None:
    for s in picks:
        if s.value in unknown and s.to in SHOP_ORDER_STATUSES:
            table[s.value] = s.to
            report["statuses"][s.value] = s.to


# ---------------------------------------------------------------- 경로 고르기

# 목록 경로 이름에 들어 있으면 그 자원으로 본다 (앞이 먼저)
PATH_WORDS: dict[Resource, tuple[str, ...]] = {
    "orders": (
        "orders",
        "bookings",
        "reservations",
        "enrollments",
        "purchases",
        "order",
        "booking",
    ),
    "products": ("products", "tours", "courses", "goods", "items", "product", "tour", "course"),
}

PATH_PROMPT = """너는 고객사 API의 목록 경로 중에서 주문(예약·결제) 목록과
상품(투어·강의) 목록을 고른다. 데이터 블록의 paths에 있는 경로만 고른다.
맞는 게 없으면 빈 글자로 둔다. why는 한국어 한 문장.
"""


class PathPick(BaseModel):
    orders_path: str = ""
    products_path: str = ""
    why: str = ""


def rule_paths(paths: list[str]) -> dict[Resource, str]:
    """경로 이름으로 고른 주문·상품 목록 경로 (못 찾으면 빈 글자)."""
    out: dict[Resource, str] = {}
    for kind, words in PATH_WORDS.items():
        out[kind] = ""
        for word in words:
            hit = next((p for p in paths if p.rstrip("/").lower().endswith("/" + word)), "")
            if hit:
                out[kind] = hit
                break
    return out


async def pick_paths(paths: list[str], factory: ClientFactory | None = None) -> dict[Resource, str]:
    """주문·상품 목록 경로. 이름 규칙으로 먼저 찾고, 못 찾은 것만 AI에게 묻는다.

    AI에게는 경로 이름만 보낸다.
    """
    found = rule_paths(paths)
    if all(found.values()) or not paths:
        return found
    pick = await (factory or make_client)().structured(
        PATH_PROMPT,
        PathPick,
        untrusted={"paths": paths[:MAX_PATHS]},
        context_id="site_adapter:propose:paths",
    )
    for kind, value in (("orders", pick.orders_path), ("products", pick.products_path)):
        if not found[kind] and value in paths:
            found[kind] = value
    return found


# 문의 목록 경로 이름 (앞이 먼저). 문의는 이름 규칙으로만 찾는다(못 찾으면 문의 짝 없이 연결)
INQUIRY_PATH_WORDS = ("inquiries", "questions", "qna", "tickets", "contacts", "inquiry", "question")


def rule_inquiry_path(paths: list[str]) -> str:
    """경로 이름으로 고른 문의 목록 경로 (못 찾으면 빈 글자)."""
    for word in INQUIRY_PATH_WORDS:
        hit = next((p for p in paths if p.rstrip("/").lower().endswith("/" + word)), "")
        if hit:
            return hit
    return ""
