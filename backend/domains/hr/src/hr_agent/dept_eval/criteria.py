"""기준값 설정 파일(`criteria.toml`) 읽기. 값은 사람만 PR로 바꾼다.

코드를 고치지 않고 파일 값만 바꿔도 판정이 바뀐다. 읽은 값이 올바르지 않으면 조용히 기본값으로
돌아가지 않고 오류를 낸다(잘못된 기준으로 판정이 쌓이는 것보다 낫다).
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PATH = Path(__file__).with_name("criteria.toml")


class CriteriaError(ValueError):
    """기준값 파일이 올바르지 않다."""


@dataclass(frozen=True)
class Rule:
    """정상으로 볼 상한. 일한 시간(초)과 시도 횟수 둘 다 이하여야 정상이다."""

    normal_max_seconds: float
    normal_max_attempts: int
    # True면 총괄이 단계에 붙여 보낸 기준 시간(`promised_seconds`)을 쓰고, 기준 시간이 없는
    # 단계만 `normal_max_seconds`를 쓴다
    use_promised: bool = False
    # 긴 작업(비동기)에서 부서의 응답 사이가 이 시간(초)보다 길게 비면 "응답 끊김"으로 본다
    max_silence_seconds: float = 300.0


@dataclass(frozen=True)
class Criteria:
    version: int
    updated: str
    default: Rule
    groups: dict[str, Rule] = field(default_factory=dict)

    def rule_for(self, group: str | None) -> Rule:
        return self.groups.get(group or "", self.default)


def _rule(raw: object, base: Rule | None, where: str) -> Rule:
    if not isinstance(raw, dict):
        raise CriteriaError(f"{where}: 표가 아니다")
    seconds = raw.get("normal_max_seconds", base.normal_max_seconds if base else None)
    attempts = raw.get("normal_max_attempts", base.normal_max_attempts if base else None)
    if isinstance(seconds, bool) or not isinstance(seconds, int | float) or seconds <= 0:
        raise CriteriaError(f"{where}: normal_max_seconds는 0보다 큰 숫자여야 한다")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise CriteriaError(f"{where}: normal_max_attempts는 1 이상의 정수여야 한다")
    promised = raw.get("use_promised", base.use_promised if base else False)
    if not isinstance(promised, bool):
        raise CriteriaError(f"{where}: use_promised는 true 또는 false여야 한다")
    silence = raw.get("max_silence_seconds", base.max_silence_seconds if base else 300.0)
    if isinstance(silence, bool) or not isinstance(silence, int | float) or silence <= 0:
        raise CriteriaError(f"{where}: max_silence_seconds는 0보다 큰 숫자여야 한다")
    return Rule(float(seconds), attempts, promised, float(silence))


def parse_criteria(data: dict) -> Criteria:
    version = data.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise CriteriaError("version은 1 이상의 정수여야 한다")
    default = _rule(data.get("default"), None, "default")
    groups_raw = data.get("group") or {}
    if not isinstance(groups_raw, dict):
        raise CriteriaError("group은 표여야 한다")
    groups = {name: _rule(raw, default, f"group.{name}") for name, raw in groups_raw.items()}
    return Criteria(version, str(data.get("updated", "")), default, groups)


def load_criteria(path: Path | str | None = None) -> Criteria:
    target = Path(path) if path else DEFAULT_PATH
    try:
        with target.open("rb") as handle:
            return parse_criteria(tomllib.load(handle))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CriteriaError(f"기준값 파일을 읽지 못했다: {target}: {exc}") from exc
