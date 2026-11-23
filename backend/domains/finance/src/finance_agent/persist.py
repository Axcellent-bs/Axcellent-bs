"""재무 기록을 파일에 남기고 다시 켤 때 읽는다 (`FINANCE_STATE_FILE`).

메모리에만 있던 기록(이벤트로 쌓은 쿠폰·환불 장부, 사람의 결산 확정·
대사 메모·정산 묶음 요청)을 JSON 하나에 담는다. 백엔드를 다시 켜도
재무 화면과 멱등 처리(같은 요청을 두 번 실행하지 않음)가 이어진다.

- 경로가 없으면(테스트·단독 실행) 아무것도 하지 않는다
- `FINANCE_STATE_FILE`을 안 정했고 회사 폴더를 켰으면(`AX_COMPANIES_DIR`, 여러 회사 설계 1-6)
  `<회사 폴더>/catbooks/finance_state.json` (옛 자리에 있으면 처음 읽을 때 옮긴다, `company_folder`)
- 캣북스 밖 회사 기록은 회사마다 따로 쓴다(`company_state_file`, 여러 회사 5단계)
- 몇 초마다 바뀐 것만 쓰고, 서버를 끌 때 한 번 더 쓴다
- 임시 파일에 쓴 뒤 바꿔 끼워 반쯤 쓴 파일이 남지 않게 한다
- 읽다가 형식이 틀리면 버리지 않고 옆에 `.broken`으로 옮기고 빈 상태로 시작한다
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

from ax_agent_template import company_files
from ax_agent_template.companies import company_path
from ax_contracts.schemas import TENANT_ID_DEFAULT
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS
from finance_agent import company_folder, exports
from finance_agent.handlers import State, company_states

logger = logging.getLogger(__name__)

VERSION = 1
SAVE_SECONDS = 5.0


STATE_ENV = "FINANCE_STATE_FILE"
STATE_NAME = "finance_state.json"


def state_file() -> Path | None:
    """`FINANCE_STATE_FILE` → 회사 폴더(`<회사 폴더>/catbooks/finance_state.json`) → None."""
    return company_folder.path_for(STATE_ENV, STATE_NAME)


def company_state_file(company_id: str) -> Path | None:
    """캣북스 밖 회사의 기록 파일 (여러 회사 5단계). 캣북스는 `state_file()`.

    `FINANCE_STATE_FILE`을 정했으면 그 옆 `finance_state.<회사>.json`, 아니면 회사 폴더를 켰을 때
    `<회사 폴더>/<회사>/finance_state.json`, 둘 다 없으면 None(저장하지 않음).
    """
    if company_id == TENANT_ID_CATBOOKS:
        return state_file()
    pinned = os.getenv(STATE_ENV, "").strip()
    if pinned:
        path = Path(pinned)
        return path.with_name(f"{path.stem}.{company_id}{path.suffix}")
    if not company_files.folders_on():
        return None
    path = company_path(company_id, STATE_NAME)
    if path is not None and not path.parent.exists():  # 회사 폴더는 0700 (company_folder와 같다)
        path.parent.parent.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(mode=company_files.DIR_MODE, exist_ok=True)
    return path


def dump(s: State) -> dict[str, Any]:
    ledger, records = s.ledger, s.records
    return {
        "version": VERSION,
        "ledger": {
            "coupons": ledger.coupons,
            "refunds": ledger.refunds,
            "seen_events": sorted(ledger.seen_events),
            "support_costs": {str(k): v for k, v in ledger.support_costs.items()},
            "coupon_events": ledger.coupon_events,
            "incident_windows": ledger.incident_windows,
            "incident_reports": ledger.incident_reports,
        },
        "records": {
            "closings": [[p, d, v] for (p, d), v in records.closings.items()],
            "closing_cancels": records.closing_cancels,
            "other_pnl": records.other_pnl,
            "vat_filings": records.vat_filings,
            "vat_filing_cancels": records.vat_filing_cancels,
            "recon_notes": records.recon_notes,
            "recon_note_log": records.recon_note_log,
            "planning_requests": records.planning_requests,
            "planning_questions": records.planning_questions,
            "close_alerts": records.close_alerts,
            "final_impacts": records.final_impacts,
            # 예전 정산 묶음(쓰지 않음). 고객지원으로 옮기는 스크립트가 읽게 그대로 남긴다
            "batches": records.legacy_batches,
            "policy_acks": records.policy_acks,
            "bank_rows": records.bank_rows,
            "bank_out_periods": records.bank_out_periods,
            "bank_notes": records.bank_notes,
            "bank_note_log": records.bank_note_log,
            "team_budgets": records.team_budgets,
            "campaigns": records.campaigns,
            "campaign_starts": records.campaign_starts,
            "campaign_cancels": records.campaign_cancels,
            "budget_reviews": records.budget_reviews,
            "lessons": records.lessons,
            "opinions": records.opinions,
            "planning_insights": records.planning_insights,
            "budget_asks": records.budget_asks,
            "exports": records.exports,
            "bank_imports": records.bank_imports,
            "pg_rows": records.pg_rows,
            "pg_periods": records.pg_periods,
            "pg_imports": records.pg_imports,
        },
    }


def _remember_sent(rows: list[dict[str, Any]]) -> None:
    """다시 켜도 기획의 되묻기에 답할 수 있게 보낸 요청 기록을 되살린다."""
    from finance_agent.planning_questions import record_sent_request

    for r in rows:
        if r.get("withdrawn_at"):  # 거둬들인 요청은 되묻기에 답하지 않는다
            continue
        if r.get("request_id") and r.get("context_id"):
            record_sent_request(
                r.get("tenant_id") or TENANT_ID_DEFAULT,
                r["context_id"],
                r["request_id"],
                facts=list(r.get("facts") or []),
                evidence_ids=list(r.get("evidence_ids") or []),
            )


def restore(s: State, data: dict[str, Any]) -> None:
    if data.get("version") != VERSION:
        raise ValueError(f"모르는 재무 기록 버전: {data.get('version')}")
    led, rec = data["ledger"], data["records"]
    s.ledger.coupons = {k: dict(v) for k, v in led["coupons"].items()}
    s.ledger.refunds = dict(led["refunds"])
    s.ledger.seen_events = set(led["seen_events"])
    # 키: 문의 번호(예전 기록) 또는 바뀌지 않는 문의 ID(`inquiry_ref`, `ctx_…`)
    s.ledger.support_costs = {
        (int(k) if k.isdigit() else k): v for k, v in led["support_costs"].items()
    }
    s.ledger.coupon_events = {k: list(v) for k, v in led["coupon_events"].items()}
    s.ledger.incident_windows = {k: dict(v) for k, v in led.get("incident_windows", {}).items()}
    s.ledger.incident_reports = {k: dict(v) for k, v in led.get("incident_reports", {}).items()}
    s.records.closings = {(p, d): v for p, d, v in rec["closings"]}
    s.records.closing_cancels = [dict(r) for r in rec.get("closing_cancels", [])]
    s.records.other_pnl = {k: dict(v) for k, v in rec.get("other_pnl", {}).items()}
    s.records.vat_filings = {k: dict(v) for k, v in rec.get("vat_filings", {}).items()}
    s.records.vat_filing_cancels = [dict(r) for r in rec.get("vat_filing_cancels", [])]
    s.records.recon_notes = dict(rec["recon_notes"])
    s.records.recon_note_log = list(rec["recon_note_log"])
    s.records.planning_requests = [dict(r) for r in rec.get("planning_requests", [])]
    s.records.close_alerts = {k: dict(v) for k, v in rec.get("close_alerts", {}).items()}
    s.records.final_impacts = {k: dict(v) for k, v in rec.get("final_impacts", {}).items()}
    s.records.planning_questions = {
        k: dict(v) for k, v in rec.get("planning_questions", {}).items()
    }
    _remember_sent(s.records.planning_requests)
    s.records.legacy_batches = {k: dict(v) for k, v in rec.get("batches", {}).items()}
    s.records.policy_acks = {k: dict(v) for k, v in rec.get("policy_acks", {}).items()}
    s.records.bank_rows = [dict(r) for r in rec.get("bank_rows", [])]
    s.records.bank_out_periods = [dict(p) for p in rec.get("bank_out_periods", [])]
    s.records.bank_notes = {k: dict(v) for k, v in rec.get("bank_notes", {}).items()}
    s.records.bank_note_log = list(rec.get("bank_note_log", []))
    s.records.team_budgets = {k: dict(v) for k, v in rec.get("team_budgets", {}).items()}
    s.records.campaigns = {k: dict(v) for k, v in rec.get("campaigns", {}).items()}
    s.records.campaign_starts = {k: dict(v) for k, v in rec.get("campaign_starts", {}).items()}
    s.records.campaign_cancels = {k: dict(v) for k, v in rec.get("campaign_cancels", {}).items()}
    s.records.budget_reviews = [dict(r) for r in rec.get("budget_reviews", [])]
    s.records.lessons = [dict(r) for r in rec.get("lessons", [])]
    s.records.opinions = [dict(r) for r in rec.get("opinions", [])]
    s.records.planning_insights = [dict(r) for r in rec.get("planning_insights", [])]
    s.records.budget_asks = [dict(r) for r in rec.get("budget_asks", [])]
    s.records.exports = {k: dict(v) for k, v in rec.get("exports", {}).items()}
    s.records.bank_imports = [dict(r) for r in rec.get("bank_imports", [])]
    exports.give_legacy_ids(s.records.bank_imports)  # id 전에 읽은 기록도 뺄 수 있게
    s.records.pg_rows = {k: dict(v) for k, v in rec.get("pg_rows", {}).items()}
    s.records.pg_periods = [dict(p) for p in rec.get("pg_periods", [])]
    s.records.pg_imports = [dict(r) for r in rec.get("pg_imports", [])]
    s.records.pg_version += 1


def _text(s: State) -> str:
    return json.dumps(dump(s), ensure_ascii=False, sort_keys=True)


def fingerprint(s: State) -> str:
    """사람 기록·장부 기록 전체의 지문(바뀌었는지만 본다). 파일에 남기는 내용의 해시다.

    올린 PG 정산 줄(`pg_rows`)은 수만 줄이라 매번 글로 바꾸면 느리다(A2A 요청마다 부른다).
    줄 대신 줄 수와 판(`pg_version`, 올리기·빼기·불러오기마다 하나씩)을 넣는다. 줄이 바뀌면 판도
    바뀌므로 지문도 바뀐다.
    """
    data = dump(s)
    data["records"]["pg_rows"] = {
        "count": len(s.records.pg_rows),
        "version": s.records.pg_version,
    }
    text = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def load(s: State, path: Path) -> bool:
    """파일이 있으면 읽어 넣는다. 읽었으면 True."""
    if not path.exists():
        return False
    try:
        restore(s, json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, KeyError, TypeError) as exc:
        broken = path.with_suffix(path.suffix + ".broken")
        path.replace(broken)
        logger.warning(
            "재무 기록 파일을 읽지 못해 %s로 옮기고 빈 상태로 시작한다 (%s)", broken, exc
        )
        return False
    return True


def save(s: State, path: Path, last: str | None = None) -> str:
    """바뀌었으면 쓴다. 쓴(또는 그대로인) 기록의 지문(`fingerprint`)을 돌려준다.

    5초마다 부르므로 가벼운 지문으로 먼저 비교하고, 바뀐 경우에만 전체를 글로 바꿔 쓴다
    (올린 PG 정산 줄이 수만 줄이어도 바뀌지 않은 동안은 다시 쓰지 않는다).
    """
    mark = fingerprint(s)
    if mark != last:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(_text(s), encoding="utf-8")
        tmp.replace(path)
    return mark


def save_companies(last: dict[str, str]) -> dict[str, str]:
    """캣북스 밖 회사 기록을 회사마다 쓴다(바뀐 것만). 회사 → 지문을 돌려준다."""
    marks = dict(last)
    for cid, s in company_states().items():
        path = company_state_file(cid)
        if path is None:
            continue
        try:
            marks[cid] = save(s, path, marks.get(cid))
        except Exception:  # 다음 회차에 다시 쓴다. 한 회사가 막혀도 다른 회사는 쓴다
            logger.exception("%s 재무 기록 저장 중 오류", cid)
    return marks


async def save_loop(get_state: Any, path: Path, last: str | None) -> None:
    others: dict[str, str] = {}
    while True:
        await asyncio.sleep(SAVE_SECONDS)
        try:
            last = save(get_state(), path, last)
        except Exception:  # 다음 회차에 다시 쓴다
            logger.exception("재무 기록 저장 중 오류")
        others = save_companies(others)
