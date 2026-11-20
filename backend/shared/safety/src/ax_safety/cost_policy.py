"""비용 관련 안전 임계값의 화면 변경 저장소(JSON 파일 + 변경 이력).

`thresholds.toml`은 기본값이고 사람만 고친다. 이 모듈은 그 위에
"승인 관문을 통과한 사람 변경"만 덮어쓴다.

- 덮어쓸 수 있는 규칙은 `EDITABLE`의 1개(영상 길이)뿐이다. 원화 비용 규칙은 없앴다(사용량 예산으로
  대체). 각 규칙에 코드 상수 하한·절대 상한이 있고,
  범위 밖 값은 쓰기도 거부하고 읽을 때도 무시한다(손으로 고친 파일도 기본값으로: fail-closed).
- 파일: `SAFETY_COST_POLICY_FILE`(없으면 `SAFETY_STATE_DB` 옆 `cost_policy.json`,
  둘 다 없으면 덮어쓰기 없음 = 기본값만, 변경 요청은 거절). 이력은 `<파일>.history.jsonl`.
  `SAFETY_COST_POLICY_FILE`을 정하지 않았고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`, 설계 1-4)
  `<회사 폴더>/catbooks/cost_policy.json`. `SAFETY_STATE_DB` 옆 옛 파일은 처음 읽을 때
  이력과 같이 옮긴다.
- 쓰는 곳은 안전 서버의 적용 경로(`apply_change`) 한 곳이다. 값은 승인된 보류 건의 요청
  기록에서만 온다. 마케팅·대시보드 코드는 이 파일을 쓰지 않는다(읽기 API만 있다).
- 안전 서버는 규칙을 볼 때마다 `effective()`로 파일의 mtime을 확인해 재시작 없이 다시 읽는다.
  (하루 누적은 상태 DB에서 온다. 상태 DB가 없는 서버는 재시작하면 일일 누적이 0으로
  돌아가는 기존 한계가 있다)
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_agent_template import company_files
from ax_safety.rules import ThresholdTable

FILE_ENV = "SAFETY_COST_POLICY_FILE"
ACTION = "change_cost_policy"
_LOCK = threading.Lock()


@dataclass(frozen=True)
class Editable:
    key: str
    label: str
    scope: str  # video_duration
    unit: str  # s
    minimum: int
    absolute_max: int  # 코드 절대 상한. 사람 승인으로도 이 값을 넘길 수 없다(코드를 바꿔야 한다)


# 절대 상한은 기본값(thresholds.toml)보다 크거나 같아야 한다(시험이 확인한다).
# 영상 길이는 숏폼 상한 90초(마케팅 ABSOLUTE_MAX_DURATION_S)라 낮추기만 된다
EDITABLE: dict[str, Editable] = {
    e.key: e for e in (Editable("video_duration", "영상 길이", "video_duration", "s", 1, 90),)
}


class PolicyError(Exception):
    """변경을 받을 수 없다. 메시지는 사유 코드다."""


POLICY_NAME = "cost_policy.json"


def policy_path() -> Path | None:
    explicit = os.getenv(FILE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    state = os.getenv("SAFETY_STATE_DB", "").strip()
    old = Path(state).with_name(POLICY_NAME) if state else None
    if company_files.folders_on():
        return company_files.company_file(POLICY_NAME, source=old, extras=(".history.jsonl",))
    return old


def in_range(key: str, value: object) -> bool:
    spec = EDITABLE.get(key)
    return (
        spec is not None
        and isinstance(value, int)
        and not isinstance(value, bool)
        and spec.minimum <= value <= spec.absolute_max
    )


_cache: tuple[tuple[str, int, int], dict[str, dict[str, Any]]] | None = None


def _read_values() -> dict[str, dict[str, Any]]:
    """파일의 덮어쓰기 값. 범위 밖·모르는 규칙은 버린다. 파일이 없거나 깨졌으면 비어 있다."""
    global _cache
    path = policy_path()
    if path is None:
        return {}
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        values = raw.get("values") if isinstance(raw, dict) else None
    except (OSError, ValueError):
        values = None
    found: dict[str, dict[str, Any]] = {}
    for rule_key, row in (values if isinstance(values, dict) else {}).items():
        if isinstance(row, dict) and in_range(rule_key, row.get("value")):
            found[rule_key] = row
    _cache = (key, found)
    return found


def effective(base: ThresholdTable) -> ThresholdTable:
    """기본 기준표에 파일 값을 덮은 기준표. 덮을 값이 없으면 기본 기준표 그대로."""
    values = _read_values()
    if not values:
        return base
    rules = [
        r.model_copy(update={"value": values[r.key]["value"]}) if r.key in values else r
        for r in base.rules
    ]
    return base.model_copy(update={"rules": rules})


def _rule(table: ThresholdTable, key: str) -> Any:
    return next((r for r in table.rules if r.key == key), None)


def describe(base: ThresholdTable) -> dict[str, Any]:
    """읽기 전용 현황: 규칙마다 현재 값·기본값·하한·절대 상한·출처."""
    current = effective(base)
    values = _read_values()
    items = []
    for spec in EDITABLE.values():
        default, now = _rule(base, spec.key), _rule(current, spec.key)
        if default is None or now is None:
            continue
        row = values.get(spec.key, {})
        items.append(
            {
                "key": spec.key,
                "label": spec.label,
                "scope": spec.scope,
                "unit": spec.unit,
                "op": default.op,
                "default": default.value,
                "value": now.value,
                "min": spec.minimum,
                "absolute_max": spec.absolute_max,
                "source": "file" if spec.key in values else "default",
                "updated_by": row.get("updated_by"),
                "updated_at": row.get("updated_at"),
            }
        )
    return {
        "editable": policy_path() is not None,
        "items": items,
        "history": history(),
    }


def check_request(base: ThresholdTable, key: str, value: object) -> None:
    """요청 단계 검사(보류 건을 만들기 전). 문제가 있으면 PolicyError(사유 코드)."""
    if policy_path() is None:
        raise PolicyError("policy_file_not_configured")
    if key not in EDITABLE or _rule(base, key) is None:
        raise PolicyError("unknown_policy_rule")
    if not in_range(key, value):
        raise PolicyError("value_out_of_range")
    if _rule(effective(base), key).value == value:
        raise PolicyError("no_change")


def _history_path(path: Path) -> Path:
    return path.with_name(path.name + ".history.jsonl")


def history(limit: int = 20) -> list[dict[str, Any]]:
    path = policy_path()
    if path is None:
        return []
    try:
        lines = _history_path(path).read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out[::-1]


def applied_review_ids() -> set[str]:
    path = policy_path()
    if path is None:
        return set()
    try:
        lines = _history_path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    ids: set[str] = set()
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and isinstance(item.get("review_id"), str):
            ids.add(item["review_id"])
    return ids


def apply_change(
    base: ThresholdTable,
    *,
    key: str,
    value: int,
    requested_by: str,
    approved_by: str,
    review_id: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """승인된 변경 하나를 파일에 쓴다. 같은 보류 건은 두 번 적용하지 않는다(PolicyError)."""
    path = policy_path()
    if path is None:
        raise PolicyError("policy_file_not_configured")
    if key not in EDITABLE or _rule(base, key) is None:
        raise PolicyError("unknown_policy_rule")
    if not in_range(key, value):
        raise PolicyError("value_out_of_range")
    stamp = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    with _LOCK:
        if review_id in applied_review_ids():
            raise PolicyError("already_applied")
        old = _rule(effective(base), key).value
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        except (OSError, ValueError):
            data = {}
        values = data.get("values") if isinstance(data.get("values"), dict) else {}
        values[key] = {
            "value": value,
            "updated_by": requested_by[:64],
            "approved_by": approved_by[:64],
            "updated_at": stamp,
            "review_id": review_id,
        }
        data.update(version=1, values=values)
        line = {
            "at": stamp,
            "key": key,
            "old": old,
            "new": value,
            "requested_by": requested_by[:64],
            "approved_by": approved_by[:64],
            "review_id": review_id,
            "reason": reason[:200],
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # 이력을 먼저 쓴다(값만 바뀌고 이력이 없는 쪽보다 안전하다)
            with _history_path(path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            raise PolicyError("policy_file_write_failed") from None
    return line
