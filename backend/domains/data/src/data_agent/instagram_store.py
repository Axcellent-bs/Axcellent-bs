"""인스타그램 저장소 (backend/.data). 모두 SQLite, 읽기는 파일이 없으면 만들지 않는다.

- `ig_audio.db`: 날짜(한국)·종류·순위·오디오 id·제목·만든이·길이·인스타 링크
  (미리듣기 주소는 저장 안 함)
- `ig_radar.db`: 날짜·계정·미디어(id·종류·게시 시각·좋아요·댓글·조회·캡션·링크), 계정 실패 이유
- `ig_hashtags.db`: 해시태그·해시태그 id·첫 조회 시각(7일 30개 한도 관리)·그날 결과(재사용)
- `ig_memo.db`: 수동 메모 (종류·이름·메모·적은 사람·날짜·연결할 유행)
- 수집 기록(`runs`, ig_audio.db 안): 출처마다 마지막 상태·이유 (화면 데이터 출처 줄)
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

DATA = Path(__file__).resolve().parents[4] / ".data"  # backend/.data
AUDIO_ENV, RADAR_ENV = "DATA_IG_AUDIO_DB", "DATA_IG_RADAR_DB"
HASHTAG_ENV, MEMO_ENV = "DATA_IG_HASHTAGS_DB", "DATA_IG_MEMO_DB"


def path_of(env: Mapping[str, str], name: str) -> str:
    default = {AUDIO_ENV: "ig_audio.db", RADAR_ENV: "ig_radar.db", HASHTAG_ENV: "ig_hashtags.db",
               MEMO_ENV: "ig_memo.db"}[name]  # fmt: skip
    return env.get(name) or str(DATA / default)


def _ro(path: str | Path) -> sqlite3.Connection | None:
    if str(path) != ":memory:" and not Path(path).is_file():
        return None
    return sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)


class _Store:
    SCHEMA = ""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(self.SCHEMA)

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------- 오디오 + 수집 기록


class AudioStore(_Store):
    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS audio (day TEXT NOT NULL, audio_type TEXT NOT NULL,"
        " rank INTEGER NOT NULL, audio_id TEXT NOT NULL, title TEXT, artist TEXT,"
        " duration_ms INTEGER, link TEXT, PRIMARY KEY (day, audio_type, rank));"
        "CREATE TABLE IF NOT EXISTS runs (source TEXT PRIMARY KEY, day TEXT NOT NULL,"
        " status TEXT NOT NULL, reason TEXT, at TEXT NOT NULL);"
    )

    def save(self, day: date, audio_type: str, items: Sequence[Mapping[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM audio WHERE day = ? AND audio_type = ?",
                               (day.isoformat(), audio_type))  # fmt: skip
            self._conn.executemany(
                "INSERT INTO audio VALUES (?,?,?,?,?,?,?,?)",
                [(day.isoformat(), audio_type, n, i["audio_id"], i.get("title"), i.get("artist"),
                  i.get("duration_ms"), i.get("link")) for n, i in enumerate(items, start=1)],
            )  # fmt: skip

    def done(self, day: date, audio_type: str) -> bool:
        with self._lock:
            row = self._conn.execute("SELECT 1 FROM audio WHERE day = ? AND audio_type = ? LIMIT 1",
                                     (day.isoformat(), audio_type)).fetchone()  # fmt: skip
        return row is not None

    def log(self, source: str, day: date, status: str, reason: str | None, at: datetime) -> None:
        with self._lock, self._conn:
            row = (source, day.isoformat(), status, reason, at.isoformat())
            self._conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?)", row)


def audio_days(path: str | Path, audio_type: str, until: date,
               days: int = 30) -> dict[date, list[dict[str, Any]]]:  # fmt: skip
    """날짜 → 그날 목록(순위 순). 읽기 전용."""
    conn = _ro(path)
    if conn is None:
        return {}
    try:
        rows = conn.execute(
            "SELECT day, rank, audio_id, title, artist, duration_ms, link FROM audio"
            " WHERE audio_type = ? AND day >= ? AND day <= ? ORDER BY day, rank",
            (audio_type, (until - timedelta(days=days)).isoformat(), until.isoformat()),
        ).fetchall()
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()
    out: dict[date, list[dict[str, Any]]] = {}
    for day, rank, aid, title, artist, dur, link in rows:
        out.setdefault(date.fromisoformat(day), []).append(
            {"rank": rank, "audio_id": aid, "title": title, "artist": artist,
             "duration_ms": dur, "link": link})  # fmt: skip
    return out


def runs(path: str | Path) -> dict[str, tuple[str, str, str | None]]:
    """출처 → (날짜, 상태, 이유). 읽기 전용."""
    conn = _ro(path)
    if conn is None:
        return {}
    try:
        return {s: (d, st, r) for s, d, st, r in
                conn.execute("SELECT source, day, status, reason FROM runs")}  # fmt: skip
    except sqlite3.DatabaseError:
        return {}
    finally:
        conn.close()


# ---------------------------------------------------------------- 레이더


class RadarStore(_Store):
    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS media (day TEXT NOT NULL, account TEXT NOT NULL,"
        " media_id TEXT NOT NULL, product_type TEXT, posted_at TEXT, like_count INTEGER,"
        " comments_count INTEGER, view_count INTEGER, caption TEXT, permalink TEXT,"
        " PRIMARY KEY (day, account, media_id));"
        "CREATE TABLE IF NOT EXISTS accounts (day TEXT NOT NULL, account TEXT NOT NULL,"
        " status TEXT NOT NULL, reason TEXT, PRIMARY KEY (day, account));"
    )

    def save(self, day: date, account: str, media: Sequence[Mapping[str, Any]],
             status: str = "ok", reason: str | None = None) -> None:  # fmt: skip
        with self._lock, self._conn:
            d = day.isoformat()
            self._conn.execute("DELETE FROM media WHERE day = ? AND account = ?", (d, account))
            self._conn.executemany(
                "INSERT OR REPLACE INTO media VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(d, account, str(m["id"]), m.get("media_product_type"), m.get("timestamp"),
                  m.get("like_count"), m.get("comments_count"), m.get("view_count"),
                  m.get("caption"), m.get("permalink")) for m in media],
            )  # fmt: skip
            self._conn.execute("INSERT OR REPLACE INTO accounts VALUES (?,?,?,?)",
                               (d, account, status, reason))  # fmt: skip

    def done(self, day: date, account: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM accounts WHERE day = ? AND account = ? AND status = 'ok'",
                (day.isoformat(), account),
            ).fetchone()
        return row is not None


def radar_latest(path: str | Path) -> tuple[date | None, dict[str, list[dict[str, Any]]],
                                           dict[str, str]]:  # fmt: skip
    """(날짜, 계정 → 미디어, 계정 → 실패 이유). 읽기 전용."""
    conn = _ro(path)
    if conn is None:
        return None, {}, {}
    try:
        row = conn.execute("SELECT MAX(day) FROM accounts").fetchone()
        if not row or not row[0]:
            return None, {}, {}
        day = row[0]
        media: dict[str, list[dict[str, Any]]] = {}
        for acc, mid, pt, at, likes, comments, views, cap, link in conn.execute(
            "SELECT account, media_id, product_type, posted_at, like_count, comments_count,"
            " view_count, caption, permalink FROM media WHERE day = ?", (day,)
        ):
            media.setdefault(acc, []).append({
                "id": mid, "media_product_type": pt, "timestamp": at, "like_count": likes,
                "comments_count": comments, "view_count": views, "caption": cap,
                "permalink": link,
            })  # fmt: skip
        failed = {a: r or s for a, s, r in conn.execute(
            "SELECT account, status, reason FROM accounts WHERE day = ? AND status != 'ok'",
            (day,))}  # fmt: skip
        return date.fromisoformat(day), media, failed
    except sqlite3.DatabaseError:
        return None, {}, {}
    finally:
        conn.close()


# ---------------------------------------------------------------- 해시태그 사용 기록


class HashtagStore(_Store):
    """7일 30개 한도: 해시태그마다 첫 조회 시각. 그날 결과는 다시 부르지 않게 남긴다."""

    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS usage (hashtag TEXT PRIMARY KEY, hashtag_id TEXT,"
        " first_at TEXT NOT NULL);"
        "CREATE TABLE IF NOT EXISTS results (day TEXT NOT NULL, hashtag TEXT NOT NULL,"
        " value TEXT NOT NULL, PRIMARY KEY (day, hashtag));"
    )
    WINDOW = timedelta(days=7)

    def active(self, now: datetime) -> dict[str, tuple[str | None, datetime]]:
        """7일 안에 처음 조회한 해시태그 → (id, 첫 조회 시각)."""
        since = (now - self.WINDOW).isoformat()
        with self._lock:
            rows = self._conn.execute(
                "SELECT hashtag, hashtag_id, first_at FROM usage WHERE first_at > ?", (since,)
            ).fetchall()
        return {h: (i, datetime.fromisoformat(at)) for h, i, at in rows}

    def use(self, hashtag: str, hashtag_id: str | None, now: datetime) -> None:
        """조회를 기록한다.

        7일 안에 이미 있으면 첫 시각을 바꾸지 않는다(문서: 타이머 재설정 안 됨).
        """
        with self._lock, self._conn:
            row = self._conn.execute("SELECT first_at FROM usage WHERE hashtag = ?",
                                     (hashtag,)).fetchone()  # fmt: skip
            if row and datetime.fromisoformat(row[0]) > now - self.WINDOW:
                if hashtag_id:
                    self._conn.execute("UPDATE usage SET hashtag_id = ? WHERE hashtag = ?",
                                       (hashtag_id, hashtag))  # fmt: skip
                return
            self._conn.execute("INSERT OR REPLACE INTO usage VALUES (?,?,?)",
                               (hashtag, hashtag_id, now.isoformat()))  # fmt: skip

    def result(self, day: date, hashtag: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM results WHERE day = ? AND hashtag = ?",
                                     (day.isoformat(), hashtag)).fetchone()  # fmt: skip
        return json.loads(row[0]) if row else None

    def save_result(self, day: date, hashtag: str, value: Mapping[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT OR REPLACE INTO results VALUES (?,?,?)",
                               (day.isoformat(), hashtag, json.dumps(value)))  # fmt: skip


# ---------------------------------------------------------------- 메모


class MemoStore(_Store):
    SCHEMA = (
        "CREATE TABLE IF NOT EXISTS memos (memo_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,"
        " kind TEXT NOT NULL, name TEXT NOT NULL, memo TEXT NOT NULL, author TEXT NOT NULL,"
        " created_at TEXT NOT NULL, linked_trend TEXT);"
    )

    def add(self, tenant: str, kind: str, name: str, memo: str, author: str,
            linked: str | None, at: datetime) -> dict[str, Any]:  # fmt: skip
        mid = f"igm_{uuid.uuid4().hex[:12]}"
        with self._lock, self._conn:
            row = (mid, tenant, kind, name, memo, author, at.isoformat(), linked)
            self._conn.execute("INSERT INTO memos VALUES (?,?,?,?,?,?,?,?)", row)
        return {"memo_id": mid, "kind": kind, "name": name, "memo": memo, "author": author,
                "created_at": at, "linked_trend": linked}  # fmt: skip


def memos(path: str | Path, tenant: str, limit: int) -> list[dict[str, Any]]:
    conn = _ro(path)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT memo_id, kind, name, memo, author, created_at, linked_trend FROM memos"
            " WHERE tenant_id = ? ORDER BY created_at DESC LIMIT ?", (tenant, limit),
        ).fetchall()
    except sqlite3.DatabaseError:
        return []
    finally:
        conn.close()
    return [{"memo_id": m, "kind": k, "name": n, "memo": t, "author": a,
             "created_at": datetime.fromisoformat(c), "linked_trend": lt}
            for m, k, n, t, a, c, lt in rows]  # fmt: skip


def env_or(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env
