"""번호 짝 장부 (`link:<자원>.<칸>`): shop-api 번호(문의 `seq` 등) ↔ 고객사 id.

설계 "짝 파일 쓰기 항목 모양"의 `link:`. 읽기 변환(① 문의·신청)이 shop-api 번호를 줄 때 같은
자리에 고객사 id를 적어 두고(`remember`), 직접 쓰기의 본문 식이 그것을 찾는다(`find`).
없으면 직접 쓰지 않고 할 일함으로 간다(지어낸 id로 남의 문의에 쓰지 않게).

`<변환기 폴더>/links.json` = `{"<자원>": {"<shop-api 번호>": "<고객사 id>"}}`.
id만 남긴다(문의 글·이름 같은 본문 값은 남기지 않는다).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from ax_dashboard_api.site_adapter.mapping import adapter_dir

FILE = "links.json"
_lock = threading.Lock()


def _read(folder: Path) -> dict[str, dict[str, str]]:
    try:
        raw = json.loads((folder / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(res): {str(k): str(v) for k, v in rows.items() if isinstance(v, (str, int))}
        for res, rows in raw.items()
        if isinstance(rows, dict)
    }


def find(resource: str, shop_key: str) -> str | None:
    """shop-api 번호의 고객사 id. 짝이 없으면 None."""
    return _read(adapter_dir()).get(resource, {}).get(shop_key)


def _save(folder: Path, book: dict[str, dict[str, str]]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / (FILE + ".tmp")
    tmp.write_text(json.dumps(book, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, folder / FILE)


def remember(resource: str, shop_key: str, site_id: str) -> None:
    """번호 짝 하나를 적는다(같은 번호면 고쳐 쓴다)."""
    folder = adapter_dir()
    with _lock:
        book = _read(folder)
        if book.get(resource, {}).get(shop_key) == site_id:
            return  # 같은 짝이면 다시 쓰지 않는다 (받은함을 읽을 때마다 부른다)
        book.setdefault(resource, {})[shop_key] = site_id
        _save(folder, book)


def numbers(resource: str, site_ids: list[str]) -> dict[str, int]:
    """고객사 id마다 shop-api 정수 번호(`seq`). 처음 보는 id는 다음 번호를 새로 적는다.

    shop-api 받은함 번호는 정수다(고객지원이 `int(seq)`로 읽는다). 고객사 id(누비투어 cuid)는
    정수가 아니라, 처음 본 순서대로 1부터 번호를 매기고 같은 id는 늘 같은 번호를 준다.
    """
    folder = adapter_dir()
    with _lock:
        book = _read(folder)
        rows = book.setdefault(resource, {})
        known = {site: int(seq) for seq, site in rows.items() if seq.isdigit()}
        last = max([int(seq) for seq in rows if seq.isdigit()], default=0)
        changed = False
        for site_id in site_ids:
            if site_id in known:
                continue
            last += 1
            rows[str(last)] = site_id
            known[site_id] = last
            changed = True
        if changed:
            _save(folder, book)
    return {site_id: known[site_id] for site_id in site_ids}
