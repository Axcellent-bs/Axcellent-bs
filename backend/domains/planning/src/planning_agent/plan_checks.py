"""영상 제작안 코드 검사 (LLM 아님, 11/26).

의뢰 지시(request_rules): 의뢰문(고객 글, untrusted)에서 제작안 LLM이 뽑은 연출 지시를 제작안이
지켰는지 코드로 본다. 지시를 뽑는 것만 LLM이고, 지켰는지(kept)·어디서(where)는 여기서 정한다.

- quote: 그 문장이 어떤 장면 caption 또는 대본(script)에 글자 그대로 있나(공백·문장부호 무시).
  VideoScene에는 대사 칸이 없어 caption·script만 본다
- ending: 마지막 장면이 엔드카드만이 아니고 그 지시 낱말이 겹치나
- setting·prop·action·look: 장면 visual·start_frame·end_frame(look은 prompt도)에 핵심 낱말이 있나
  (낱말 절반 이상)

제작 가능성(feasibility): 참조 없는 인물(회사 캐릭터가 있으면 다른 인물이 가까이, 없으면 인물 수)·
장면 안 동작 수·장소 전환 횟수 (아래 G 칸, 11/26 기준 고침).
"""

from __future__ import annotations

import math
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ax_agent_template import plan_rules

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


QUOTE_NOT_IN_REQUEST = "quote_not_in_request"  # 버린 quote 로그 이유 코드 (문장은 남기지 않는다)


def real_quotes(
    rules: list[dict[str, str]], request_text: str
) -> tuple[list[dict[str, str]], int]:
    """의뢰문에 실제로 있는 quote만 남긴다 (11/26 예진 님 리뷰, 공백·문장부호 무시).

    LLM이 의뢰문에 없는 문장을 quote로 적으면 그 문장이 금칙어 검사에서 빠지므로 버린다.
    돌려주는 값: (남긴 지시, 버린 quote 수). quote가 아닌 지시는 그대로 둔다."""
    request = bare(request_text)
    kept = [r for r in rules if r["kind"] != "quote" or (request and bare(r["text_ko"]) in request)]
    return kept, len(rules) - len(kept)


# 광고 금칙어(성과 과장·인력 대체). 마케팅 sns.FORBIDDEN_CLAIM_PATTERNS와 같은 값이고, 마케팅
# video_draft가 제작안 concept+script 전체를 has_forbidden_claim으로 본다(quote 예외 없음).
# 도메인끼리 import하지 않아 옮겨 둔다(video_plan.CLAIM_PATTERNS도 이 값). 공백을 뺀 글에서 찾는다
CLAIM_PATTERNS = (
    r"인건비(를|가|은|는)?절감",
    r"직원(을|이|은|는)?(모두|전부)?대체",
    r"에이전트(가|는|들이)?(더|훨씬)?빨라",
    r"최고",
    r"무조건",
    r"획기적",
    r"압도적",
    r"혁신적",
)
BLOCKED_WHERE = "광고 금칙어라 넣지 않아요"  # 금칙어 든 의뢰 대사 (화면 ✕ 줄, 11/26 S)


def has_claim(text: str) -> bool:
    """광고 금칙어가 있나 (마케팅 has_forbidden_claim과 같은 기준)."""
    squeezed = re.sub(r"\s+", "", text or "")
    return any(re.search(pat, squeezed) for pat in CLAIM_PATTERNS)


def blocked_quote(rule: dict[str, Any]) -> bool:
    """광고 금칙어가 든 의뢰 대사 (넣지 않고, 금칙어 검사에서도 빼지 않는다)."""
    return rule.get("kind") == "quote" and has_claim(str(rule.get("text_ko") or ""))


# 11/26 G: 채널 이름 엔드카드는 마케팅 코드가 마지막 2초에 늘 얹는다. "끝에 채널 이름 넣기"처럼
# 회사·채널 이름·로고·엔드카드를 끝에 넣으라는 말뿐인 ending 지시는 코드가 지킨다
# (마지막 장면 글로 보지 않음)
END_CARD_WHERE = "엔드카드(마지막 2초, 코드)"
END_CARD_MARKS = ("채널", "로고", "엔드카드", "엔드", "이름", "channel", "logo", "end", "card",
                  "name")  # fmt: skip
# 연출이 아닌 말 (낱말 앞부분이 같으면 뺀다: 넣어줘·나오게·보이게 해줘)
END_CARD_FILLER = (
    "끝", "마지막", "넣", "나오", "나와", "보이", "보여", "표시", "띄우", "띄워", "해줘", "해주",
    "하기", "하게", "주세요", "줘", "카드", "put", "show", "add", "at", "the",
)  # fmt: skip


def end_card_only(text: str, company: str = "") -> bool:
    """회사·채널 이름·로고·엔드카드를 끝에 넣으라는 말뿐인가 (남는 연출 낱말이 없음)."""
    names = [w for w in keywords(company) if w]
    words = keywords(text)
    marks = [w for w in words if w in names or any(w.startswith(m) for m in END_CARD_MARKS)]
    rest = [w for w in words if w not in marks
            and not any(w.startswith(f) for f in END_CARD_FILLER)]  # fmt: skip
    return bool(marks) and not rest


def check_rule(
    rule: dict[str, str], scenes: list[Scene], script: str, company: str = ""
) -> tuple[bool, str]:
    """(지켰나, 어디서). 금칙어 든 대사는 늘 (False, BLOCKED_WHERE).

    company: 회사 이름 (ending 지시가 채널 이름 엔드카드뿐인지 볼 때)."""
    kind, text = rule["kind"], rule["text_ko"]
    if blocked_quote(rule):
        return False, BLOCKED_WHERE
    if kind == "ending" and end_card_only(text, company):
        return True, END_CARD_WHERE
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
    rules: list[dict[str, str]], scenes: list[Scene], script: str, company: str = ""
) -> list[dict[str, Any]]:
    """지시마다 {kind, text_ko, kept, where} (계약 RequestRule 모양)."""
    out = []
    for r in rules:
        kept, where = check_rule(r, scenes, script, company)
        out.append({**r, "kept": kept, "where": where})
    return out


KIND_KO = {"quote": "대사", "ending": "마지막 장면", "setting": "배경", "prop": "소품",
           "action": "동작", "look": "외형"}  # fmt: skip


def rule_fix(rule: dict[str, Any]) -> str:
    """다시 쓰기 "고칠 점" 한 줄 (못 지킨 의뢰 지시)."""
    kind = rule["kind"]
    if blocked_quote(rule):
        return (f"의뢰 대사 '{rule['text_ko']}'에는 광고 금칙어가 있어 넣지 않는다: 장면 caption·"
                "script·concept에서 그 문장을 뺀다")  # fmt: skip
    tail = {
        "quote": "어떤 장면 caption에 글자 그대로 쓴다",
        "ending": "마지막 장면을 엔드카드만으로 두지 말고 이 지시 장면으로 쓴다",
    }.get(kind, "장면 visual·start_frame·end_frame에 보이게 쓴다")
    return f"의뢰 지시({KIND_KO[kind]}) '{rule['text_ko']}'를 지키지 않았다: {tail}"


def quotes(rules: list[dict[str, Any]]) -> set[str]:
    """비우지 않을 자막(의뢰 quote 문장, bare). 금칙어 든 대사는 넣지 않으니 뺀다."""
    return {bare(r["text_ko"]) for r in rules
            if r.get("kind") == "quote" and not blocked_quote(r)} - {""}  # fmt: skip


def strip_quotes(text: str, rules: list[dict[str, Any]]) -> str:
    """의뢰 quote 문장을 뺀 글 (그 문장은 마케팅 금칙 검사에서 통과시킨다).

    11/26 S: 광고 금칙어가 든 대사는 빼지 않는다 (마케팅 video_draft가 quote 예외 없이 거절)."""
    for r in rules:
        if r.get("kind") == "quote" and r.get("text_ko") and not blocked_quote(r):
            text = text.replace(r["text_ko"], " ")
    return text


# ---------------------------------------------------------------- 제작 가능성 (G, 11/26)
# AI 영상(Flash 15초 한 번 생성)이 잘 못 만드는 것: 참조 없는 얼굴(영상 참조는 회사 캐릭터 1명뿐),
# 장면 안 여러 동작, 장면마다 바뀌는 장소(한 번 생성이라 전환이 어색하거나 장소가 섞인다).
# 영상을 다시 만들면 크레딧이 다시 나가니 영상 전에 기획이 거른다.
# 11/26 기준 고침: 인물은 인원수가 아니라 "참조 없는 얼굴"(회사 캐릭터가 있으면 마케팅 확인 refs와
# 같은 기준, ax_agent_template.plan_rules). 소품 개수는 근거가 없어 보지 않는다(배경 소품은 AI가 잘
# 그린다). PROPS_MAX·env·LLM props 칸은 남긴다(소품 그리기에 쓸 수 있다)
# 기준은 env(PLANNING_PLAN_*), 없으면 기본 상수
PEOPLE_MAX_ENV, ACTIONS_MAX_ENV, PROPS_MAX_ENV = (
    "PLANNING_PLAN_PEOPLE_MAX", "PLANNING_PLAN_ACTIONS_MAX", "PLANNING_PLAN_PROPS_MAX",
)  # fmt: skip
PEOPLE_MAX, ACTIONS_MAX, PROPS_MAX = 2, 1, 2
PLACE_CHANGES_MAX_ENV, PLACE_CHANGES_MAX = "PLANNING_PLAN_PLACE_CHANGES_MAX", 2
# 문제 kind 안의 자세한 검사 이름 (계약 CheckProblem.code)
CLOSE_PERSON, PLACE_CHANGE = "close_person", "place_change"
CLOSE_PERSON_KO = "회사 캐릭터 말고 다른 인물이 가까이 나와요(참조 이미지는 회사 캐릭터 1명뿐)"
# 장면 글에서 읽는 인물 수 (낱말 → 수). "여럿·무리·군중·친구들"은 3명 이상으로 본다
PEOPLE_WORDS = (
    ("둘이서", 2), ("두 명", 2), ("두 사람", 2), ("셋이서", 3), ("세 명", 3), ("세 사람", 3),
    ("세 마리", 3), ("넷이서", 4), ("네 명", 4), ("네 마리", 4), ("다섯 명", 5), ("여러 명", 3),
    ("여럿", 3), ("무리", 3), ("군중", 3), ("친구들", 3), ("관객들", 3),
)  # fmt: skip
_PEOPLE_NUM = re.compile(r"(\d+)\s*(?:명|마리)")
ACTION_JOINS = (" then ", ", and ", " and then ", " while ")
# "~하고 ~" = 동작 2개. "하고 있다"·"하고 서 있다"(보조 용언)는 동작 하나라 세지 않는다
# (11/26 T 예진 님 리뷰 #1860: "책을 정리하고 있다")
_KO_JOIN = re.compile(r"[가-힣]+하고\s+(?!있|서(?:\s|$))[가-힣]+")


def _limit(env: str, default: int) -> int:
    raw = os.getenv(env, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def limits() -> tuple[int, int, int]:
    """(인물, 동작, 소품) 상한."""
    return (_limit(PEOPLE_MAX_ENV, PEOPLE_MAX), _limit(ACTIONS_MAX_ENV, ACTIONS_MAX),
            _limit(PROPS_MAX_ENV, PROPS_MAX))  # fmt: skip


# 11/26 H: 낱말 한가운데는 세지 않는다 (운영 wr_eabc06b19e 장면 4 "손을 흔들고 마무리"의 "무리"를
# 3명으로 셌다). 앞에 한글이 붙어 있으면 다른 낱말이다
_WORD_START = r"(?<![가-힣])"
# 글에 한 명이라고 분명히 적힌 말 (인물 수 칸보다 글을 믿는다). "한 명"·"1명"은 "친구 한 명과"처럼
# 둘을 뜻할 때가 있어 넣지 않는다
SOLO_WORDS = ("혼자", "홀로")


def _found(word: str, text: str) -> bool:
    return re.search(_WORD_START + re.escape(word), text) is not None


def people_in_text(text: str) -> int:
    """장면 글에서 읽은 인물 수 (못 읽으면 0). 낱말은 낱말 처음에서만 맞춘다."""
    text = text or ""
    found = [n for w, n in PEOPLE_WORDS if _found(w, text)]
    found += [int(m.group(1)) for m in _PEOPLE_NUM.finditer(text)]
    return max(found, default=0)


def solo_in_text(text: str) -> bool:
    """장면 글이 한 명이라고 말하고 여럿을 뜻하는 말은 없나 ("주인공 혼자 …")."""
    text = text or ""
    return any(_found(w, text) for w in SOLO_WORDS) and people_in_text(text) <= 1


def scene_people(said: int, text: str) -> int:
    """장면 인물 수: LLM 칸과 글 중 큰 값. 글이 한 명이라고 분명히 말하면 글을 믿는다."""
    if solo_in_text(text):
        return 1
    return max(said, people_in_text(text))


def action_count(action_en: str, visual: str) -> tuple[int, str]:
    """(동작 수 추정, 걸린 글). 이음말이 없으면 (1, "")."""
    low = f" {action_en or ''} ".lower()
    joins = sum(low.count(j) for j in ACTION_JOINS)
    if joins:
        return 1 + joins, " ".join((action_en or "").split())[:60]
    m = _KO_JOIN.search(visual or "")
    if m:
        return 2, m.group(0)
    if "동시에" in (visual or ""):
        return 2, "동시에"
    return 1, ""


def place_changes_max() -> int:
    """장소 전환 상한 (엔드카드를 뺀 장면에서 장소가 바뀐 횟수)."""
    return _limit(PLACE_CHANGES_MAX_ENV, PLACE_CHANGES_MAX)


def place_key(text: str) -> str:
    """장소 비교 값 (공백·문장부호 무시, 낱말 끝 조사 뗌, 소문자). "책상에서" = "책상"."""
    return "".join(_stem(w.lower()) for w in _WORD.findall(text or ""))


def _get(s: Any, k: str, default: Any = "") -> Any:
    return s.get(k, default) if isinstance(s, dict) else getattr(s, k, default)


def place_change(scenes: Sequence[Any]) -> dict[str, Any] | None:
    """장소 전환이 상한을 넘으면 문제 하나 (scene = 처음 넘긴 장면), 아니면 None.

    엔드카드 장면은 빼고 센다. 빈 place_ko는 앞 장면과 같은 장소로 본다."""
    most = place_changes_max()
    prev, changes, first = "", 0, None
    path: list[str] = []
    for n, s in enumerate(scenes, start=1):
        if is_end_card(str(_get(s, "visual") or "")):
            continue
        raw = " ".join(str(_get(s, "place_ko") or "").split())
        key = place_key(raw)
        if not key or key == prev:
            continue
        if prev:
            changes += 1
            if changes > most and first is None:
                first = n
        prev = key
        path.append(raw[:20])
    if first is None:
        return None
    detail = (f"장소가 {changes}번 바뀌어요: {' → '.join(path)} "
              f"(15초 한 번 생성이라 최대 {most}번)")  # fmt: skip
    return {"kind": "actions", "code": PLACE_CHANGE, "scene": first, "detail_ko": detail[:160]}


def feasibility(scenes: Sequence[Any], has_character: bool = False) -> list[dict[str, Any]]:
    """제작 검사 문제 {kind, scene, detail_ko, code?} (엔드카드 장면은 보지 않는다).

    scenes: LLM 장면(_SceneOut)처럼 visual·start_frame·end_frame·action_en·camera_en·people·
    place_ko를 가진 것. has_character: 회사 캐릭터가 있는 제작안이면 인원수 대신 "다른 인물이
    가까이 나오는지"(참조 없는 얼굴)를 본다. 없으면 인원수 상한(PEOPLE_MAX)만."""
    people_max, actions_max, _ = limits()
    out: list[dict[str, Any]] = []
    for n, s in enumerate(scenes, start=1):
        visual = str(_get(s, "visual") or "")
        if is_end_card(visual):
            continue
        text = " ".join(str(_get(s, k) or "") for k in ("visual", "start_frame", "end_frame"))
        said = _get(s, "people", 1)
        said = said if isinstance(said, int) and not isinstance(said, bool) else 1
        if has_character:  # #1911 기준 그대로 (LLM 칸과 글 중 큰 값)
            camera = f"{_get(s, 'camera_en') or ''} {visual}"
            people = max(said, people_in_text(text))
            if plan_rules.close_other_person(text, camera, people):
                out.append({"kind": "people", "code": CLOSE_PERSON, "scene": n,
                            "detail_ko": CLOSE_PERSON_KO})  # fmt: skip
        elif (people := scene_people(said, text)) > people_max:
            out.append({"kind": "people", "scene": n,
                        "detail_ko": f"인물 {people}명 (허용 {people_max}명)"})  # fmt: skip
        count, joined = action_count(str(_get(s, "action_en") or ""), visual)
        if count > actions_max:
            detail = f'동작 {count}개 "{joined}" (허용 {actions_max}개)'
            out.append({"kind": "actions", "scene": n, "detail_ko": detail})
    moved = place_change(scenes)
    return out + ([moved] if moved else [])


FIX_TAIL = {
    "people": "한 화면에 보이는 인물을 줄인다",
    "actions": "그 장면 동작을 하나로 줄이거나 장면을 나눈다",
    "props": "손으로 다루는 소품을 줄인다",
    CLOSE_PERSON: "다른 인물을 빼거나 멀리·뒤에 둔다",
    PLACE_CHANGE: "장소를 2곳 이하로 줄이거나 같은 장소 안에서 일어나게 한다",
}


def problem_fix(p: dict[str, Any]) -> str:
    """제작 검사 문제의 "고칠 점" 한 줄 (code가 있으면 code 문구)."""
    tail = FIX_TAIL.get(p.get("code") or "", FIX_TAIL.get(p["kind"], "고친다"))
    return f"제작 검사 장면 {p['scene']}: {p['detail_ko']} → {tail}"


def rule_problem(r: dict[str, Any]) -> dict[str, Any]:
    """못 지킨 의뢰 지시 → 검사 기록 문제 한 줄."""
    tail = "광고 금칙어라 넣지 않음" if blocked_quote(r) else "못 지킴"
    detail = f"의뢰 지시({KIND_KO[r['kind']]}) '{r['text_ko'][:80]}' {tail}"
    return {"kind": "request_rule", "scene": None, "detail_ko": detail}


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
