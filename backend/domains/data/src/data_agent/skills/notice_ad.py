"""브랜드 공지·광고 게시물 분류 (11/26 C). 코드 규칙, LLM 없음.

11/25 증거: 레이더 "갑자기 뜬 게시물" 12개 중 밈 계정 건은 1개, 나머지는 방송 예고·티켓 공지 같은
브랜드 게시물이었다. 이 게시물은 보고서 근거·밈 재료에서 빼고(보고서 `excluded` reason
notice_ad), 레이더 화면에서는 지우지 않고 칩 "공지·광고"만 붙인다.

규칙 (위에서부터, 하나라도 맞으면 notice_ad)
1. 공식 API 표시: `media_product_type == "AD"` (business_discovery가 주는 값. 유료 광고 표시 칸
   `is_paid_partnership`은 다른 계정 게시물에는 주지 않는다)
2. 캡션에 광고 표시 해시태그·말(`AD_TAGS`: #ad·#광고·#유료광고·#협찬·#sponsored·"유료 광고" 등)
3. 캡션 공지 낱말(`NOTICE_WORDS`: 예매·티켓·오픈·공지·출시·사전예약·이벤트·할인 등) 수가
   브랜드 계정이면 1개 이상, 그 밖(밈·크리에이터)이면 2개 이상. 계정 종류는 공식 API에 없어서
   레이더 계정 메타(`account_kind` industry·brand) 또는 레이더 설정 종류(`source_kind` benchmark =
   기업 계정)로 본다
"""

from __future__ import annotations

from typing import Any

AD_TAGS = ("#ad", "#광고", "#유료광고", "#협찬", "#sponsored", "#paidpartnership", "#pr",
           "유료 광고", "유료광고 포함", "광고 포함", "제작 지원", "협찬")  # fmt: skip
NOTICE_WORDS = ("예매", "티켓", "오픈", "공지", "출시", "사전예약", "사전 예약", "이벤트", "할인",
                "광고", "응모", "당첨", "추첨", "쿠폰", "본방", "방송 예고", "첫 방송", "예고편",
                "단독 공개", "판매", "구매", "선착순", "프로모션", "론칭", "런칭", "기간 한정",
                "신제품", "혜택", "특가", "증정")  # fmt: skip
BRAND_KINDS = frozenset({"industry", "brand"})
CHIP = "공지·광고"


def _is_brand(account_kind: str | None, source_kind: str | None) -> bool:
    if account_kind is not None:
        return account_kind in BRAND_KINDS
    return source_kind != "meme"  # 메타가 없으면 레이더 설정 종류 (benchmark = 기업 계정)


def classify(caption: str | None, media_product_type: str | None = None, *,
             account_kind: str | None = None,
             source_kind: str | None = None) -> str | None:  # fmt: skip
    """공지·광고면 이유, 아니면 None.

    이유: "유료 광고 표시" · "광고 표시 #광고" · "공지 낱말 예매·티켓"."""
    if (media_product_type or "").upper() == "AD":
        return "유료 광고 표시"
    low = (caption or "").lower()
    for tag in AD_TAGS:
        if tag.startswith("#"):
            # 해시태그는 낱말 끝까지 같아야 (#adventure·#광고천재 같은 것은 아님)
            for word in low.replace("\n", " ").split():
                if word.rstrip(".,!?") == tag:
                    return f"광고 표시 {tag}"
        elif tag in low:
            return f"광고 표시 {tag}"
    hits = [w for w in NOTICE_WORDS if w in low]
    need = 1 if _is_brand(account_kind, source_kind) else 2
    if len(hits) >= need:
        return "공지 낱말 " + "·".join(hits[:3])
    return None


def of_post(post: Any) -> str | None:
    """`InstagramRadarPost`(또는 같은 칸이 있는 것) → 이유 또는 None. 예전 저장분에도 쓴다."""
    return classify(getattr(post, "caption_excerpt", None),
                    getattr(post, "media_product_type", None),
                    account_kind=getattr(post, "account_kind", None),
                    source_kind=getattr(post, "source_kind", None))  # fmt: skip


def is_notice_ad(post: Any) -> bool:
    """저장된 표시(`notice_ad`)가 있으면 그것, 없으면(예전 저장분) 규칙으로 다시 본다."""
    return bool(getattr(post, "notice_ad", None) or of_post(post))
