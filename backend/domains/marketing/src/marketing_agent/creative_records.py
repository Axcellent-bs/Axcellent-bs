"""이미지 시트·스토리보드 제작 기록 (2026-11-20).

마케팅 화면에서 이미지 시트(`character_sheet_prompt`)를 만들면 기록 한 줄을 남기고,
같은 기록에 이어서 만든 스토리보드(`storyboard_prompt`)를 붙인다.
기록 페이지(마케팅 › 제작 기록)가 목록·상세로 읽는다.

- 저장소는 SQLite다. 환경변수 `MARKETING_CREATIVE_DB`가 가리키는 파일에 두면 서비스를 다시 시작해도
  남는다(run_all.sh가 `backend/.data/marketing_creative.db`로 준다). 없으면 메모리.
- 올린 원본 이미지는 저장하지 않는다. 화면이 보낸 작은 미리보기(긴 변 256px JPEG, 80KB 이하)만 둔다.
- 기록은 최근 `KEEP`개만 남긴다. 기록이 실패해도 시트·스토리보드 결과는 그대로 돌려준다(부가 기능).
- 11/23 회사별: `store()`는 요청의 회사(`ax_agent_template.company_scope.active()`, 서버가 skill
  요청마다 `meta.tenant_id`로 건다)의 저장소다. 요청 밖이면 지금처럼 캣북스. 다른 회사 기록은
  읽기·고치기 모두 없는 것과 같다.
- 11/23 회사 대표 캐릭터: 기록 한 줄의 `brand` 칸(JSON)에 표시를 단다(새 저장소 없음).
  회사마다 최대 `MAX_BRAND_CHARACTERS`개, 4개째를 표시하면 가장 오래된 표시를 뗀다. 마지막에 표시한
  것이 기본이다. 표시한 기록은 `KEEP` 정리에서 지우지 않는다. 권리 확인(누가·언제)을 같이 남긴다.
- 11/23 홍보 영상 의뢰: 대표 캐릭터 기록을 의뢰마다 새 기록으로 복사한다(`copy_for_request`, 번호는
  의뢰 context로 정해 같은 의뢰면 같은 기록). 대표 캐릭터 기록에 스토리보드·영상을 직접 붙이면 다음
  의뢰가 앞 의뢰의 결과를 덮어쓰기 때문이다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from ax_agent_template import company_scope as request_company
from ax_contracts.schemas.marketing_brand import MAX_BRAND_CHARACTERS, MAX_BRAND_NAME
from marketing_agent import company_scope

logger = logging.getLogger("marketing.creative_records")

DB_ENV = "MARKETING_CREATIVE_DB"
KEEP = 200
MAX_THUMB_BASE64 = 80_000
MAX_NOTE = 200
MAX_STORY = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_creative_records (
    record_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    note TEXT,
    story TEXT,
    shots INTEGER,
    thumbnail TEXT,
    sheet TEXT NOT NULL,
    storyboard TEXT,
    storyboard_at TEXT,
    render TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'catbooks'
)
"""
# 의뢰로 만든 영상(wr_…)의 유튜브 올리기 상태(2026-11-23). 제작 기록의 render.youtube와 같은 모양을
# 의뢰 영상 초안(post_draft_id)마다 회사 칸별로 둔다.
# 영상 파일은 SNS 초안의 AI 라벨 입힌 파일 그대로다
_REQUEST_YOUTUBE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_request_video_youtube (
    post_draft_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    youtube TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (post_draft_id, tenant_id)
)
"""

# 의뢰로 만든 영상의 인스타그램 올리기 상태(2026-11-23). 유튜브 표와 같은 모양이다
_REQUEST_INSTAGRAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_request_video_instagram (
    post_draft_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    instagram TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (post_draft_id, tenant_id)
)
"""

# 기획안에서 만든 제작 기록 번호(11/25). 같은 기획안을 두 번 눌러도 기록 하나
_BRIEF_RECORD_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_brief_creative (
    proposal_id TEXT NOT NULL,
    tenant_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    character_record_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (proposal_id, tenant_id)
)
"""


# 홍보 사이트에 올린(올리는 중인) 영상(11/26, promo_site). 사이트가 하나라 회사 전체에서 센다
_PROMO_SITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_promo_site (
    item_id TEXT NOT NULL PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


UPLOAD_KINDS = ("real_photo", "virtual_human", "illustration")


def upload_kind(sheet: dict[str, Any] | None) -> str:
    """시트 기록의 올린 이미지 종류(11/24). 옛 기록·모르는 값은 real_photo."""
    kind = (sheet or {}).get("kind")
    return kind if kind in UPLOAD_KINDS else "real_photo"


def references_of(sheet: dict[str, Any] | None) -> list[dict[str, Any]]:
    """시트 기록의 참조 이미지 목록(11/25). 옛 기록은 빈 목록 = 대표만."""
    refs = (sheet or {}).get("references")
    return [r for r in refs if isinstance(r, dict)] if isinstance(refs, list) else []


def clean_thumbnail(raw: Any) -> str | None:
    """JPEG base64 미리보기만 받는다. 크거나 깨졌으면 버린다(기록은 미리보기 없이 남긴다)."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_THUMB_BASE64:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if data.startswith(b"\xff\xd8") else None


class CreativeRecords:
    """`path`가 없으면 환경변수 `MARKETING_CREATIVE_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None, company_id: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self.company_id = company_scope.scope_id(company_id)  # 회사 칸(여러 회사 1-6)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)
            self._conn.execute(_REQUEST_YOUTUBE_SCHEMA)
            self._conn.execute(_REQUEST_INSTAGRAM_SCHEMA)
            self._conn.execute(_BRIEF_RECORD_SCHEMA)
            self._conn.execute(_PROMO_SITE_SCHEMA)
            info = self._conn.execute("PRAGMA table_info(marketing_creative_records)")
            have = {r["name"] for r in info}
            if "render" not in have:  # 2026-11-20 Higgsfield 그림·영상 칸
                self._conn.execute("ALTER TABLE marketing_creative_records ADD COLUMN render TEXT")
            if "brand" not in have:  # 2026-11-23 회사 대표 캐릭터 표시
                self._conn.execute("ALTER TABLE marketing_creative_records ADD COLUMN brand TEXT")
            if "script" not in have:  # 2026-11-23 영상 대본(시트 → 대본 → 스토리보드)
                self._conn.execute("ALTER TABLE marketing_creative_records ADD COLUMN script TEXT")
            company_scope.ensure_tenant_column(
                self._conn, "marketing_creative_records"
            )  # 옛 줄은 캣북스

    def add_sheet(
        self,
        sheet: dict[str, Any],
        note: str | None,
        thumbnail: str | None,
        record_id: str | None = None,
    ) -> str:
        """기록 한 줄을 남긴다. `record_id`를 주면 그 번호로(이미 있으면 그대로 둔다)."""
        record_id = record_id or f"CR-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO marketing_creative_records"
                " (record_id, created_at, note, thumbnail, sheet, tenant_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record_id,
                    now,
                    (note or "")[:MAX_NOTE] or None,
                    thumbnail,
                    json.dumps(sheet),
                    self.company_id,
                ),
            )
            self._conn.execute(  # 대표 캐릭터로 표시한 기록은 지우지 않는다
                "DELETE FROM marketing_creative_records WHERE tenant_id = ? AND brand IS NULL"
                " AND record_id NOT IN ("
                " SELECT record_id FROM marketing_creative_records WHERE tenant_id = ?"
                " ORDER BY created_at DESC LIMIT ?)",
                (self.company_id, self.company_id, KEEP),
            )
        return record_id

    def attach_storyboard(
        self, record_id: str, story: str, shots: int, storyboard: dict[str, Any]
    ) -> bool:
        """기록에 스토리보드를 붙인다(다시 만들면 바꾼다). 기록이 없으면 False."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE marketing_creative_records"
                " SET story = ?, shots = ?, storyboard = ?, storyboard_at = ?"
                " WHERE record_id = ? AND tenant_id = ?",
                (story[:MAX_STORY], shots, json.dumps(storyboard), now, record_id, self.company_id),
            )
        return cur.rowcount == 1

    def set_board_source(self, record_id: str, source: str) -> bool:
        """스토리보드 JSON의 `board_source`만 바꾼다(`storyboard_at` 그대로). 없으면 False."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT storyboard FROM marketing_creative_records"
                " WHERE record_id = ? AND tenant_id = ?",
                (record_id, self.company_id),
            ).fetchone()
            if row is None or not row["storyboard"]:
                return False
            board = json.loads(row["storyboard"])
            if board.get("board_source") == source:
                return True
            board["board_source"] = source
            self._conn.execute(
                "UPDATE marketing_creative_records SET storyboard = ?"
                " WHERE record_id = ? AND tenant_id = ?",
                (json.dumps(board), record_id, self.company_id),
            )
        return True

    def set_board_svgs(self, record_id: str, svgs: dict[int, str], tried_key: str) -> bool:
        """스토리보드 칸 `svg`만 채운다(칸 문장·시간·자막·`storyboard_at` 그대로).

        기록이나 스토리보드가 없으면 False.

        `tried_key`에 시도했다고 남겨 같은 의뢰를 다시 준비해도 LLM을 또 부르지 않게 한다.
        하나라도 채우면 `board_source`가 "layout"일 때만 "svg"로 바꾼다
        (Higgsfield 그림은 그대로)."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT storyboard FROM marketing_creative_records"
                " WHERE record_id = ? AND tenant_id = ?",
                (record_id, self.company_id),
            ).fetchone()
            if row is None or not row["storyboard"]:
                return False
            board = json.loads(row["storyboard"])
            for shot in board.get("shots") or []:
                svg = svgs.get(shot.get("no")) if isinstance(shot, dict) else None
                if svg:
                    shot["svg"] = svg
            if svgs and board.get("board_source", "layout") == "layout":
                board["board_source"] = "svg"
            board[tried_key] = True
            self._conn.execute(
                "UPDATE marketing_creative_records SET storyboard = ?"
                " WHERE record_id = ? AND tenant_id = ?",
                (json.dumps(board), record_id, self.company_id),
            )
        return True

    def attach_script(self, record_id: str, script: dict[str, Any], story: str | None) -> bool:
        """기록에 대본을 붙인다(다시 쓰거나 고치면 바꾼다). 줄거리를 주면 같이 남긴다.

        스토리보드는 그대로 둔다(대본을 고친 뒤 스토리보드를 다시 만들면 새 대본을 따른다).
        기록이 없으면 False."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE marketing_creative_records SET script = ?, story = COALESCE(?, story)"
                " WHERE record_id = ? AND tenant_id = ?",
                (
                    json.dumps(script, ensure_ascii=False),
                    story[:MAX_STORY] if story else None,
                    record_id,
                    self.company_id,
                ),
            )
        return cur.rowcount == 1

    # ------------------------------------------------ 홍보 사이트(11/26)
    def promo_rows(self) -> list[tuple[str, str, dict[str, Any]]]:
        """모든 회사의 (항목 번호, 회사, 상태). 올린 순서와 상관없다."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT item_id, tenant_id, state FROM marketing_promo_site ORDER BY item_id"
            ).fetchall()
        return [(r["item_id"], r["tenant_id"], json.loads(r["state"])) for r in rows]

    def promo_get(self, item_id: str) -> tuple[str, dict[str, Any]] | None:
        """(회사, 상태). 없으면 None."""
        with self._lock:
            r = self._conn.execute(
                "SELECT tenant_id, state FROM marketing_promo_site WHERE item_id = ?", (item_id,)
            ).fetchone()
        return (r["tenant_id"], json.loads(r["state"])) if r else None

    def promo_put(self, item_id: str, state: dict[str, Any]) -> None:
        """이 회사의 항목 상태를 적는다. 다른 회사의 항목은 바꾸지 않는다."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO marketing_promo_site (item_id, tenant_id, state, updated_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET"
                " state = excluded.state, updated_at = excluded.updated_at"
                " WHERE tenant_id = excluded.tenant_id",
                (item_id, self.company_id, json.dumps(state, ensure_ascii=False), now),
            )

    def promo_delete(self, item_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM marketing_promo_site WHERE item_id = ? AND tenant_id = ?",
                (item_id, self.company_id),
            )

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """목록은 가볍게: 요약·미리보기·칸 수만."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM marketing_creative_records WHERE tenant_id = ?"
                " ORDER BY created_at DESC LIMIT ?",
                (self.company_id, max(1, min(limit, KEEP))),
            ).fetchall()
        out = []
        for r in rows:
            sheet = json.loads(r["sheet"])
            board = json.loads(r["storyboard"]) if r["storyboard"] else None
            script = _script(r)
            out.append(
                {
                    "record_id": r["record_id"],
                    "created_at": r["created_at"],
                    "summary_ko": sheet.get("summary_ko", ""),
                    "note": r["note"],
                    "story": r["story"],
                    "thumbnail_base64": r["thumbnail"],
                    "storyboard_title_ko": board.get("title_ko") if board else None,
                    "shot_count": len(board.get("shots", [])) if board else 0,
                    "storyboard_at": r["storyboard_at"],
                    "render": _statuses(r["render"]),
                    "script_scenes": len(script.get("scenes") or []) if script else 0,
                    "script_at": script.get("at") if script else None,
                    "kind": upload_kind(sheet),
                    "references": references_of(sheet),
                    **_video_preview(r["render"]),
                }
            )
        return out

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM marketing_creative_records WHERE record_id = ? AND tenant_id = ?",
                (record_id, self.company_id),
            ).fetchone()
        if r is None:
            return None
        return {
            "record_id": r["record_id"],
            "created_at": r["created_at"],
            "note": r["note"],
            "story": r["story"],
            "shots": r["shots"],
            "thumbnail_base64": r["thumbnail"],
            "sheet": json.loads(r["sheet"]),
            "storyboard": json.loads(r["storyboard"]) if r["storyboard"] else None,
            "storyboard_at": r["storyboard_at"],
            "render": json.loads(r["render"]) if r["render"] else {},
            "script": _script(r),
        }

    def get_render(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            r = self._conn.execute(
                "SELECT render FROM marketing_creative_records"
                " WHERE record_id = ? AND tenant_id = ?",
                (record_id, self.company_id),
            ).fetchone()
        return json.loads(r["render"]) if r is not None and r["render"] else {}

    def set_render(self, record_id: str, render: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE marketing_creative_records SET render = ?"
                " WHERE record_id = ? AND tenant_id = ?",
                (json.dumps(render, ensure_ascii=False), record_id, self.company_id),
            )

    # ------------------------------------------------ 회사 대표 캐릭터 (11/23)
    def _brand_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT record_id, note, thumbnail, sheet, render, brand"
                " FROM marketing_creative_records WHERE tenant_id = ? AND brand IS NOT NULL",
                (self.company_id,),
            ).fetchall()
        return sorted(rows, key=lambda r: str(json.loads(r["brand"]).get("marked_at") or ""))

    def _preview_part(
        self, record_id: str, sheet: dict[str, Any], render: dict[str, Any]
    ) -> str | None:
        """미리보기 칸 (11/26 A): 만든 캐릭터 시트 → "sheet_image", 원본만 있으면 "original_image"
        (화면 "원본 사진 (시트 아직 없음)" + [캐릭터 시트 만들기]), 없으면 None.

        예전 기록(원본이 시트 그림 자리)은 원본 칸으로 복사해 둔다(파일은 지우지 않는다)."""
        from marketing_agent import creative_render as _render

        rec = {"sheet": sheet, "render": render}
        if _render.legacy_original(rec) and _render.move_legacy_original(record_id, rec):
            rec["render"] = self.get_render(record_id)
        if _render.sheet_ready(rec):
            return "sheet_image"
        return "original_image" if _render.original_part(rec) == _render.ORIGINAL_PART else None

    def brand_characters(self) -> list[dict[str, Any]]:
        """이 회사의 대표 캐릭터. 마지막에 표시한 것(기본)부터."""
        rows = list(reversed(self._brand_rows()))
        out = []
        for i, r in enumerate(rows):
            brand = json.loads(r["brand"])
            sheet = json.loads(r["sheet"])
            render = json.loads(r["render"]) if r["render"] else {}
            preview = self._preview_part(r["record_id"], sheet, render)
            name = str(sheet.get("summary_ko") or r["note"] or r["record_id"])
            out.append(
                {
                    "record_id": r["record_id"],
                    "name": " ".join(name.split())[:MAX_BRAND_NAME],
                    "thumbnail_base64": r["thumbnail"],
                    "preview_part": preview,
                    "kind": upload_kind(sheet),
                    "references": references_of(sheet),
                    "saved_at": brand.get("marked_at") or "",
                    "is_default": i == 0,
                    "marked_by": brand.get("marked_by"),
                    "rights_confirmed_by": brand.get("rights_confirmed_by"),
                    "rights_confirmed_at": brand.get("rights_confirmed_at"),
                }
            )
        return out

    def is_brand(self, record_id: str) -> bool:
        return any(r["record_id"] == record_id for r in self._brand_rows())

    def mark_brand(self, record_id: str, who: str | None) -> str | None | bool:
        """대표 캐릭터로 표시한다(권리 확인을 받은 뒤에만 부른다).

        돌려주는 값: 기록이 없으면 False, 아니면 표시가 떨어진 가장 오래된 기록 번호(없으면 None).
        이미 표시한 기록을 다시 표시하면 기본(마지막)이 되고 확인 기록을 새로 남긴다.
        """
        if self.get(record_id) is None:
            return False
        now = datetime.now(UTC).isoformat()
        brand = {
            "marked_at": now,
            "marked_by": who,
            "rights_confirmed": True,
            "rights_confirmed_by": who,
            "rights_confirmed_at": now,
        }
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE marketing_creative_records SET brand = ?"
                " WHERE record_id = ? AND tenant_id = ?",
                (json.dumps(brand, ensure_ascii=False), record_id, self.company_id),
            )
        rows = self._brand_rows()
        dropped = None
        while len(rows) > MAX_BRAND_CHARACTERS:
            oldest = rows.pop(0)["record_id"]
            self.unmark_brand(oldest)
            dropped = oldest
        logger.info("대표 캐릭터 표시: %s (%s)", record_id, self.company_id)
        return dropped

    def unmark_brand(self, record_id: str) -> bool:
        """표시만 뗀다(기록은 남는다). 표시가 없거나 다른 회사 기록이면 False."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE marketing_creative_records SET brand = NULL"
                " WHERE record_id = ? AND tenant_id = ? AND brand IS NOT NULL",
                (record_id, self.company_id),
            )
        return cur.rowcount == 1

    @staticmethod
    def request_record_id(company_id: str, context_id: str, brand_id: str) -> str:
        """의뢰마다 쓸 제작 기록 번호. 같은 회사·의뢰·캐릭터면 늘 같다(다시 불려도 하나만)."""
        digest = hashlib.sha256(f"{company_id}|{context_id}|{brand_id}".encode()).hexdigest()
        return f"CR-{digest[:12]}"

    def copy_for_request(self, brand_id: str, context_id: str) -> str | None:
        """대표 캐릭터 기록의 시트·미리보기·시트 그림 상태를 의뢰용 새 기록으로 옮긴다.

        새 기록 번호를 돌려준다. 대표 캐릭터 기록이 이 회사에 없으면 None.
        시트 그림 파일 복사는 부르는 쪽(`request_creative`)이 한다."""
        source = self.get(brand_id)
        if source is None:
            return None
        rid = self.request_record_id(self.company_id, context_id, brand_id)
        if self.get(rid) is None:
            note = f"의뢰 {context_id.removeprefix('ctx_')} · 대표 캐릭터 {brand_id}"
            self.add_sheet(source["sheet"], note, source["thumbnail_base64"], record_id=rid)
        return rid

    # ------------------------------------------------ 기획안에서 만든 기록 (11/25)
    def brief_record(self, proposal_id: str) -> tuple[str, str | None] | None:
        """(기록 번호, 그때 쓴 대표 캐릭터 번호). 만든 적이 없으면 None."""
        with self._lock:
            r = self._conn.execute(
                "SELECT record_id, character_record_id FROM marketing_brief_creative"
                " WHERE proposal_id = ? AND tenant_id = ?",
                (proposal_id, self.company_id),
            ).fetchone()
        return (r["record_id"], r["character_record_id"]) if r is not None else None

    def set_brief_record(
        self, proposal_id: str, record_id: str, character_record_id: str | None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO marketing_brief_creative"
                " (proposal_id, tenant_id, record_id, character_record_id, created_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(proposal_id, tenant_id) DO UPDATE SET"
                " record_id = excluded.record_id,"
                " character_record_id = excluded.character_record_id,"
                " created_at = excluded.created_at",
                (
                    proposal_id,
                    self.company_id,
                    record_id,
                    character_record_id,
                    datetime.now(UTC).isoformat(),
                ),
            )

    # ------------------------------------------------ 의뢰로 만든 영상의 유튜브 상태
    def get_request_youtube(self, post_draft_id: str) -> dict[str, Any]:
        with self._lock:
            r = self._conn.execute(
                "SELECT youtube FROM marketing_request_video_youtube"
                " WHERE post_draft_id = ? AND tenant_id = ?",
                (post_draft_id, self.company_id),
            ).fetchone()
        return json.loads(r["youtube"]) if r is not None else {}

    def set_request_youtube(
        self, post_draft_id: str, request_id: str, youtube: dict[str, Any]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO marketing_request_video_youtube"
                " (post_draft_id, tenant_id, request_id, youtube, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(post_draft_id, tenant_id)"
                " DO UPDATE SET youtube = excluded.youtube, updated_at = excluded.updated_at",
                (
                    post_draft_id,
                    self.company_id,
                    request_id,
                    json.dumps(youtube, ensure_ascii=False),
                    now,
                ),
            )

    def request_youtube_all(self, limit: int = KEEP) -> dict[str, dict[str, Any]]:
        """이 회사의 의뢰 영상 유튜브 상태 전부(post_draft_id → 상태), 최근 것부터."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT post_draft_id, youtube FROM marketing_request_video_youtube"
                " WHERE tenant_id = ? ORDER BY updated_at DESC LIMIT ?",
                (self.company_id, max(1, limit)),
            ).fetchall()
        return {r["post_draft_id"]: json.loads(r["youtube"]) for r in rows}

    # ------------------------------------------------ 의뢰로 만든 영상의 인스타 상태
    def get_request_instagram(self, post_draft_id: str) -> dict[str, Any]:
        with self._lock:
            r = self._conn.execute(
                "SELECT instagram FROM marketing_request_video_instagram"
                " WHERE post_draft_id = ? AND tenant_id = ?",
                (post_draft_id, self.company_id),
            ).fetchone()
        return json.loads(r["instagram"]) if r is not None else {}

    def set_request_instagram(
        self, post_draft_id: str, request_id: str, instagram: dict[str, Any]
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO marketing_request_video_instagram"
                " (post_draft_id, tenant_id, request_id, instagram, updated_at)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(post_draft_id, tenant_id)"
                " DO UPDATE SET instagram = excluded.instagram, updated_at = excluded.updated_at",
                (
                    post_draft_id,
                    self.company_id,
                    request_id,
                    json.dumps(instagram, ensure_ascii=False),
                    now,
                ),
            )

    def request_instagram_all(self, limit: int = KEEP) -> dict[str, dict[str, Any]]:
        """이 회사의 의뢰 영상 인스타 상태 전부(post_draft_id → 상태), 최근 것부터."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT post_draft_id, instagram FROM marketing_request_video_instagram"
                " WHERE tenant_id = ? ORDER BY updated_at DESC LIMIT ?",
                (self.company_id, max(1, limit)),
            ).fetchall()
        return {r["post_draft_id"]: json.loads(r["instagram"]) for r in rows}


def _script(row: sqlite3.Row) -> dict[str, Any] | None:
    """기록의 대본(옛 기록·대본 없는 기록은 None)."""
    raw = row["script"] if "script" in row.keys() else None
    return json.loads(raw) if raw else None


def _statuses(raw: str | None) -> dict[str, str]:
    """목록용: 단계별 상태만(sheet_image·board_image·video)."""
    render = json.loads(raw) if raw else {}
    return {k: str((v or {}).get("status") or "") for k, v in render.items() if isinstance(v, dict)}


def _video_preview(raw: str | None) -> dict[str, Any]:
    """목록의 영상 칸 미리보기(11/26): 지금 정해진 썸네일과 영상 길이. 없으면 None.

    썸네일은 고른 후보, 고르지 않았으면 첫 후보다(`creative_thumbnails.chosen_id`와 같은 규칙)."""
    render = json.loads(raw) if raw else {}
    thumbs = render.get("thumbnails") or {}
    ids = [str(c.get("media_id")) for c in thumbs.get("candidates") or [] if c.get("media_id")]
    cover = None
    if ids:
        cover = thumbs.get("chosen") if thumbs.get("chosen") in ids else ids[0]
    # 지금 보여 주는 영상(완성 영상이 있으면 그것)의 길이만. 직접 올린 파일은 길이를 몰라 None
    final = render.get("final_video") or {}
    info = final if final.get("status") == "done" else render.get("video") or {}
    sped = (render.get("overlay") or {}).get("speed_applied")  # 배속했으면 재생 길이는 계획 길이
    value = (info.get("plan_seconds") if sped else None) or info.get("seconds")
    known = info.get("status") == "done" and isinstance(value, int | float) and value > 0
    return {"cover_media_id": cover, "video_seconds": float(value) if known else None}


_store: CreativeRecords | None = None
_by_company: dict[str, CreativeRecords] = {}


def store() -> CreativeRecords:
    """요청 회사의 제작 기록 저장소(11/23). 요청 밖이면 지금처럼 기본 저장소(캣북스).

    같은 DB 파일을 회사 칸으로 나눠 쓴다(회사마다 연결 하나). 메모리 DB면 회사마다 따로다."""
    global _store
    active = request_company.active()
    company = company_scope.scope_id(active)
    if _store is None:
        _store = CreativeRecords()
    if active is None or _store.company_id == company:
        return _store
    found = _by_company.get(company)
    if found is None:
        found = _by_company[company] = CreativeRecords(_store.path, company_id=company)
    return found


def set_store(s: CreativeRecords | None) -> None:
    """테스트용. 다른 회사 저장소도 비운다(그 회사는 같은 파일로 새로 연다)."""
    global _store
    _store = s
    _by_company.clear()
