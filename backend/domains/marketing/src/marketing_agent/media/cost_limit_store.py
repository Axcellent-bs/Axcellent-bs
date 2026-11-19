"""영상 비용 상한 설정 저장소(JSON 파일 + 변경 이력). 화면 값이 환경변수보다 우선한다.

- 파일: `MARKETING_COST_LIMITS_FILE`(없으면 `<MARKETING_MEDIA_DIR>/cost_limits.json`).
  내용: 영상 건당 상한 `video_per_item_krw`, 월 예산 `monthly_budget_krw`, 하향 셀프 승인 허용
  `lower_self_approve`(기본 꺼짐), 대기 중인 요청 `requests`(최근 10건, 화면 표시용).
- 이력: `<파일>.history.jsonl`에 바꿀 때마다 한 줄(언제·누가·무엇을·이전값·새값·사유).
- 읽기는 `video_store`·`company_connection`처럼 mtime 캐시(경로·mtime·크기)로 재시작 없이 반영한다.
  쓰는 곳은 마케팅 skill(`cost_limits.py`, execute() 관문 뒤) 한 곳이다. 진행 중 생성 건은
  이미 계산한 설정을 쓰므로 영향이 없다.
- 깨진 파일·범위 밖 값은 없는 것으로 본다(환경변수로 돌아간다).
- TODO: 변경 알림(Slack 등)은 보내지 않는다. 변경 이력과 승인 목록에만 남는다.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FILE_ENV = "MARKETING_COST_LIMITS_FILE"
MEDIA_DIR_ENV = "MARKETING_MEDIA_DIR"
FIELDS = ("video_per_item_krw", "monthly_budget_krw")
# 코드 상수. 화면 설정은 이 범위 안에서만 저장된다(정책 상한은 이보다 더 좁을 수 있다)
MIN_KRW = 1  # 0원 불가
ABSOLUTE_MAX_VIDEO_ITEM_KRW = 10_000  # safety `video_cost_single` 절대 상한과 같다(시험이 확인)
ABSOLUTE_MAX_MONTHLY_KRW = 5_000_000
MAX_REQUESTS = 10
_LOCK = threading.Lock()
_cache: tuple[tuple[str, int, int], dict[str, Any]] | None = None


class CostLimitStoreError(Exception):
    """저장소 쓰기 실패."""


def absolute_max(field: str) -> int:
    return (
        ABSOLUTE_MAX_VIDEO_ITEM_KRW if field == "video_per_item_krw" else ABSOLUTE_MAX_MONTHLY_KRW
    )


def valid_amount(field: str, value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and MIN_KRW <= value <= absolute_max(field)
    )


def store_path() -> Path:
    explicit = os.getenv(FILE_ENV, "").strip()
    if explicit:
        return Path(explicit)
    base = os.getenv(MEDIA_DIR_ENV, "").strip() or str(
        Path(tempfile.gettempdir()) / "marketing_media"
    )
    return Path(base) / "cost_limits.json"


def _history_path() -> Path:
    path = store_path()
    return path.with_name(path.name + ".history.jsonl")


def _read() -> dict[str, Any]:
    global _cache
    path = store_path()
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if _cache is not None and _cache[0] == key:
        return _cache[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {}
    data = raw if isinstance(raw, dict) else {}
    _cache = (key, data)
    return data


def _write(data: dict[str, Any]) -> None:
    path = store_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        raise CostLimitStoreError("저장소에 쓸 수 없다") from None


def _log(lines: list[dict[str, Any]]) -> None:
    try:
        with _history_path().open("a", encoding="utf-8") as handle:
            for line in lines:
                handle.write(json.dumps(line, ensure_ascii=False) + "\n")
    except OSError:
        raise CostLimitStoreError("변경 이력을 쓸 수 없다") from None


def _entry(field: str) -> dict[str, Any] | None:
    row = _read().get(field)
    if isinstance(row, dict) and valid_amount(field, row.get("value")):
        return row
    return None


def value(field: str) -> int | None:
    """화면에서 정한 값. 없으면 None(환경변수를 쓴다)."""
    row = _entry(field)
    return row["value"] if row else None


def video_per_item() -> int | None:
    return value("video_per_item_krw")


def monthly_budget() -> int | None:
    return value("monthly_budget_krw")


def lower_self_approve() -> bool:
    row = _read().get("lower_self_approve")
    return bool(isinstance(row, dict) and row.get("value") is True)


def meta() -> dict[str, str | None]:
    """마지막으로 값을 바꾼 사람과 시각."""
    rows = [r for r in (_entry(f) for f in FIELDS) if r]
    last = max(rows, key=lambda r: str(r.get("updated_at") or ""), default=None)
    return {
        "updated_by": str(last.get("updated_by") or "") or None if last else None,
        "updated_at": str(last.get("updated_at") or "") or None if last else None,
    }


def option_meta() -> dict[str, str | None]:
    row = _read().get("lower_self_approve")
    row = row if isinstance(row, dict) else {}
    return {
        "updated_by": str(row.get("updated_by") or "") or None,
        "updated_at": str(row.get("updated_at") or "") or None,
    }


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def set_values(changes: dict[str, int], who: str, action: str, reason: str) -> None:
    """상한 값을 바꾸고 필드마다 이력을 남긴다. action: lower | raise."""
    with _LOCK:
        data = dict(_read())
        stamp = _now()
        lines = []
        for field, new in changes.items():
            if field not in FIELDS or not valid_amount(field, new):
                raise CostLimitStoreError("범위 밖 값이다")
            old = value(field)
            data[field] = {"value": new, "updated_by": who[:64], "updated_at": stamp}
            lines.append(
                {
                    "at": stamp,
                    "by": who[:64],
                    "action": action,
                    "field": field,
                    "old": old,
                    "new": new,
                    "reason": reason[:200],
                }
            )
        data["version"] = 1
        _log(lines)  # 이력을 먼저 쓴다
        _write(data)


def set_option(enabled: bool, who: str, reason: str) -> None:
    with _LOCK:
        data = dict(_read())
        old = lower_self_approve()
        stamp = _now()
        data["lower_self_approve"] = {"value": enabled, "updated_by": who[:64], "updated_at": stamp}
        data["version"] = 1
        _log(
            [
                {
                    "at": stamp,
                    "by": who[:64],
                    "action": "option",
                    "field": "lower_self_approve",
                    "old": int(old),
                    "new": int(enabled),
                    "reason": reason[:200],
                }
            ]
        )
        _write(data)


def requests() -> list[dict[str, Any]]:
    rows = _read().get("requests")
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def add_request(row: dict[str, Any]) -> None:
    """승인 대기 요청을 적는다(화면 표시용). 같은 보류 건이면 덮는다."""
    with _LOCK:
        data = dict(_read())
        rows = [r for r in requests() if r.get("id") != row.get("id")]
        rows.append(row)
        data["requests"] = rows[-MAX_REQUESTS:]
        data["version"] = 1
        _write(data)


def drop_requests(payload: dict[str, int]) -> None:
    """적용이 끝난 요청(같은 내용)을 목록에서 뺀다."""
    with _LOCK:
        data = dict(_read())
        kept = [r for r in requests() if r.get("payload") != payload]
        if len(kept) == len(requests()):
            return
        data["requests"] = kept
        _write(data)


def history(limit: int = 15) -> list[dict[str, Any]]:
    try:
        lines = _history_path().read_text(encoding="utf-8").splitlines()[-limit:]
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
