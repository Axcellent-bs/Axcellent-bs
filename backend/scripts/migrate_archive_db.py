"""결정 기록·이슈 기록을 인사 파일(hr.db)에서 아카이브 전용 파일(archive.db)로 옮긴다.

4단계 데이터 이전. 아카이브 AI가 소유하는 표만 복사한다. 예전 hr.db는 지우거나 고치지 않는다.

    cd backend
    uv run python scripts/migrate_archive_db.py --dry-run   # 옮길 건수만 본다
    uv run python scripts/migrate_archive_db.py             # 백업 후 이전

- 옮기는 것: records, mock_cards, gate_decisions(아카이브 행동), audit_logs(아카이브가 남긴 종류),
  idempotency(아카이브 skill 키), 검색 벡터 파일(hr_vectors.db → archive_vectors.db)
- 안 옮기는 것: 조직 변동·인수인계·교대·온보딩 같은 인사 표와 인사가 남긴 감사 기록
- 다시 돌려도 안전하다(이미 있는 행은 건너뛴다).
- 이전 전에 원본을 `.data_backup_<시각>_archive_migrate/`로 백업한다.
- 서버를 끈 상태에서 돌린다(`bash scripts/run_all.sh stop`).
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 아카이브가 소유하는 표와 행의 기준. 같은 파일에 있던 인사 행은 가져가지 않는다.
ARCHIVE_SKILLS = (
    "record_case", "check_plan", "list_candidate_cards", "list_cards", "get_audit_timeline",
    "decide_candidate_card", "retire_request_cards", "find_similar_fraud_cards",
    "find_similar_cases", "record_resolution", "record_event_times", "list_duplicate_groups",
    "decide_duplicate_group", "restore_merged_card", "answer_planning_question",
    "find_similar_decisions",
)  # fmt: skip
ARCHIVE_GATE_ACTIONS = ("memory.card_write", "memory.card_link", "record_case")
ARCHIVE_AUDIT_PREFIXES = (
    "record_case", "record_only", "record_event_times", "record_resolution", "card_decision",
    "card_write_blocked", "check_plan", "dept_eval.", "duplicate_group", "answer_planning",
    "retire_request",
)  # fmt: skip


def _like_any(column: str, prefixes: tuple[str, ...]) -> tuple[str, list[str]]:
    return "(" + " OR ".join(f"{column} LIKE ?" for _ in prefixes) + ")", [
        f"{p}%" for p in prefixes
    ]


def _idem_where() -> tuple[str, list[str]]:
    # 키는 "<skill>:<이슈>:..." 또는 "<이슈>:<skill>:..." 꼴이다
    clauses, params = [], []
    for s in ARCHIVE_SKILLS:
        clauses += ["key LIKE ?", "key LIKE ?"]
        params += [f"{s}:%", f"%:{s}:%"]
    return "(" + " OR ".join(clauses) + ")", params


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _count(
    conn: sqlite3.Connection, table: str, where: str = "1", params: list | None = None
) -> int:
    if table not in _tables(conn):
        return 0
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params or []).fetchone()[0]


def _sqlite_copy(src: Path, dst: Path) -> None:
    """실행 중 쓰기와 섞여도 일관된 사본이 되도록 sqlite backup으로 복사한다."""
    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def migrate(
    src: Path, dst: Path, *, dry_run: bool = False, backup_root: Path | None = None
) -> dict:
    """옮긴 결과를 돌려준다: 표마다 {원본, 이전 전, 이전 후}. 원본은 아카이브 기준으로 센 건수."""
    if not src.is_file():
        raise SystemExit(f"원본이 없습니다: {src}")
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "domains" / "archive" / "src"))
    from archive_agent.store import SCHEMA  # 표 모양은 아카이브 코드가 정한다

    audit_w, audit_p = _like_any("kind", ARCHIVE_AUDIT_PREFIXES)
    idem_w, idem_p = _idem_where()
    gate_w, gate_p = _like_any("action", ARCHIVE_GATE_ACTIONS)
    plan = {  # 표, 조건, 값
        "records": ("1", []),
        "mock_cards": ("1", []),
        "gate_decisions": (gate_w, gate_p),
        "audit_logs": (audit_w, audit_p),
        "idempotency": (idem_w, idem_p),
    }

    s = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    report: dict[str, dict[str, int]] = {}
    for t, (w, p) in plan.items():
        report[t] = {"원본": _count(s, t, w, p), "이전 전": 0, "이전 후": 0}
    vec_src = src.with_name(f"{src.stem}_vectors.db")
    vec_dst = dst.with_name(f"{dst.stem}_vectors.db")

    if dry_run:
        if dst.is_file():
            d = sqlite3.connect(f"file:{dst.as_posix()}?mode=ro", uri=True)
            for t in plan:
                report[t]["이전 전"] = _count(d, t)
            d.close()
        s.close()
        return report

    # 1) 원본 백업
    stamp = datetime.now().strftime("%m%d_%H%M%S")
    root = backup_root or src.parent.parent
    bdir, n = root / f".data_backup_{stamp}_archive_migrate", 1
    while bdir.exists():  # 같은 초에 다시 돌려도 덮어쓰지 않는다
        n += 1
        bdir = root / f".data_backup_{stamp}_archive_migrate_{n}"
    bdir.mkdir(parents=True)
    s.close()
    _sqlite_copy(src, bdir / src.name)
    if vec_src.is_file():
        _sqlite_copy(vec_src, bdir / vec_src.name)
    if dst.is_file():
        _sqlite_copy(dst, bdir / dst.name)
    print(f"백업: {bdir}")

    # 2) 복사 (이미 있는 행은 건너뜀)
    dst.parent.mkdir(parents=True, exist_ok=True)
    d = sqlite3.connect(dst)
    d.executescript(SCHEMA)
    for t in plan:
        report[t]["이전 전"] = _count(d, t)
    d.execute("ATTACH DATABASE ? AS src", (str(src),))
    have = {r[0] for r in d.execute("SELECT name FROM src.sqlite_master WHERE type='table'")}
    for t, (w, p) in plan.items():
        if t not in have:
            continue
        cols = [r[1] for r in d.execute(f"PRAGMA main.table_info({t})")]
        scols = {r[1] for r in d.execute(f"PRAGMA src.table_info({t})")}
        use = [c for c in cols if c in scols and not (t == "audit_logs" and c == "id")]
        names = ", ".join(use)
        sel = ", ".join(f"x.{c}" for c in use)
        if t == "audit_logs":  # id는 새로 매기고, 같은 행이 이미 있으면 건너뛴다
            d.execute(
                f"INSERT INTO main.{t} ({names}) SELECT {sel} FROM src.{t} x "
                f"WHERE {w.replace('kind', 'x.kind')} "
                f"AND NOT EXISTS (SELECT 1 FROM main.{t} m WHERE m.context_id=x.context_id "
                "AND m.kind=x.kind "
                "AND m.payload=x.payload AND m.created_at=x.created_at)",
                p,
            )
        else:
            d.execute(
                f"INSERT OR IGNORE INTO main.{t} ({names}) SELECT {sel} FROM src.{t} x WHERE "
                + w.replace("key LIKE", "x.key LIKE").replace("action LIKE", "x.action LIKE"),
                p,
            )
    d.commit()
    d.execute("DETACH DATABASE src")
    for t in plan:
        report[t]["이전 후"] = _count(d, t)
    d.close()

    # 3) 검색 벡터 파일 (아카이브 쪽에 아직 없을 때만)
    if vec_src.is_file() and not vec_dst.is_file():
        _sqlite_copy(vec_src, vec_dst)
        print(f"벡터 복사: {vec_src.name} → {vec_dst.name}")
    elif vec_dst.is_file():
        print(f"벡터 파일이 이미 있어 건너뜀: {vec_dst.name}")
    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--src", default=".data/hr.db", type=Path)
    ap.add_argument("--dst", default=".data/archive.db", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="옮기지 않고 건수만 본다")
    a = ap.parse_args()
    rep = migrate(a.src, a.dst, dry_run=a.dry_run)
    print(f"{'(미리보기) ' if a.dry_run else ''}{a.src} → {a.dst}")
    ok = True
    for t, r in rep.items():
        print(f"  {t:15} 원본 {r['원본']:>4}  이전 전 {r['이전 전']:>4}  이전 후 {r['이전 후']:>4}")
        if not a.dry_run and r["이전 후"] < r["원본"]:
            ok = False
    if not a.dry_run:
        print("건수 일치" if ok else "[경고] 일부 행이 옮겨지지 않았습니다")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
