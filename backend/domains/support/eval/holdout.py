"""고객지원 AI holdout 평가 (처음 보는 무작위 입력): uv run python domains/support/eval/holdout.py

무엇을 재나
- 고객지원 AI에서 **정답을 문서(README·모듈 설명)대로 코드 규칙으로 계산할 수 있는 6개 영역**:
  ① 답변 가드(환불 단정·개인정보) ② 개인정보 가리기 ③ 욕설·모욕·위협 규칙(한국어) ④ 분류 필요 자동 판정
  ⑤ 문의 급증 판정 ⑥ 화면에 보이는 언어 판별.
- 뺀 것: 분류·초안·순화·대화방 답(LLM이라 정답이 하나가 아님), 영수증 사진 분석(비전 모델).

어떻게 재나
- seed로 입력을 무작위로 만든다. 정답은 고객지원 코드를 쓰지 않고 **이 파일 안에서 문서대로 따로** 정한다
  (넣은 값으로 만든 입력이라 정답을 이미 알고 있다 / 급증은 원자료에서 문서의 규칙으로 센다).
- 고객지원 코드는 **함수를 직접 부른다**(`guard`·`abuse_rules`·`triage`·`insights`·`languages`).
  다른 부서 holdout처럼 `POST /a2a`로 부르지는 않는다(이 영역들은 skill 안에서 LLM·저장소와 섞여 있어서).
- 문서가 정한 한계(일반전화·"계좌" 단서 없는 계좌번호·기호로 비튼 욕설·이름·주소)는 정답에 넣지 않는다.

규칙: 하네스는 dev seed(`dev`)로만 고치고, holdout seed는 한 번 돌려 숫자만 본다(`--show`가 holdout에선 꺼진다).
"""

# ruff: noqa: E501  (언어별 문장·욕설 말뭉치는 한 줄에 한 묶음씩 둔다)
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
os.environ["AGENT_TEMPLATE_MOCK"] = "1"  # 비밀값·실제 모델 없이 (이 평가는 LLM을 쓰지 않는다)
os.environ.pop("SUPPORT_STATE_FILE", None)

from support_agent import abuse_rules, guard, insights, languages, shop_client, triage  # noqa: E402
from support_agent.schemas import Analysis, Classification, Triage  # noqa: E402
from support_agent.store import InquiryStore  # noqa: E402

HOLDOUT_SEED = "20261110_01"
DEV_SEED = "dev"
NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)

AREAS = ("guard", "mask", "abuse", "triage", "surge", "language")
AREA_KO = {
    "guard": "답변 가드",
    "mask": "개인정보 가리기",
    "abuse": "욕설 규칙",
    "triage": "분류 필요 판정",
    "surge": "문의 급증 판정",
    "language": "언어 판별",
}

# ------------------------------------------------------------------ 말뭉치 (seed마다 다른 쪽을 쓴다)
# dev 말뭉치는 하네스를 고치며 보는 것이고, holdout 말뭉치는 따로 써서 결과 숫자만 본다.

LANG_BANK: dict[str, dict[str, list[str]]] = {
    "dev": {
        "ko": [
            "결제가 두 번 됐어요",
            "배송이 너무 늦어요 확인 부탁드립니다",
            "쿠폰이 적용이 안 돼요",
        ],
        "en": [
            "I was charged twice for my order",
            "The package has not arrived yet",
            "Could you check my refund status please",
        ],
        "ja": ["注文したのに二回も請求されています", "荷物がまだ届いていません"],
        "zh": ["我的订单被扣了两次款", "包裹还没有收到，请帮我查一下"],
        "es": [
            "Me cobraron dos veces por mi pedido",
            "Mi paquete todavía no ha llegado, por favor revisen",
            "Quiero que me devuelvan el dinero ya",
        ],
        "fr": [
            "Ma commande a été facturée deux fois",
            "Je n'ai toujours pas reçu mon colis, merci de vérifier",
            "Je voudrais être remboursé tout de suite",
        ],
        "de": [
            "Meine Bestellung wurde zweimal abgebucht",
            "Das Paket ist noch nicht angekommen, bitte prüfen Sie das",
            "Ich möchte mein Geld zurück",
        ],
        "pt": [
            "Meu pedido foi cobrado duas vezes",
            "O pacote ainda não chegou, por favor verifiquem",
            "Quero o reembolso do meu dinheiro",
        ],
        "it": [
            "Il mio ordine è stato addebitato due volte",
            "Il pacco non è ancora arrivato, per favore controllate",
            "Vorrei il rimborso dei miei soldi",
        ],
        "ru": [
            "Деньги за заказ списали дважды",
            "Посылка до сих пор не пришла, проверьте пожалуйста",
        ],
        "uk": [
            "Гроші за замовлення списали двічі",
            "Посилка досі не прийшла, перевірте будь ласка",
        ],
        "ar": ["تم خصم المبلغ مرتين من حسابي", "لم يصل الطرد حتى الآن"],
        "he": ["חייבו אותי פעמיים על ההזמנה", "החבילה עדיין לא הגיעה"],
        "th": ["ถูกเรียกเก็บเงินสองครั้ง", "พัสดุยังไม่มาถึงเลย"],
        "hi": ["मेरे ऑर्डर का पैसा दो बार कट गया", "पार्सल अभी तक नहीं पहुँचा है"],
    },
    "holdout": {
        "ko": [
            "주문한 상품이 파손되어 도착했습니다",
            "환불은 언제 처리되나요",
            "배송지를 바꾸고 싶은데 어떻게 하나요",
        ],
        "en": [
            "My coupon code does not work at checkout",
            "Please cancel the order I placed this morning",
            "Where can I find the invoice for my purchase",
        ],
        "ja": ["クーポンが使えません、確認してください", "注文をキャンセルしたいのですが"],
        "zh": ["优惠券无法使用，请帮忙看看", "我想取消今天早上下的订单"],
        "es": [
            "El cupón no funciona cuando intento pagar",
            "Quisiera cancelar el pedido que hice esta mañana",
            "¿Dónde puedo encontrar la factura de mi compra?",
        ],
        "fr": [
            "Mon code promo ne fonctionne pas au moment du paiement",
            "Je souhaite annuler la commande passée ce matin",
            "Où puis-je trouver la facture de mon achat ?",
        ],
        "de": [
            "Mein Gutschein funktioniert beim Bezahlen nicht",
            "Ich möchte die Bestellung von heute Morgen stornieren",
            "Wo finde ich die Rechnung zu meinem Kauf?",
        ],
        "pt": [
            "O meu cupom não funciona na hora de pagar",
            "Gostaria de cancelar o pedido que fiz hoje de manhã",
            "Onde encontro a fatura da minha compra?",
        ],
        "it": [
            "Il mio coupon non funziona al momento del pagamento",
            "Vorrei annullare l'ordine fatto stamattina",
            "Dove posso trovare la fattura del mio acquisto?",
        ],
        "ru": [
            "Купон не работает при оплате",
            "Я хочу отменить заказ, который сделал сегодня утром",
        ],
        "uk": [
            "Купон не працює під час оплати",
            "Я хочу скасувати замовлення, яке зробив сьогодні вранці",
        ],
        "ar": ["الكوبون لا يعمل عند الدفع", "أريد إلغاء الطلب الذي قدمته هذا الصباح"],
        "he": ["הקופון לא עובד בעת התשלום", "אני רוצה לבטל את ההזמנה שביצעתי הבוקר"],
        "th": ["คูปองใช้ไม่ได้ตอนชำระเงิน", "ฉันอยากยกเลิกคำสั่งซื้อเมื่อเช้านี้"],
        "hi": ["भुगतान के समय कूपन काम नहीं कर रहा है", "मैं आज सुबह किया गया ऑर्डर रद्द करना चाहता हूँ"],
    },
}
LANG_NOISE = [
    "",
    " ORD-2026-000123",
    " 21,000",
    " 😊",
    " #3",
    " 2026-11-10",
]  # 언어에 중립인 덧붙임만(한글 "원" 같은 것은 넣지 않는다)

# 욕설 말뭉치: 문서(모듈 설명·README)에 적힌 단계 기준으로 쓴 낱말이다
ABUSE_BANK: dict[str, dict[str, list[str]]] = {
    "dev": {
        "profanity": ["씨발", "시발", "ㅅㅂ", "존나 짜증나네", "지랄하네", "썅"],
        "insult": ["병신 같은 사이트", "멍청한 직원들", "쓰레기 같은 서비스", "한심하네요"],
        "threat": ["가만 안 둘 거예요", "죽여버리고 싶네", "찾아가서 따질 겁니다"],
        "legit": [
            "소비자원에 신고하겠습니다",
            "법적으로 고소할 수도 있습니다",
            "공정위에 신고하겠다",
        ],
        "trap": [
            "시발점이 어디인가요",
            "쓰레기통 모양 상품 문의",
            "두개같이 보내 주세요",
            "분리수거 가능한 포장인가요",
        ],
    },
    "holdout": {
        "profanity": ["ㅆㅂ 진짜", "시발 이게 뭐야", "이런 씨발", "개같은 서비스", "좆같네요"],
        "insult": [
            "미친놈들",
            "등신 같아요",
            "닥쳐 주세요",
            "한심한 대응이에요",
            "쓰레기 같은 배송",
        ],
        "threat": [
            "가만두지 않겠습니다",
            "죽이겠다는 말이 아닙니다 찾아가겠어요",
            "불 질러 버리고 싶다",
        ],
        "legit": ["소비자원에 접수하겠습니다", "고소를 고려 중입니다", "신고 절차를 알려 주세요"],
        "trap": [
            "시발역 근처 매장은 있나요",
            "쓰레기봉투 상품이 있나요",
            "세개같이 포장해 주세요",
            "분리수거 안내 부탁드려요",
        ],
    },
}
ABUSE_CLEAN = [
    "주문한 책이 아직 도착하지 않았습니다",
    "결제가 두 번 된 것 같아 확인 부탁드립니다",
    "쿠폰을 사용했는데 할인이 반영되지 않았어요",
    "교환하고 싶은 상품이 있습니다",
    "배송지를 변경할 수 있을까요",
    "영수증을 다시 받을 수 있을까요",
]
ABUSE_EXPECT = {
    "profanity": "profanity",
    "insult": "insult",
    "threat": "threat",
    "legit": "none",
    "trap": "none",
}

REPLY_BANK: dict[str, dict[str, list[str]]] = {
    "dev": {
        "promise": [
            "무조건 환불해 드리겠습니다",
            "100% 환불 보장합니다",
            "반드시 보상해 드립니다",
            "환불을 약속드립니다",
            "꼭 환불받으실 수 있습니다",
        ],
        "safe": [
            "환불이 가능한지 확인 후 안내드리겠습니다",
            "접수되었습니다. 확인해서 연락드리겠습니다",
            "주문 내역을 살펴본 뒤 답변드리겠습니다",
        ],
    },
    "holdout": {
        "promise": [
            "보상을 보장해 드립니다",
            "배상은 확정되었습니다 걱정하지 마세요",
            "환불을 처리해 드릴게요",
            "무조건 배상해 드리겠습니다",
            "쿠폰을 지급해 드리겠습니다",
        ],
        "safe": [
            "가능 여부를 확인하는 대로 알려 드리겠습니다",
            "불편을 드려 죄송합니다. 상황을 확인 중입니다",
            "담당자가 검토한 뒤 결과를 안내드리겠습니다",
        ],
    },
}


# ------------------------------------------------------------------ 입력 만들기 (정답은 만들면서 정한다)
def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _luhn_card(rng: random.Random, n: int = 16) -> str:
    body = [int(c) for c in _digits(rng, n - 1)]
    total = 0
    for index, d in enumerate(reversed(body)):
        d = d * 2 if index % 2 == 0 else d
        total += d - 9 if d > 9 else d
    return "".join(map(str, body)) + str((10 - total % 10) % 10)


def _luhn_valid(number: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(number)):
        d = int(ch) * (2 if index % 2 == 1 else 1)
        total += d - 9 if d > 9 else d
    return total % 10 == 0


def _non_luhn(rng: random.Random, n: int) -> str:
    while True:
        number = _digits(rng, n)
        if not _luhn_valid(number):
            return number


def _grouped(number: str, rng: random.Random) -> str:
    sep = rng.choice(["-", " ", ""])
    return sep.join(number[i : i + 4] for i in range(0, len(number), 4)) if sep else number


def _rrn(rng: random.Random) -> str:
    month, day = rng.randint(1, 12), rng.randint(1, 28)
    year = rng.randint(50, 99) if rng.random() < 0.6 else rng.randint(0, 9)
    return f"{year:02d}{month:02d}{day:02d}{rng.choice('-')}{rng.choice('1234')}{_digits(rng, 6)}"


def _phone(rng: random.Random) -> str:
    mid, last = _digits(rng, 4), _digits(rng, 4)
    return rng.choice(
        [
            f"010-{mid}-{last}",
            f"010{mid}{last}",
            f"010 {mid} {last}",
            f"+82 10-{mid}-{last}",
            f"011-{_digits(rng, 3)}-{last}",
        ]
    )


def _email(rng: random.Random) -> str:
    user = rng.choice(["hong", "kim.min", "book_lover", "a1b2", "shop+cs"]) + _digits(rng, 2)
    return f"{user}@{rng.choice(['example.com', 'mail.co.kr', 'my-domain.net'])}"


def _account(rng: random.Random) -> str:
    return f"계좌번호 {_digits(rng, 3)}-{_digits(rng, 6)}-{_digits(rng, 5)}"


def make_mask_cases(rng: random.Random, n: int) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for _ in range(n):
        kind = rng.choice(["phone", "email", "rrn", "card", "account", "none", "none"])
        lead = rng.choice(["연락처는 ", "제 정보: ", "확인 부탁드립니다. ", "", "문의드립니다 "])
        tail = rng.choice(["입니다.", " 로 연락 주세요", "", " 감사합니다"])
        if kind == "none":
            safe = rng.choice(
                [
                    f"주문번호 ORD-2026-{_digits(rng, 6)}",
                    f"결제 금액 {rng.randint(1, 99)},{_digits(rng, 3)}원",
                    f"2026-11-{rng.randint(1, 28):02d}에 주문",
                    f"송장 {_non_luhn(rng, 13)}",  # 문서: 카드번호는 Luhn 검사를 통과한 13~19자리만 가린다
                    f"상품 {_digits(rng, 5)}번 2개",
                ]
            )
            cases.append({"text": f"{lead}{safe}{tail}", "kind": "none", "secret": None})
            continue
        secret = {"phone": _phone, "email": _email, "rrn": _rrn, "account": _account}.get(kind)
        value = secret(rng) if secret else _grouped(_luhn_card(rng), rng)
        shown = (
            value.split(" ", 1)[1] if kind == "account" else value
        )  # 계좌는 "계좌번호 " 단서는 남는다
        cases.append({"text": f"{lead}{value}{tail}", "kind": kind, "secret": shown})
    return cases


def score_mask(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cases:
        masked, kinds = guard.mask_pii(c["text"])
        if c["kind"] == "none":
            ok = masked == c["text"] and not kinds  # 지워지면 안 된다
        else:
            ok = c["secret"].replace(" ", "") not in masked.replace(" ", "") and c["kind"] in kinds
        out.append({"ok": ok, "kind": c["kind"], "text": c["text"], "got": [masked, kinds]})
    return out


def make_guard_cases(
    rng: random.Random, bank: dict[str, list[str]], n: int
) -> list[dict[str, Any]]:
    cases = []
    for _ in range(n):
        kind = rng.choice(["promise", "safe", "safe", "pii", "pii_safe"])
        head = rng.choice(["안녕하세요 고객님. ", "문의 주셔서 감사합니다. ", ""])
        if kind == "promise":
            body, expect = rng.choice(bank["promise"]), "promise_refund"
        elif kind == "safe":
            body, expect = rng.choice(bank["safe"]), None
        elif kind == "pii":
            value = rng.choice(
                [_phone(rng), _email(rng), _rrn(rng), _grouped(_luhn_card(rng), rng)]
            )
            body, expect = f"확인된 정보는 {value} 입니다.", "pii_in_reply"
        else:
            body, expect = (
                f"주문번호 ORD-2026-{_digits(rng, 6)} 건은 {rng.randint(1, 9)}일 안에 안내드립니다.",
                None,
            )
        cases.append({"text": head + body, "expect": expect, "kind": kind})
    return cases


def score_guard(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cases:
        errors = {f.code for f in guard.check_reply(c["text"]).findings if f.level == "error"}
        ok = (
            (c["expect"] in errors) if c["expect"] else not errors
        )  # 막아야 할 것은 막고 통과할 것은 통과
        out.append({"ok": ok, "kind": c["kind"], "text": c["text"], "got": sorted(errors)})
    return out


def make_abuse_cases(
    rng: random.Random, bank: dict[str, list[str]], n: int
) -> list[dict[str, Any]]:
    cases = []
    for _ in range(n):
        kind = rng.choice(["profanity", "insult", "threat", "legit", "trap", "clean", "clean"])
        base = rng.choice(ABUSE_CLEAN)
        if kind == "clean":
            text, expect = base, "none"
        else:
            term = rng.choice(bank[kind])
            text = rng.choice([f"{term}. {base}", f"{base}. {term}", f"{base} {term}"])
            expect = ABUSE_EXPECT[kind]
        cases.append({"text": text, "expect": expect, "kind": kind})
    return cases


def score_abuse(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cases:
        shown, level, _kinds = abuse_rules.assess(c["text"])
        # 문서는 단계 순서(위협 > 모욕 > 비속어)만 정하고 낱말별 단계는 정하지 않는다: 욕설·모욕은 "찾았나",
        # 위협은 정확히 위협인지, 없어야 할 것은 하나도 안 가리는지를 본다
        if c["expect"] in ("profanity", "insult"):
            ok = level in ("profanity", "insult", "threat")
        else:
            ok = level == c["expect"] and (level != "none" or shown == c["text"])
        out.append({"ok": ok, "kind": c["kind"], "text": c["text"], "got": level})
    return out


def make_language_cases(
    rng: random.Random, bank: dict[str, list[str]], n: int
) -> list[dict[str, Any]]:
    cases = []
    for _ in range(n):
        lang = rng.choice(list(bank))
        parts = rng.sample(bank[lang], k=min(len(bank[lang]), rng.choice([1, 1, 2])))
        sep = "。" if lang in ("ja", "zh") else ". "
        text = sep.join(parts) + rng.choice(LANG_NOISE)
        cases.append({"text": text, "expect": lang, "kind": lang})
    return cases


def score_language(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "ok": languages.detect_detail(c["text"]) == c["expect"],
            "kind": c["kind"],
            "text": c["text"],
            "got": languages.detect_detail(c["text"]),
        }
        for c in cases
    ]


# 분류 필요: 문서 규칙(README "분류 필요 문의와 회사별 관리")대로 정답을 정한다
def make_triage_cases(rng: random.Random, n: int) -> list[dict[str, Any]]:
    cases = []
    for _ in range(n):
        category = rng.choice(["general", "payment", "payment", "shipping", "refund", "coupon"])
        context = rng.choice(["ctx_req_", "ctx_req_", "ctx_inc_"])
        has_order = rng.random() < 0.7
        mode = rng.choice(
            ["failed", "clean", "error", "missing"]
        )  # 주문에 실패한 결제가 있나 / 없나 / 못 읽음 / 주문 자체 없음
        source = rng.choice(["live", "live", "live", "seed"])
        existing = rng.random() < 0.1
        if existing:
            expect = None
        elif category == "general":
            expect = "unclassified"
        elif category != "payment" or context == "ctx_inc_":
            expect = None
        elif not has_order:
            expect = "no_error_code"
        elif source == "seed" or mode == "error":
            expect = None
        else:
            expect = "no_error_code" if mode in ("clean", "missing") else None
        cases.append(
            {
                "category": category,
                "context": context + _digits(rng, 4),
                "has_order": has_order,
                "mode": mode,
                "source": source,
                "existing": existing,
                "expect": expect,
            }
        )
    return cases


def _analysis(category: str) -> Analysis:
    return Analysis(
        classification=Classification(
            intent_ko="문의",
            category=category,
            sentiment="calm",
            urgency="medium",
            search_query=category,
        )
    )


def score_triage(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    saved = (shop_client.order_source, shop_client.fetch_order_facts)
    try:
        for c in cases:
            store = InquiryStore()
            conv = store.create(
                context_id=c["context"],
                tenant_id="shop-demo",
                user_id=1,
                customer_name="고객",
                subject="문의",
                order_id="ord_1" if c["has_order"] else None,
                order_no=None,
                first_message="문의",
                analysis=_analysis(c["category"]),
            )
            if c["existing"]:
                conv.triage = Triage(
                    state="done", reason="manual", marked_by="agent", marked_at=conv.updated_at
                )  # type: ignore[arg-type]

            async def fetch(_order_id: str, mode: str = c["mode"]) -> Any:
                if mode == "error":
                    raise shop_client.ShopError("UNAVAILABLE", "boom")
                if mode == "missing":
                    return None
                return {
                    "payments": [{"status": "failed"}] if mode == "failed" else [{"status": "paid"}]
                }

            shop_client.order_source = lambda s=c["source"]: s  # type: ignore[assignment]
            shop_client.fetch_order_facts = fetch  # type: ignore[assignment]
            got = asyncio.run(triage.assess(conv))
            out.append(
                {
                    "ok": got == c["expect"],
                    "kind": f"{c['category']}",
                    "text": json.dumps(c, ensure_ascii=False),
                    "got": got,
                }
            )
    finally:
        shop_client.order_source, shop_client.fetch_order_facts = saved  # type: ignore[assignment]
    return out


# 문의 급증: 문서 규칙(창 안 건수 ≥ 기준 건수, 평소 창당 건수의 배수 이상이거나 평소가 0)대로 원자료에서 센다
def make_surge_cases(rng: random.Random, n: int) -> list[dict[str, Any]]:
    cases = []
    for _ in range(n):
        window, baseline_hours = rng.choice([(10, 24), (30, 12), (60, 24)])
        min_count, ratio = rng.choice([(3, 2.0), (5, 3.0), (5, 3.0)])
        events = []
        for topic in rng.sample(["payment", "shipping", "refund", "coupon"], k=rng.randint(1, 3)):
            recent = rng.choice([0, 1, 2, 4, 5, 6, 9])
            earlier = rng.choice([0, 0, 2, 6, 20, 50])
            events += [(topic, rng.uniform(0.01, window - 0.01)) for _ in range(recent)]
            events += [
                (topic, window + rng.uniform(0.01, baseline_hours * 60 - 0.01))
                for _ in range(earlier)
            ]
        cases.append(
            {
                "window": window,
                "baseline_hours": baseline_hours,
                "min_count": min_count,
                "ratio": ratio,
                "events": events,
            }
        )
    return cases


def _expected_surges(c: dict[str, Any]) -> list[str]:
    window, base_min = c["window"], c["baseline_hours"] * 60
    recent, earlier = Counter(), Counter()
    for topic, minutes_ago in c["events"]:
        if minutes_ago <= window:
            recent[topic] += 1
        elif minutes_ago <= window + base_min:
            earlier[topic] += 1
    out = []
    for topic, count in recent.items():
        per_window = earlier[topic] / (base_min / window)
        if count >= c["min_count"] and (per_window == 0 or count / per_window >= c["ratio"]):
            out.append(topic)
    return sorted(out)


def score_surge(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for c in cases:
        store = InquiryStore()
        for topic, minutes_ago in c["events"]:
            conv = store.create(
                context_id="ctx_req_0001",
                tenant_id="shop-demo",
                user_id=1,
                customer_name="고객",
                subject="문의",
                order_id=None,
                order_no=None,
                first_message="문의",
                analysis=_analysis(topic),
            )
            stamp = (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            conv.messages[0].created_at = stamp
            conv.updated_at = stamp
        report = insights.detect_surge(
            store,
            NOW,
            window_minutes=c["window"],
            baseline_hours=c["baseline_hours"],
            min_count=c["min_count"],
            ratio=c["ratio"],
            baseline_method="mean",
        )
        got = sorted(s.topic_code for s in report.surges)
        want = _expected_surges(c)
        out.append(
            {
                "ok": got == want,
                "kind": f"{c['window']}분",
                "text": f"창 {c['window']}분 기준 {c['min_count']}건 {c['ratio']}배",
                "got": got,
                "want": want,
            }
        )
    return out


# ------------------------------------------------------------------ 실행
def run(seed: str, n: int) -> dict[str, Any]:
    rng = random.Random(seed)
    split = "holdout" if seed == HOLDOUT_SEED else "dev"  # holdout 말뭉치는 holdout seed에서만 쓴다
    results = {
        "guard": score_guard(make_guard_cases(rng, REPLY_BANK[split], n)),
        "mask": score_mask(make_mask_cases(rng, n)),
        "abuse": score_abuse(make_abuse_cases(rng, ABUSE_BANK[split], n)),
        "triage": score_triage(make_triage_cases(rng, n)),
        "surge": score_surge(make_surge_cases(rng, max(n // 5, 20))),
        "language": score_language(make_language_cases(rng, LANG_BANK[split], n)),
    }
    return results


def summarize(results: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    areas: dict[str, Any] = {}
    for area, rows in results.items():
        by_kind: dict[str, list[int]] = {}
        for r in rows:
            by_kind.setdefault(r["kind"], [0, 0])[0] += int(r["ok"])
            by_kind[r["kind"]][1] += 1
        ok = sum(int(r["ok"]) for r in rows)
        areas[area] = {
            "ok": ok,
            "total": len(rows),
            "rate": round(ok / len(rows), 4),
            "by_kind": {k: f"{a}/{b}" for k, (a, b) in sorted(by_kind.items())},
        }
    ok = sum(a["ok"] for a in areas.values())
    total = sum(a["total"] for a in areas.values())
    return {"areas": areas, "ok": ok, "total": total, "rate": round(ok / total, 4)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=200, help="영역마다 입력 수")
    parser.add_argument(
        "--holdout",
        action="store_true",
        help=f"holdout seed({HOLDOUT_SEED})로 한 번 돌려 숫자만 본다",
    )
    parser.add_argument(
        "--seed", default=None, help="seed를 직접 정한다(holdout seed가 아니면 dev 말뭉치)"
    )
    parser.add_argument(
        "--show", type=int, default=0, help="틀린 입력을 영역마다 몇 개 보여 준다(dev seed만)"
    )
    args = parser.parse_args()
    seed = args.seed or (HOLDOUT_SEED if args.holdout else DEV_SEED)
    holdout = seed == HOLDOUT_SEED  # 그 밖의 seed는 dev 말뭉치로 만들고 틀린 입력도 보여 준다
    results = run(seed, args.n)
    summary = summarize(results)
    summary.update({"seed": seed, "n_per_area": args.n})
    name = (
        f"holdout_{seed}.json"
        if holdout
        else ("dev.json" if seed == DEV_SEED else f"dev_{seed}.json")
    )
    (HERE / "results").mkdir(exist_ok=True)
    (HERE / "results" / name).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"seed {seed}  영역마다 {args.n}개  전체 {summary['rate'] * 100:.1f}% ({summary['ok']}/{summary['total']})"
    )
    for area, a in summary["areas"].items():
        print(
            f"  {AREA_KO[area]:<10} {a['rate'] * 100:5.1f}% ({a['ok']}/{a['total']})  {a['by_kind']}"
        )
    if args.show and not holdout:
        for area, rows in results.items():
            for r in [r for r in rows if not r["ok"]][: args.show]:
                print(
                    f"  [틀림 {AREA_KO[area]}/{r['kind']}] {r['text'][:100]!r} → {r['got']}"
                    + (f" (정답 {r['want']})" if "want" in r else "")
                )
    elif args.show:
        print("  (holdout seed에서는 틀린 입력을 보여 주지 않는다)")
    sys.exit(0)


if __name__ == "__main__":
    main()
