"""고객 메시지의 언어를 문자 종류로 판별한다 (코드만, LLM 없음).

지원하는 답변 언어는 한국어(`ko`)·영어(`en`)·일본어(`ja`)·중국어(`zh`)다. 답변 가드(`guard.py`)가
이 네 언어의 약속 표현을 검사할 수 있기 때문이다. 그 밖의 언어로 온 문의는 영어로 답한다.

판별 규칙(글자 수가 가장 많은 쪽): 한글 → `ko`, 가나 → `ja`, 한자(가나·한글 없음) → `zh`,
라틴 문자 → `en`, 그 밖의 문자(키릴·아랍·태국어 등) → `other`. 글자가 없으면(숫자·기호뿐) `ko`다.
라틴 문자 언어는 영어와 구분하지 못한다(스페인어 등도 `en`). 그래서 가드는 악센트 있는 라틴 글자가
있으면 경고한다.

`detect`는 이 거친 판별이고(답변 언어·가드가 쓴다), 화면에 보이는 언어 표시는
`detect_detail`이 한 단계 더 나눈다. 스페인어·프랑스어·독일어·포르투갈어·이탈리아어
(`es`·`fr`·`de`·`pt`·`it`), 러시아어·우크라이나어(`ru`·`uk`), 아랍어·히브리어·태국어·힌디어
(`ar`·`he`·`th`·`hi`)까지 알려 준다. 라틴 문자는 악센트와 흔한 낱말(자주 쓰는 짧은 낱말)로
세어서, 근거가 모자라면 영어로 둔다.
"""

from __future__ import annotations

import re

SUPPORTED = ("ko", "en", "ja", "zh")
NAMES = {"ko": "Korean", "en": "English", "ja": "Japanese", "zh": "Chinese"}

_HANGUL = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힯]")
_KANA = re.compile(r"[぀-ゟ゠-ヿㇰ-ㇿ]")
_HAN = re.compile(r"[㐀-䶿一-鿿]")
_LATIN = re.compile(r"[A-Za-zÀ-ɏ]")
_OTHER = re.compile(r"[Ѐ-ӿ֐-ۿݐ-ݿ฀-๿ऀ-ॿ]")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
_UKRAINIAN = re.compile(r"[іїєґІЇЄҐ]")
_HEBREW = re.compile(r"[֐-׿]")
_ARABIC = re.compile(r"[؀-ۿݐ-ݿ]")
_THAI = re.compile(r"[฀-๿]")
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-zà-ɏ']+")
ACCENTED_LATIN = re.compile(r"[À-ÿĀ-ɏ]")


def detect(text: str) -> str:
    """`ko`·`en`·`ja`·`zh`·`other` 중 하나."""
    counts = {
        "ko": len(_HANGUL.findall(text)),
        "ja": len(_KANA.findall(text)),
        "zh": len(_HAN.findall(text)),
        "en": len(_LATIN.findall(text)),
        "other": len(_OTHER.findall(text)),
    }
    if counts["ja"]:  # 일본어 문장에는 한자가 섞이므로 가나가 있으면 일본어다
        counts["ja"] += counts["zh"]
        counts["zh"] = 0
    if counts["ko"]:  # 한국어 문장에도 한자·영문이 섞인다
        counts["zh"] = 0
    language, count = max(counts.items(), key=lambda item: item[1])
    return language if count else "ko"


# 라틴 문자 언어별 흔한 낱말(영어와 겹치지 않는 것 위주)과 그 언어에만 있는 악센트 글자
_MARKERS: dict[str, tuple[frozenset[str], re.Pattern[str]]] = {
    "es": (
        frozenset(
            "el la los las una unos de del que por para con mi me nos son pero muy como cuando "
            "dos veces cobraron pedido dinero devuélvanme gracias hola está esta ya".split()
        ),
        re.compile(r"[ñ¿¡]"),
    ),
    "fr": (
        frozenset(
            "le la les un une des du de et est pour avec mon ma mes nous vous je ne pas par "
            "facturé commande remboursez remboursement merci bonjour tout très deux fois".split()
        ),
        re.compile(r"[çœ]|[àâêîôû]"),
    ),
    "de": (
        frozenset(
            "der die das den dem ein eine und ist nicht ich mich mir mein meine für mit von "
            "bei auf zweimal bestellung geld erstattet bitte danke hallo eure ihr sofort".split()
        ),
        re.compile(r"[äöüß]"),
    ),
    "pt": (
        frozenset(
            "o a os as um uma de do da que não para com meu minha por mas muito obrigado olá "
            "duas vezes pedido dinheiro reembolso você vocês".split()
        ),
        re.compile(r"[ãõ]"),
    ),
    "it": (
        frozenset(
            "il lo gli un una di del che per con mio mia ma molto grazie ciao due volte ordine "
            "soldi rimborso voi sono non è".split()
        ),
        re.compile(
            r"$^"
        ),  # 이탈리아어는 고유 악센트 글자가 없어(아무것도 안 잡는 패턴) 낱말 목록으로만 판별한다
    ),
}


def detect_detail(text: str) -> str:
    """화면에 보일 언어 코드. `detect`보다 한 단계 더 나눈다(근거가 모자라면 그 결과 그대로)."""
    coarse = detect(text)
    if coarse == "other":
        if _THAI.search(text):
            return "th"
        if _DEVANAGARI.search(text):
            return "hi"
        if _HEBREW.search(text):
            return "he"
        if _ARABIC.search(text):
            return "ar"
        if _CYRILLIC.search(text):
            return "uk" if _UKRAINIAN.search(text) else "ru"
        return coarse
    if coarse != "en":
        return coarse
    words = _WORD.findall(text.lower())
    scores = {
        language: sum(w in common for w in words) + 2 * len(accents.findall(text.lower()))
        for language, (common, accents) in _MARKERS.items()
    }
    best, score = max(scores.items(), key=lambda item: item[1])
    english = sum(w in _ENGLISH for w in words)
    return best if score >= 2 and score > english else coarse


_ENGLISH = frozenset(
    "the a an and is are was were of to in for with my me i you your we it this that not have has "
    "been twice site order refund charge charged money back now please thanks hello".split()
)


def reply_language(detected: str) -> str:
    """고객에게 답할 언어. 가드가 검사하지 못하는 언어는 영어로 답한다."""
    return detected if detected in SUPPORTED else "en"
