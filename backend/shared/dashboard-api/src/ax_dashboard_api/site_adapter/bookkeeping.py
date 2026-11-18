"""고객지원 접수 동기화 기록: 사이트가 아니라 AXcellent 쪽 장부라 변환기 폴더에 한 줄씩 남긴다.

`<AX_SITE_ADAPTER_DIR>/bookkeeping.jsonl`.
본문 값은 남기지 않고 항목 이름만 남긴다(개인정보·문의 내용 제외).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ax_dashboard_api.site_adapter.mapping import adapter_dir

FILE = "bookkeeping.jsonl"


def record(kind: str, key: str, action: str, body: dict[str, Any]) -> None:
    folder = adapter_dir()
    folder.mkdir(parents=True, exist_ok=True)
    line = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "kind": kind,
        "key": key,
        "action": action,
        "fields": sorted(body),
    }
    with (folder / FILE).open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
