"""프롬프트 만들기와 코드 검사. 자유 프롬프트는 받지 않는다.

프롬프트 = `scene_templates.toml`의 고정 문장 + 화이트리스트 필드 치환.
필드 값에 개인정보·고객 원문 말투·글자 요청·실존 인물·브랜드가 있으면 거절한다.
검사는 정규식과 금칙어 목록이고 LLM 판단에 맡기지 않는다.
거절 결과에는 이유 코드와 필드 이름만 담고 입력 값은 되돌려주지 않는다(개인정보 재노출 방지).
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from marketing_agent.media.base import MediaKind

TEMPLATES_PATH = Path(__file__).with_name("scene_templates.toml")
_FREE_WORD = re.compile(r"[A-Za-z0-9가-힣 ]+")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


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


@lru_cache(maxsize=4)
def _load(path: Path) -> _Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    rules = raw["rules"]
    fields = raw.get("field", {})
    templates: dict[str, SceneTemplate] = {}
    for template_id, item in raw.get("template", {}).items():
        sentence = item["sentence"]
        used = set(_PLACEHOLDER.findall(sentence))
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


def render_prompt(template_id: str, fields: dict[str, object], path: Path | None = None) -> str:
    """검사를 모두 통과한 프롬프트 문장. 걸리면 `PromptRejected`."""
    config = _load(path or TEMPLATES_PATH)
    template = get_template(template_id, path)
    for name in fields:
        if name not in template.fields:
            raise PromptRejected("field_not_allowed", name)
    for name in template.fields:
        if name not in fields:
            raise PromptRejected("field_missing", name)
    values = {name: _check_value(config, name, fields[name]) for name in template.fields}
    return _PLACEHOLDER.sub(lambda m: values[m.group(1)], template.sentence)


def check_field_value(name: str, value: object, path: Path | None = None) -> str:
    """필드 값 하나를 `render_prompt`와 같은 규칙으로 검사한다.

    모르는 필드·걸린 값은 `PromptRejected`.
    """
    config = _load(path or TEMPLATES_PATH)
    if name not in config.field_values:
        raise PromptRejected("field_not_allowed", name)
    return _check_value(config, name, value)


def field_choices(name: str, path: Path | None = None) -> tuple[tuple[str, ...] | None, int]:
    """필드의 (고를 수 있는 값 목록 | None=짧은 단어, 짧은 단어 최대 길이). 화면 선택지에 쓴다."""
    config = _load(path or TEMPLATES_PATH)
    if name not in config.field_values:
        raise PromptRejected("field_not_allowed", name)
    return config.field_values[name], config.field_max_len[name]
