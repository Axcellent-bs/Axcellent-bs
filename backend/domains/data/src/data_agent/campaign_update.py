"""성과 결과를 기획에 보내기 (data → planning `receive_campaign_update`, stage "result").

data 서버 안의 주기 루틴이 캠페인마다 한 번 보낸다. 수집기 프로세스에는 에이전트 이름·안전 관문
(`execute()`)이 없어서, 업무 리포트 일간 저장(`main.start_report_snapshots`)처럼 data 서버가
스스로 시작하는 A2A로 보낸다.

- 언제: 시험 아닌 영상이 모두 7일을 쟀거나(측정 중 0개) 측정 기간(첫 게시 + 7일)이 끝난 캠페인
- 안 보냄(이유를 상태로): 시험 캠페인 · 기획 제안 번호 없음 · 결과 unknown
- 값: `campaign_result`(기획 묶음) 그대로, LLM 안 씀. direction은 매출 판정 기준
  (더 팔림 → 늘었음, 덜 팔림 → 줄었음, 효과 확인 안 됨 → 같음, 표본 적음·측정 불가 → 확인 불가).
  summary는 인사이트 "지난번 결과"와 같은 문장(200자 넘으면 짧은 고정형), facts는 최대 12개
- 방식: 자기 `execute("send_campaign_update")` 뒤
  `call_agent("planning", "receive_campaign_update")`. idempotency_key = "<campaign_id>:result",
  context_id는 캠페인마다 하나 정해 저장하고 재시도 때 같은 값
- 응답: applied·duplicate → 보냄, unknown_proposal → 안 보냄(다시 안 함), 그 밖의 거절 → 안 보냄
  (다시 안 함), 연결 실패·5xx·시간 초과·safety에 닿지 못함 → 실패, 다음 루프에 다시
  (캠페인당 한국 날짜 하루 3번)
- 기능 플래그 `DATA_SEND_CAMPAIGN_UPDATE`(기본 꺼짐). 꺼져 있으면 아무것도 부르지 않고 화면에는
  "자동 보내기 꺼짐"
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_contracts.schemas.data import CampaignResultOutput, CampaignSendStatus
from ax_contracts.schemas.planning import InsightFact, ReceiveCampaignUpdateInput

logger = logging.getLogger("ax.data.campaign_update")

FLAG_ENV = "DATA_SEND_CAMPAIGN_UPDATE"
DB_ENV = "DATA_CAMPAIGN_SEND_DB"
DEFAULT_DB = Path(__file__).resolve().parents[4] / ".data" / "campaign_sends.db"  # backend/.data
ACTION = "send_campaign_update"  # data 자신의 execute() 액션 (safety registry 항목 필요)
TARGET, SKILL = "planning", "receive_campaign_update"
SOURCE = "data.campaign_result"
RETRY_PER_DAY = 3
CAMPAIGN_DAYS = 7
SUMMARY_MAX, FACTS_MAX, LABEL_MAX, VALUE_MAX = 200, 12, 120, 80
EVERY_SECONDS, FIRST_DELAY_SECONDS = 3600, 120
DEADLINE = timedelta(seconds=30)
DIRECTION = {"more": "늘었음", "less": "줄었음", "no_effect": "같음"}
UNKNOWN_DIRECTION = "확인 불가"
RETRYABLE = frozenset({"unreachable", "TIMEOUT"})

Send = Callable[[str, AgentRequest], Awaitable[AgentResult]]
Gate = Callable[..., Awaitable[Any]]


def enabled(env: Mapping[str, str]) -> bool:
    return str(env.get(FLAG_ENV, "")).strip().lower() in {"1", "true", "yes", "on"}


def kst_day(now: datetime) -> date:
    from data_agent.youtube_store import KST

    return now.astimezone(KST).date()


def when(t: datetime) -> str:
    """화면 시각: "11월 12일 14:05" (한국 시각)."""
    from data_agent.youtube_store import KST

    k = t.astimezone(KST)
    return f"{k.month}월 {k.day}일 {k:%H:%M}"


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# -------------------------------------------------------- 보내기 판정·값 (평가 채점기가 부른다)


@dataclass(frozen=True)
class Decision:
    kind: str  # send · wait · skip
    reason: str | None = None
    send_after: datetime | None = None


def decide(out: CampaignResultOutput, now: datetime) -> Decision:
    """캠페인 하나를 지금 보낼지. 시험·제안 번호 없음·unknown은 안 보냄(이유)."""
    if out.test:
        return Decision("skip", "시험 캠페인")
    if not out.proposal_id:
        return Decision("skip", "기획 제안 번호 없음")
    if out.status == "unknown":
        return Decision("skip", out.reason or "성과 결과를 계산하지 못함")
    if out.window_from is None:
        return Decision("wait", None, None)
    end = out.window_from + timedelta(days=CAMPAIGN_DAYS)
    real = [v for v in out.videos if not v.test]
    measuring = any(v.verdict in (None, "measuring") for v in real)
    if (real and not measuring) or now >= end:
        return Decision("send", None, end)
    return Decision("wait", None, end)


def direction_of(out: CampaignResultOutput) -> str:
    verdict = out.sales.verdict if out.sales else None
    if verdict is None or verdict.status != "known" or verdict.low_sample or not verdict.verdict:
        return UNKNOWN_DIRECTION
    return DIRECTION[verdict.verdict]


def summary_of(out: CampaignResultOutput) -> str:
    """"조회 목표의 N배 (영상 M개 중 K개 달성) · 매출 ±N원 · 주문 ±N건" (+ " · 매출 감소 경고").
    인사이트 "지난번 결과"와 같은 문장. 200자를 넘으면 자르지 않고 "<조회 판정> · 매출 <방향>"."""
    from data_agent.past_results import to_past

    past = to_past(out)
    text = past.summary_line
    if any(a.kind == "loss" for a in out.alerts):
        text += " · 매출 감소 경고"
    if len(text) <= SUMMARY_MAX:
        return text
    return f"{past.views_label} · 매출 {direction_of(out)}"


def facts_of(out: CampaignResultOutput) -> list[InsightFact]:
    """근거 숫자 (최대 12, 길이 제한). 값이 없는 칸은 넣지 않고, 마이너스는 그대로, 네 칸은 따로."""
    from data_agent.past_results import to_past

    past = to_past(out)
    verdict = out.sales.verdict if out.sales else None
    rows = [("조회 판정", past.views_label)]
    if past.target_ratio is not None:
        rows.append(("목표 대비 배수 (7일 중앙값)", f"{past.target_ratio:.2f}배"))
    rows.append(("목표 달성 영상", f"{past.videos_achieved}/{past.videos_total}개"))
    rows.append(("매출 판정", past.sales_label))
    if verdict and verdict.incremental_gross_krw is not None:
        rows.append(("증가 매출 (기준선 대비)", f"{verdict.incremental_gross_krw:+,}원"))
    if verdict and verdict.incremental_orders is not None:
        rows.append(("증가 주문 (기준선 대비)", f"{verdict.incremental_orders:+,}건"))
    for cell in out.sales.cells if out.sales else []:
        value = cell.line or f"측정 불가: {cell.reason or '이유 없음'}"
        rows.append((f"매출 칸: {cell.label}", value))
    if verdict and verdict.low_sample:
        rows.append(("표본 적음", verdict.low_sample_reason or "기준선·주문 수가 적음"))
    return [
        InsightFact(label=clip(a, LABEL_MAX), value=clip(b, VALUE_MAX), source=SOURCE)
        for a, b in rows
    ][:FACTS_MAX]


def build_update(out: CampaignResultOutput) -> ReceiveCampaignUpdateInput:
    """기획에 보낼 성과 (stage result). 계약 모델로 만들어 길이·필수 칸을 검증한다."""
    return ReceiveCampaignUpdateInput(
        proposal_id=out.proposal_id or "",
        campaign_id=out.campaign_id,
        stage="result",
        direction=direction_of(out),  # type: ignore[arg-type]  # 네 값 중 하나
        summary=summary_of(out),
        facts=facts_of(out),
        insight_id=out.insight_id,
    )


# ---------------------------------------------------------------- 보내기 상태 저장소


@dataclass
class SendRow:
    campaign_id: str
    state: str = "pending"  # sent · not_sent(기획이 거절, 다시 안 함) · failed
    proposal_id: str | None = None
    direction: str | None = None
    sent_at: datetime | None = None
    failures: int = 0
    fail_day: str | None = None  # 실패를 센 한국 날짜
    reason: str | None = None
    context_id: str | None = None


class SendStore:
    """캠페인마다 보내기 상태 (상태·시각·proposal_id·direction·실패 수·이유·context_id)."""

    COLUMNS = ("campaign_id", "state", "proposal_id", "direction", "sent_at", "failures",
               "fail_day", "reason", "context_id")  # fmt: skip

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS sends (campaign_id TEXT PRIMARY KEY,"
                " state TEXT NOT NULL, proposal_id TEXT, direction TEXT, sent_at TEXT,"
                " failures INTEGER NOT NULL, fail_day TEXT, reason TEXT, context_id TEXT,"
                " updated_at TEXT NOT NULL)"
            )

    def close(self) -> None:
        self._conn.close()

    def get(self, campaign_id: str) -> SendRow | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {', '.join(self.COLUMNS)} FROM sends WHERE campaign_id = ?",  # noqa: S608
                (campaign_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(zip(self.COLUMNS, row, strict=True))
        data["sent_at"] = datetime.fromisoformat(data["sent_at"]) if data["sent_at"] else None
        return SendRow(**data)

    def save(self, row: SendRow) -> None:
        values = [getattr(row, c) for c in self.COLUMNS]
        values[4] = row.sent_at.isoformat() if row.sent_at else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sends VALUES (?,?,?,?,?,?,?,?,?,?)",
                (*values, datetime.now(UTC).isoformat()),
            )


def store_of(env: Mapping[str, str]) -> SendStore:
    return SendStore(env.get(DB_ENV) or DEFAULT_DB)


# ---------------------------------------------------------------- 보내기


def _failures_today(row: SendRow | None, now: datetime) -> int:
    if row is None or row.state != "failed" or row.fail_day != kst_day(now).isoformat():
        return 0
    return row.failures


def _fail(row: SendRow, now: datetime, reason: str) -> None:
    row.failures = _failures_today(row, now) + 1
    row.state, row.fail_day, row.reason = "failed", kst_day(now).isoformat(), reason


def _new_context() -> str:
    # data가 스스로 시작하는 사건 번호 (업무 리포트 일간 저장의 ctx_req_data_snapshot과 같은 모양)
    return f"ctx_req_data_result_{uuid.uuid4().hex[:12]}"


async def send_one(
    out: CampaignResultOutput, store: SendStore, now: datetime, *, send: Send, gate: Gate
) -> bool:
    """보낼 차례인 캠페인 하나를 보낸다. 기획을 불렀으면 True."""
    row = store.get(out.campaign_id) or SendRow(out.campaign_id)
    row.context_id = row.context_id or _new_context()
    row.proposal_id = out.proposal_id
    store.save(row)  # 재시도 때도 같은 context_id
    try:
        update = build_update(out)
    except (ValidationError, ValueError) as error:
        row.state, row.reason = "not_sent", f"계약 검증 실패: {str(error)[:120]}"
        store.save(row)
        return False
    key = f"{out.campaign_id}:result"
    meta = RequestMeta(
        context_id=row.context_id,
        token="internal:data",  # call_agent가 data 토큰으로 바꿔 보낸다
        tenant_id=TENANT_ID_DEFAULT,
        deadline=datetime.now(UTC) + DEADLINE,
        idempotency_key=key,
        requester="data",
    )
    decision = await gate(
        ACTION,
        {"campaign_id": out.campaign_id, "proposal_id": out.proposal_id,
         "direction": update.direction},  # fmt: skip
        meta,
        Evidence(
            ref_ids=[f"campaign:{out.campaign_id}", f"proposal:{out.proposal_id}"],
            data_summary=f"{out.campaign_id} 성과 → 기획 {out.proposal_id} ({update.direction})",
        ),
    )
    if decision.status != "executed":
        _fail(row, now, f"safety가 실행하지 않음 ({decision.status})")
        store.save(row)
        return False
    reply = await send(TARGET, AgentRequest(meta=meta, skill=SKILL,
                                            input=update.model_dump(mode="json")))  # fmt: skip
    outcome = reply.outcome or reply.status.value
    if reply.status == TaskStatus.COMPLETED and outcome in ("applied", "duplicate"):
        row.state, row.reason, row.failures = "sent", None, 0
        row.direction, row.sent_at = update.direction, now
    elif reply.status == TaskStatus.COMPLETED and outcome == "unknown_proposal":
        row.state, row.reason = "not_sent", "기획에 없는 제안"
    elif outcome in RETRYABLE or outcome.startswith("http_5"):
        _fail(row, now, f"기획에 닿지 못함 ({outcome})")
    else:
        row.state, row.reason = "not_sent", f"기획이 거절: {outcome}"
    store.save(row)
    return True


async def send_ready(
    outs: Sequence[CampaignResultOutput],
    store: SendStore,
    env: Mapping[str, str],
    now: datetime,
    *,
    send: Send | None = None,
    gate: Gate | None = None,
) -> int:
    """보낼 차례인 캠페인을 보낸다. 기획을 부른 횟수. 플래그가 꺼져 있으면 0 (아무것도 안 함)."""
    if not enabled(env):
        return 0
    if send is None:
        from ax_agent_template.client import call_agent as send
    if gate is None:
        from ax_agent_template.execute import execute as gate
    calls = 0
    for out in outs:
        row = store.get(out.campaign_id)
        if row is not None and row.state in ("sent", "not_sent"):
            continue  # 캠페인당 1번, 거절은 다시 안 함
        if decide(out, now).kind != "send":
            continue
        if row is not None and _failures_today(row, now) >= RETRY_PER_DAY:
            continue  # 오늘은 3번 다 실패 → 한국 날짜가 바뀌면 다시
        calls += await send_one(out, store, now, send=send, gate=gate)
    return calls


# -------------------------------------------------------- 화면 상태 (campaign_result send_status)


def send_status(
    out: CampaignResultOutput, store: SendStore | None, env: Mapping[str, str], now: datetime
) -> CampaignSendStatus:
    if not enabled(env):
        return CampaignSendStatus(state="off", chip="자동 보내기 꺼짐",
                                  line="safety 확인 후 켭니다")  # fmt: skip
    row = store.get(out.campaign_id) if store is not None else None
    if row is not None and row.state == "sent":
        line = f'{row.proposal_id} · "{row.direction}"' + (
            f" · {when(row.sent_at)}" if row.sent_at else "")  # fmt: skip
        return CampaignSendStatus(state="sent", chip="기획에 보냄", line=line,
                                  proposal_id=row.proposal_id, direction=row.direction,
                                  sent_at=row.sent_at)  # fmt: skip
    if row is not None and row.state == "not_sent":
        return CampaignSendStatus(state="not_sent", chip="보내지 않음", line=row.reason or "",
                                  proposal_id=row.proposal_id, reason=row.reason)  # fmt: skip
    d = decide(out, now)
    if d.kind == "skip":
        return CampaignSendStatus(state="not_sent", chip="보내지 않음", line=d.reason or "",
                                  proposal_id=out.proposal_id, reason=d.reason)  # fmt: skip
    if row is not None and row.state == "failed":
        n = _failures_today(row, now)
        chip = f"보내기 실패 · 다시 시도 중 ({n}/{RETRY_PER_DAY})"
        return CampaignSendStatus(state="failed", chip=chip,
                                  line=row.reason or "", proposal_id=out.proposal_id, failures=n,
                                  retry_limit=RETRY_PER_DAY)  # fmt: skip
    line = (f"7일 측정이 끝나는 {when(d.send_after)} 이후 자동으로 보냅니다" if d.send_after
            else "7일 측정이 끝나면 자동으로 보냅니다")  # fmt: skip
    return CampaignSendStatus(state="pending", chip="기획에 보낼 예정", line=line,
                              proposal_id=out.proposal_id, send_after=d.send_after)  # fmt: skip


def read_status(
    out: CampaignResultOutput, env: Mapping[str, str], now: datetime
) -> CampaignSendStatus:
    """campaign_result 응답용: 저장소 파일이 없으면 만들지 않고 판정만."""
    path = env.get(DB_ENV) or DEFAULT_DB
    store = SendStore(path) if str(path) == ":memory:" or Path(path).is_file() else None
    try:
        return send_status(out, store, env, now)
    finally:
        if store is not None:
            store.close()


# ---------------------------------------------------------------- data 서버 주기 루틴


async def tick(env: Mapping[str, str], now: datetime | None = None, **kwargs: Any) -> int:
    """캠페인마다 성과 결과(기획 묶음)를 계산해 보낼 차례면 보낸다. 플래그가 꺼져 있으면 0."""
    if not enabled(env):
        return 0
    from ax_contracts.schemas.data import CampaignResultRequest
    from data_agent.skills import campaign_result as cr
    from data_agent.skills.video_performance import DB_ENV as PERF_ENV
    from data_agent.skills.video_performance import DEFAULT_DB as PERF_DB
    from data_agent.skills.video_performance import VideoStore

    now = now or datetime.now(UTC)
    path = env.get(PERF_ENV) or PERF_DB
    if str(path) != ":memory:" and not Path(path).is_file():
        return 0  # 등록된 영상이 없다 (캠페인 없음)
    videos = VideoStore(path)
    try:
        ids = list(dict.fromkeys(v.campaign_id for v in videos.videos(include_test=True)
                                 if v.campaign_id))  # fmt: skip
    finally:
        videos.close()
    if not ids:
        return 0
    try:
        from data_agent.handlers import get_campaign_connector

        connector = get_campaign_connector()
    except NotImplementedError:
        connector = None
    outs = []
    for cid in ids:
        request = CampaignResultRequest(campaign_id=cid, view="planning")
        outs.append((await cr.campaign_result(request, connector, env=env, now=now)).output)
    store = store_of(env)
    try:
        return await send_ready(outs, store, env, now, **kwargs)
    finally:
        store.close()


async def run_forever(env: Mapping[str, str] | None = None) -> None:
    """한 시간마다 보낼 차례인 캠페인을 보낸다 (data 서버 lifespan). 실패해도 다음 시간에 다시."""
    env = os.environ if env is None else env
    await asyncio.sleep(FIRST_DELAY_SECONDS)
    while True:
        try:
            calls = await tick(env)
            if calls:
                logger.info("기획에 성과 결과 보내기: 호출 %d번", calls)
        except Exception:  # noqa: BLE001 - 한 번 실패해도 루틴은 계속
            logger.exception("기획에 성과 결과 보내기 실패")
        await asyncio.sleep(EVERY_SECONDS)
