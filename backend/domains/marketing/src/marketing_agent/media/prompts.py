"""프롬프트 만들기와 코드 검사. 자유 프롬프트는 받지 않는다.

프롬프트 = `scene_templates.toml`의 고정 문장 + 화이트리스트 필드 치환
(+ 영상 템플릿은 기획안의 구조화 필드를 정리해 끼우는 `plan_*` 선택 필드)
(+ 회사 브랜드 자리 `{brand_character}`·`{brand_setting}`: 요청 회사 tenant.toml `[brand]`의
character_en·setting_en. 공용 `tenant.brand()`가 금칙·길이 검사를 하고, 없으면 중립 기본값).
필드 값에 개인정보·고객 원문 말투·글자 요청·실존 인물·브랜드가 있으면 거절한다.
검사는 정규식과 금칙어 목록이고 LLM 판단에 맡기지 않는다.
거절 결과에는 이유 코드와 필드 이름만 담고 입력 값은 되돌려주지 않는다(개인정보 재노출 방지).
"""

from __future__ import annotations

import hashlib
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ax_agent_template import tenant
from marketing_agent.media.base import MediaKind

TEMPLATES_PATH = Path(__file__).with_name("scene_templates.toml")
_FREE_WORD = re.compile(r"[A-Za-z0-9가-힣 ]+")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")
PLAN_SLOT = "plan_clause"  # 기획 데이터 자리. 템플릿 fields에는 없고 선택이다
# 회사 브랜드 자리. 템플릿 fields에는 없고 요청 회사 설정에서 채운다(호출자가 넘기지 않는다)
BRAND_SLOTS = {"brand_character": "character_en", "brand_setting": "setting_en"}
_PLAN_STRIP = re.compile(r"[^A-Za-z0-9가-힣 ]+")
# 영상 모델 프롬프트 길이 상한 (가장 짧은 공급자: runway gen4.5 promptText 1000자).
# 넘으면 plan_scene_4 → 3 → 2 → 1 순서로 뺀다(render_prompt_detailed, 순서는 테스트로 고정)
VIDEO_PROMPT_MAX = 1000
PLAN_SCENE_FIELDS = ("plan_scene_1", "plan_scene_2", "plan_scene_3", "plan_scene_4")


class PromptRejected(Exception):
    """프롬프트를 만들 수 없다. `code`는 닫힌 집합, `field`는 걸린 필드 이름(값은 담지 않는다)."""

    def __init__(self, code: str, field: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.field = field


@dataclass(frozen=True)
class SceneTemplate:
    template_id: str
    kind: MediaKind
    resolution: str
    quality: str | None
    duration_s: int | None
    fields: tuple[str, ...]
    sentence: str
    plan_clause: bool = False  # 문장에 `{plan_clause}` 자리가 있다(기획 데이터를 받을 수 있다)


@dataclass(frozen=True)
class _PlanConfig:
    intro: str
    tone_map: dict[str, str]
    max_len: dict[str, int]
    phrase: dict[str, str]  # 필드 이름 -> 문구(순서가 문장 순서)
    ad_words: tuple[str, ...]
    injection_words: tuple[str, ...]


@dataclass(frozen=True)
class _Rules:
    text_request: tuple[str, ...]
    person: tuple[str, ...]
    brand: tuple[str, ...]
    pii: tuple[re.Pattern[str], ...]


@dataclass(frozen=True)
class _Config:
    templates: dict[str, SceneTemplate]
    field_values: dict[str, tuple[str, ...] | None]  # None이면 짧은 단어
    field_max_len: dict[str, int]
    rules: _Rules
    plan: _PlanConfig


@lru_cache(maxsize=4)
def _load(path: Path) -> _Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    rules = raw["rules"]
    fields = raw.get("field", {})
    templates: dict[str, SceneTemplate] = {}
    for template_id, item in raw.get("template", {}).items():
        sentence = item["sentence"]
        found = set(_PLACEHOLDER.findall(sentence))
        used = found - {PLAN_SLOT} - set(BRAND_SLOTS)
        if used != set(item["fields"]) or not used <= set(fields):
            raise ValueError(f"템플릿 {template_id}의 자리표시자와 fields가 다르다")
        templates[template_id] = SceneTemplate(
            template_id=template_id,
            kind=item["kind"],
            resolution=item["resolution"],
            quality=item.get("quality"),
            duration_s=item.get("duration_s"),
            fields=tuple(item["fields"]),
            sentence=sentence,
            plan_clause=PLAN_SLOT in found,
        )
    plan_raw = raw.get("plan", {})
    plan_fields = plan_raw.get("field", {})
    plan_rules = raw.get("plan_rules", {})
    plan = _PlanConfig(
        intro=plan_raw.get("intro", ""),
        tone_map=dict(plan_raw.get("tone_map", {})),
        max_len={k: int(v.get("max_len", 0)) for k, v in plan_fields.items()},
        phrase={k: v["phrase"] for k, v in plan_fields.items()},
        ad_words=tuple(w.lower() for w in plan_rules.get("ad_words", [])),
        injection_words=tuple(w.lower() for w in plan_rules.get("injection_words", [])),
    )
    return _Config(
        templates=templates,
        field_values={k: tuple(v["values"]) if "values" in v else None for k, v in fields.items()},
        field_max_len={k: int(v.get("max_len", 0)) for k, v in fields.items()},
        rules=_Rules(
            text_request=tuple(w.lower() for w in rules["text_request_words"]),
            person=tuple(w.lower() for w in rules["person_words"]),
            brand=tuple(w.lower() for w in rules["brand_words"]),
            pii=tuple(re.compile(p) for p in rules["pii_patterns"]),
        ),
        plan=plan,
    )


def get_template(template_id: str, path: Path | None = None) -> SceneTemplate:
    template = _load(path or TEMPLATES_PATH).templates.get(template_id)
    if template is None:
        raise PromptRejected("unknown_template")
    return template


def _contains(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    for word in words:
        if word.isascii():  # 영문은 단어 경계로 ("context"가 "text"에 걸리지 않게)
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
                return True
        elif word in lowered:
            return True
    return False


def _check_value(config: _Config, name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromptRejected("field_value_invalid", name)
    value = value.strip()
    rules = config.rules
    if any(p.search(value) for p in rules.pii):
        raise PromptRejected("pii", name)
    if _contains(value, rules.text_request):
        raise PromptRejected("text_request", name)
    if _contains(value, rules.person) or _contains(value, rules.brand):
        raise PromptRejected("real_person_or_brand", name)
    allowed = config.field_values[name]
    if allowed is not None:
        if value not in allowed:
            raise PromptRejected("value_not_allowed", name)
    elif len(value) > config.field_max_len[name] or not _FREE_WORD.fullmatch(value):
        raise PromptRejected("free_text", name)
    return value


@dataclass(frozen=True)
class RenderedPrompt:
    """프롬프트와 기획 데이터 반영 기록. 기록에는 이름·길이·해시만 담는다(값 전체 없음)."""

    prompt: str
    plan_used: tuple[dict, ...] = ()
    plan_skipped: tuple[dict, ...] = ()

    def plan_record(self) -> dict | None:
        """`meta.json`·장부에 남길 요약. 기획 필드를 하나도 받지 않았으면 None(옛 기록과 같다)."""
        if not self.plan_used and not self.plan_skipped:
            return None
        record: dict = {
            "fields_used": [u["field"] for u in self.plan_used],
            "used": [{k: v for k, v in u.items() if k != "value"} for u in self.plan_used],
            "skipped": list(self.plan_skipped),
        }
        trends = next((u["value"] for u in self.plan_used if u["field"] == "plan_trends"), None)
        if trends:  # 어떤 유행 이름을 따랐는지 보이게(데이터분석이 준 이름뿐, 다른 사실은 없다)
            record["trend_names"] = trends
        return record


def plan_field_names(path: Path | None = None) -> tuple[str, ...]:
    """기획 데이터로 받을 수 있는 필드 이름(문장 순서)."""
    return tuple(_load(path or TEMPLATES_PATH).plan.phrase)


def _clean_plan_value(config: _Config, name: str, raw: object) -> tuple[str | None, str | None]:
    """(정리된 값, 뺀 이유). 걸린 값은 돌려주지 않고 이유 코드만 돌려준다."""
    if not isinstance(raw, str) or not raw.strip():
        return None, "empty"
    plan, rules = config.plan, config.rules
    text = unicodedata.normalize("NFKC", raw)
    if any(p.search(text) for p in rules.pii):  # 주소·전화·이메일·고객 말투는 정리 전에 본다
        return None, "pii"
    if name == "plan_tone":  # 고른 방향 코드 -> 고정 문구. 글은 받지 않는다
        mapped = plan.tone_map.get(text.strip())
        return (mapped, None) if mapped else (None, "unknown_value")
    words = [w for w in _PLAN_STRIP.sub(" ", text).split() if not any(c.isdigit() for c in w)]
    value = " ".join(words)
    if not value:
        return None, "empty"
    limit = plan.max_len.get(name, 0)
    if limit and len(value) > limit:  # 길이 초과는 단어 경계에서 자른다
        value = value[:limit].rsplit(" ", 1)[0] or value[:limit]
    if _contains(value, rules.text_request):
        return None, "text_request"
    if _contains(value, rules.person) or _contains(value, rules.brand):
        return None, "real_person_or_brand"
    if _contains(value, plan.ad_words):
        return None, "ad_claim"
    if _contains(value, plan.injection_words):
        return None, "injection"
    return value, None


def _plan_clause(
    config: _Config, plan_fields: dict[str, object]
) -> tuple[str, list[dict], list[dict]]:
    used: list[dict] = []
    skipped: list[dict] = []
    parts: list[str] = []
    for name, phrase in config.plan.phrase.items():  # 문장 순서는 설정이 정한다(입력 순서 무관)
        if name not in plan_fields:
            continue
        value, reason = _clean_plan_value(config, name, plan_fields[name])
        if value is None:
            skipped.append({"field": name, "reason": reason})
            continue
        parts.append(phrase.replace("{value}", value))
        entry = {
            "field": name,
            "len": len(value),
            "sha256": hashlib.sha256(value.encode()).hexdigest()[:16],
        }
        if name == "plan_trends":
            entry["value"] = value
        used.append(entry)
    if not parts:
        return "", used, skipped
    return config.plan.intro + "".join(parts).rstrip(";") + ".", used, skipped


def render_prompt_detailed(
    template_id: str, fields: dict[str, object], path: Path | None = None
) -> RenderedPrompt:
    """검사를 모두 통과한 프롬프트 + 기획 데이터 반영 기록. 걸리면 `PromptRejected`.

    `plan_*` 필드는 `{plan_clause}` 자리가 있는 템플릿에서만 받는다. 값은 정리·검사를 거치고,
    걸린 필드는 거절하지 않고 뺀다(기획 글 때문에 생성이 막히지 않게). 비면 기존 문장 그대로다.
    """
    config = _load(path or TEMPLATES_PATH)
    template = get_template(template_id, path)
    plan_names = set(config.plan.phrase)
    plan_in: dict[str, object] = {}
    for name in fields:
        if name in template.fields:
            continue
        if template.plan_clause and name in plan_names:
            plan_in[name] = fields[name]
            continue
        raise PromptRejected("field_not_allowed", name)
    for name in template.fields:
        if name not in fields:
            raise PromptRejected("field_missing", name)
    values = {name: _check_value(config, name, fields[name]) for name in template.fields}
    if any(f"{{{slot}}}" in template.sentence for slot in BRAND_SLOTS):
        brand = tenant.brand()
        values.update({slot: brand[key] for slot, key in BRAND_SLOTS.items()})
    dropped: list[str] = []
    while True:
        kept = {k: v for k, v in plan_in.items() if k not in dropped}
        clause, used, skipped = _plan_clause(config, kept) if template.plan_clause else ("", [], [])
        values[PLAN_SLOT] = clause
        prompt = _PLACEHOLDER.sub(lambda m: values[m.group(1)], template.sentence)
        if template.kind != "video" or len(prompt) <= VIDEO_PROMPT_MAX:
            break
        # 길이 상한: 뒤 장면부터 하나씩 뺀다
        # (장면 밖 필드는 빼지 않는다. 그래도 넘으면 공급자가 자른다)
        names = {u["field"] for u in used}
        last = next((n for n in reversed(PLAN_SCENE_FIELDS) if n in names), None)
        if last is None:
            break
        dropped.append(last)
    skipped = skipped + [{"field": n, "reason": "too_long"} for n in dropped]
    return RenderedPrompt(prompt, tuple(used), tuple(skipped))


def render_prompt(template_id: str, fields: dict[str, object], path: Path | None = None) -> str:
    """검사를 모두 통과한 프롬프트 문장. 걸리면 `PromptRejected`."""
    return render_prompt_detailed(template_id, fields, path).prompt


def check_field_value(name: str, value: object, path: Path | None = None) -> str:
    """필드 값 하나를 `render_prompt`와 같은 규칙으로 검사한다.

    모르는 필드·걸린 값은 `PromptRejected`.
    """
    config = _load(path or TEMPLATES_PATH)
    if name not in config.field_values:
        raise PromptRejected("field_not_allowed", name)
    return _check_value(config, name, value)


def list_templates(path: Path | None = None) -> tuple[SceneTemplate, ...]:
    """장면 템플릿 전부(파일 순서). 종류별 후보를 찾을 때 쓴다."""
    return tuple(_load(path or TEMPLATES_PATH).templates.values())


def contains_pii(text: str, path: Path | None = None) -> bool:
    """글에 개인정보·식별자 패턴(전화·이메일·주문번호·주소 등)이 있는지. 값은 돌려주지 않는다."""
    return any(p.search(text) for p in _load(path or TEMPLATES_PATH).rules.pii)


def field_choices(name: str, path: Path | None = None) -> tuple[tuple[str, ...] | None, int]:
    """필드의 (고를 수 있는 값 목록 | None=짧은 단어, 짧은 단어 최대 길이). 화면 선택지에 쓴다."""
    config = _load(path or TEMPLATES_PATH)
    if name not in config.field_values:
        raise PromptRejected("field_not_allowed", name)
    return config.field_values[name], config.field_max_len[name]
