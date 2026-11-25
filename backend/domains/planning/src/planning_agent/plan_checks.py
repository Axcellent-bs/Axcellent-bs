"""영상 제작안 코드 검사 (LLM 아님, 11/26).

의뢰 지시(request_rules): 의뢰문(고객 글, untrusted)에서 제작안 LLM이 뽑은 연출 지시를 제작안이
지켰는지 코드로 본다. 지시를 뽑는 것만 LLM이고, 지켰는지(kept)·어디서(where)는 여기서 정한다.

- quote: 그 문장이 어떤 장면 caption 또는 대본(script)에 글자 그대로 있나(공백·문장부호 무시).
  VideoScene에는 대사 칸이 없어 caption·script만 본다
- ending: 마지막 장면이 엔드카드만이 아니고 그 지시 낱말이 겹치나
- setting·prop·action·look: 장면 visual·start_frame·end_frame(look은 prompt도)에 핵심 낱말이 있나
  (낱말 절반 이상)
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

RULE_KINDS = ("quote", "ending", "setting", "prop", "action", "look")
MAX_RULES = 6
RULE_TEXT_MAX = 120
END_CARD_WORDS = ("엔드 카드", "엔드카드", "end card")
_WORD = re.compile(r"[0-9A-Za-z가-힣]+")
# 낱말 끝 조사 (긴 것부터). 떼고도 두 글자 이상 남을 때만 뗀다
_JOSA = (
    "에서는", "에서", "으로", "에게", "까지", "부터", "처럼", "이랑", "하고", "랑", "을", "를",
    "이", "가", "은", "는", "에", "와", "과", "로", "도", "만", "의",
)  # fmt: skip
# 지시 글에 흔한, 화면에 안 나오는 말
_STOP = frozenset(
    {"그대로", "정확히", "반드시", "꼭", "마지막", "대사", "장면", "영상", "화면", "끝나면", "하는",
     "있는", "모습", "느낌", "주세요", "해주세요", "합니다", "한다", "the", "and", "with"}
)  # fmt: skip


@dataclass(frozen=True)
class Scene:
    """검사에 쓰는 장면 글 (LLM 답·계약 장면 어느 쪽이든)."""

    n: int
    visual: str = ""
    caption: str = ""
    prompt: str = ""
    start_frame: str = ""
    end_frame: str = ""


def scene_of(n: int, s: Any) -> Scene:
    def get(k: str) -> str:
        return str(getattr(s, k, "") or "")

    return Scene(n, get("visual"), get("caption"), get("prompt"), get("start_frame"),
                 get("end_frame"))  # fmt: skip


def bare(text: str) -> str:
    """공백·문장부호를 뺀 소문자 (따옴표 문장 비교)."""
    return re.sub(r"[\s\W_]+", "", text or "").lower()


def is_end_card(text: str) -> bool:
    low = (text or "").lower()
    return any(w in low for w in END_CARD_WORDS)


def _stem(word: str) -> str:
    for j in _JOSA:
        if word.endswith(j) and len(word) - len(j) >= 2:
            return word[: -len(j)]
    return word


def keywords(text: str) -> list[str]:
    """핵심 낱말 (두 글자 이상, 조사 뗌, 흔한 말 뺌, 순서 유지·중복 없음)."""
    out: list[str] = []
    for w in _WORD.findall(text or ""):
        s = _stem(w.lower())
        if len(s) >= 2 and s not in _STOP and s not in out:
            out.append(s)
    return out


def _hits(words: list[str], text: str) -> int:
    low = text.lower()
    return sum(1 for w in words if w in low)


def _enough(words: list[str], text: str) -> bool:
    return bool(words) and _hits(words, text) >= math.ceil(len(words) / 2)


def scene_span(ns: list[int]) -> str:
    """[1,2,3] → "장면 1~3", [2] → "장면 2", [1,3] → "장면 1, 3"."""
    if not ns:
        return ""
    if len(ns) > 1 and ns == list(range(ns[0], ns[-1] + 1)):
        return f"장면 {ns[0]}~{ns[-1]}"
    return "장면 " + ", ".join(str(n) for n in ns)


def clean_rules(raw: Sequence[Any] | None) -> list[dict[str, str]]:
    """LLM이 뽑은 지시 → {kind, text_ko} (모르는 종류·빈 글 버림, 같은 글 하나, 최대 6)."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in raw or []:
        kind = str(getattr(r, "kind", "") or (r.get("kind") if isinstance(r, dict) else "") or "")
        text = getattr(r, "text_ko", None)
        if text is None and isinstance(r, dict):
            text = r.get("text_ko")
        text = " ".join(str(text or "").split())[:RULE_TEXT_MAX]
        key = bare(text)
        if kind not in RULE_KINDS or not key or key in seen:
            continue
        seen.add(key)
        out.append({"kind": kind, "text_ko": text})
    return out[:MAX_RULES]


def check_rule(rule: dict[str, str], scenes: list[Scene], script: str) -> tuple[bool, str]:
    """(지켰나, 어디서)."""
    kind, text = rule["kind"], rule["text_ko"]
    if kind == "quote":
        want = bare(text)
        for s in scenes:
            if want and want in bare(s.caption):
                return True, f"장면 {s.n} 자막"
        return (True, "대본") if want and want in bare(script) else (False, "")
    words = keywords(text)
    if kind == "ending":
        if not scenes:
            return False, ""
        last = scenes[-1]
        body = " ".join((last.visual, last.start_frame, last.end_frame, last.caption))
        if is_end_card(last.visual):
            return False, ""
        return (True, f"장면 {last.n}") if not words or _hits(words, body) >= 1 else (False, "")
    found = []
    for s in scenes:
        body = " ".join((s.visual, s.start_frame, s.end_frame))
        if kind == "look":
            body += " " + s.prompt
        if _enough(words, body):
            found.append(s.n)
    return (bool(found), scene_span(found))


def check_rules(
    rules: list[dict[str, str]], scenes: list[Scene], script: str
) -> list[dict[str, Any]]:
    """지시마다 {kind, text_ko, kept, where} (계약 RequestRule 모양)."""
    out = []
    for r in rules:
        kept, where = check_rule(r, scenes, script)
        out.append({**r, "kept": kept, "where": where})
    return out


KIND_KO = {"quote": "대사", "ending": "마지막 장면", "setting": "배경", "prop": "소품",
           "action": "동작", "look": "외형"}  # fmt: skip


def rule_fix(rule: dict[str, Any]) -> str:
    """다시 쓰기 "고칠 점" 한 줄 (못 지킨 의뢰 지시)."""
    kind = rule["kind"]
    tail = {
        "quote": "어떤 장면 caption에 글자 그대로 쓴다",
        "ending": "마지막 장면을 엔드카드만으로 두지 말고 이 지시 장면으로 쓴다",
    }.get(kind, "장면 visual·start_frame·end_frame에 보이게 쓴다")
    return f"의뢰 지시({KIND_KO[kind]}) '{rule['text_ko']}'를 지키지 않았다: {tail}"


def quotes(rules: list[dict[str, Any]]) -> set[str]:
    """비우지 않을 자막(의뢰 quote 문장, bare)."""
    return {bare(r["text_ko"]) for r in rules if r.get("kind") == "quote"} - {""}


def strip_quotes(text: str, rules: list[dict[str, Any]]) -> str:
    """의뢰 quote 문장을 뺀 글 (그 문장은 마케팅 금칙 검사에서 통과시킨다)."""
    for r in rules:
        if r.get("kind") == "quote" and r.get("text_ko"):
            text = text.replace(r["text_ko"], " ")
    return text


_SEGMENT = re.compile(r"\d{1,3}\s*[~∼-]\s*\d{1,3}\s*초")


def script_segments(script: str) -> int:
    """대본에 적힌 시간 구간 수 ("0~2초" 같은 것). 없으면 0."""
    return len(_SEGMENT.findall(script or ""))


def even_cuts(duration: int, n: int) -> list[tuple[int, int]]:
    cuts = [round(duration * i / n) for i in range(n + 1)]
    return [(cuts[i], cuts[i + 1]) for i in range(n)]


def cuts_ok(cuts: list[tuple[int, int]], duration: int, n: int) -> bool:
    """장면 n개와 구간이 1:1이고 0초부터 빈틈없이 duration에 끝나나."""
    if len(cuts) != n or not cuts or cuts[0][0] != 0 or cuts[-1][1] != duration:
        return False
    return all(a < b for a, b in cuts) and all(
        cuts[i][1] == cuts[i + 1][0] for i in range(len(cuts) - 1)
    )
