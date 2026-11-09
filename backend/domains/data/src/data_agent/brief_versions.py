"""인사이트 브리프 판과 "다시 추천" (인사이트 탭 맨 위 줄).

- 판: 같은 날 브리프를 지우지 않고 `brief_versions`에 쌓는다 (`BriefStore.put`). 판 목록·최신 판은
  (한국 날짜, 지금 규칙 버전) 기준. `briefs` 표에는 그날 최신 판 (수집기·대화 호환).
  0시 이후 첫 자동 브리프는 지금처럼 만들고 trigger "auto" (다시 추천 횟수에 안 셈).
- 다시 추천(`start`): 막는 조건을 서버가 보고, 통과하면 "만드는 중" 잠금을 걸고 바로 started를
  돌려준 뒤 data 서버 안에서 백그라운드로 만든다 (A2A 시간 제한에 안 걸림).
  화면은 status를 2초마다 읽음.
- 막는 조건 (모두 횟수 차감 없음):
  만드는 중(누가 눌렀든, 자동 포함) → in_progress · 하루 3번(한국 0시 초기화) → daily_limit ·
  마지막 판(자동 포함) 뒤 1시간 → cooldown · LLM 오늘 70% 이상 → llm_budget ·
  마지막 판과 감지 스냅샷이 같음 → no_new_data
- 잠금: 같은 sqlite 파일(`brief_lock`, 서버·수집기 공용, #835 "계산 중" 표시와 같은 방식).
  만료 10분(`DATA_BRIEF_LOCK_SECONDS`): 만든 프로세스가 죽어도 10분 뒤 풀린다.
  0시 자동 브리프도 같은 잠금
- 실패하면 이전 판이 그대로 최신, 횟수 차감 없음, 이유는 status로.
- 공통 로그에 다시 추천 시작/끝·누가·몇 토큰 (`event_published`)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import statistics
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import (
    BriefBuilding,
    BriefDiff,
    BriefRegenBlocked,
    BriefRegenJob,
    BriefVersionInfo,
    TrendInsightBriefOutput,
)
from data_agent import llm_budget

_log = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
LOCK_ENV = "DATA_BRIEF_LOCK_SECONDS"
DEFAULT_LOCK_SECONDS = 600  # 10분
DAILY_REGEN = 3
COOLDOWN = timedelta(hours=1)
LLM_MAX_SHARE = 0.70


def lock_seconds(env: Mapping[str, str] | None = None) -> float:
    raw = (os.environ if env is None else env).get(LOCK_ENV)
    try:
        return max(1.0, float(raw)) if raw else DEFAULT_LOCK_SECONDS
    except ValueError:
        return DEFAULT_LOCK_SECONDS


def hhmm(at: datetime) -> str:
    return at.astimezone(KST).strftime("%H:%M")


def _josa(name: str) -> str:
    """이/가 (받침이 있으면 이)."""
    last = name[-1:] if name else ""
    if "가" <= last <= "힣":
        return "이" if (ord(last) - 0xAC00) % 28 else "가"
    return "이(가)"


def label(row: Mapping[str, Any]) -> str:
    """판 이름: "00:27 자동" · "11:16 다시 추천"."""
    at = row["created_at"]
    at = datetime.fromisoformat(at) if isinstance(at, str) else at
    return f"{hhmm(at)} {'자동' if row['trigger'] == 'auto' else '다시 추천'}"


# ---------------------------------------------------------------- 감지 스냅샷


@dataclass(frozen=True)
class Detect:
    """브리프가 쓰는 감지 데이터의 시점. key가 같으면 새로 만들 게 없다."""

    as_of: datetime | None
    key: str


def _max_at(path: str | Path, sql: str) -> str | None:
    if not Path(path).is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            row = conn.execute(sql).fetchone()
        finally:
            conn.close()
    except sqlite3.DatabaseError:
        return None
    return row[0] if row else None


def detect_stamp(env: Mapping[str, str]) -> Detect:
    """마지막 YouTube 스냅샷·인기 검색어 스냅샷 시각 + 오늘 판정판 (계산하지 않고 읽기만)."""
    from data_agent.skills import trend_candidates as cand_mod
    from data_agent.today_board import BOARD
    from data_agent.youtube_store import DB_ENV as YT_ENV
    from data_agent.youtube_store import DEFAULT_DB

    yt = _max_at(env.get(YT_ENV) or str(DEFAULT_DB), "SELECT MAX(collected_at) FROM snapshots")
    tr = _max_at(cand_mod.trending_db(env), "SELECT MAX(collected_at) FROM trending")
    try:
        board = BOARD.stamp()
    except Exception:  # noqa: BLE001 - 판정판을 못 읽으면 스냅샷 시각만으로
        board = ""
    times = [datetime.fromisoformat(t) for t in (yt, tr) if t]
    as_of = max(times) if times else None
    return Detect(as_of=as_of, key=json.dumps([yt, tr, board], ensure_ascii=False))


# ---------------------------------------------------------------- 막는 조건


@dataclass(frozen=True)
class Blocked:
    code: str
    text: str


def _store(env: Mapping[str, str]):  # noqa: ANN202
    from data_agent.skills import trend_insight_brief as tib

    return tib.BriefStore(env.get(tib.DB_ENV) or tib.DEFAULT_DB)


def _day_rules(env: Mapping[str, str], now: datetime) -> tuple[Any, str]:
    from data_agent.skills import trend_insight_brief as tib

    return tib.today_kst(now), tib.rules_version(tib.load_config(env))


def used(rows: list[dict[str, Any]]) -> int:
    return sum(1 for r in rows if r["trigger"] == "manual")


def regen_used(env: Mapping[str, str], now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    day, rules = _day_rules(env, now)
    store = _store(env)
    try:
        return used(store.versions(day, rules))
    finally:
        store.close()


def building_text(b: Mapping[str, Any]) -> str:
    return f"만드는 중… (시작 {hhmm(b['started_at'])})"


def blocked(store: Any, day: Any, rules: str, now: datetime, *, llm_share: float,
            detect: Detect) -> Blocked | None:  # fmt: skip
    """지금 다시 추천을 누르면 막히는 이유 (통과면 None)."""
    b = store.building(now)
    if b is not None:
        who = "자동으로" if b["trigger"] == "auto" else f"{b['who']}{_josa(b['who'])}"
        at = hhmm(b["started_at"])
        return Blocked("in_progress", f"지금 만드는 중이에요 ({who} {at}에 시작)")
    rows = store.versions(day, rules)
    if used(rows) >= DAILY_REGEN:
        return Blocked("daily_limit", f"오늘 다시 추천 {DAILY_REGEN}번을 다 썼어요")
    if rows:
        last = rows[-1]
        after = datetime.fromisoformat(last["created_at"]) + COOLDOWN
        if now < after:
            return Blocked("cooldown", f"{hhmm(after)} 이후에 다시 추천할 수 있어요")
    if llm_share >= LLM_MAX_SHARE:
        return Blocked("llm_budget", f"LLM 오늘 {llm_share * 100:.0f}% · 다시 추천은 "
                                     f"{LLM_MAX_SHARE * 100:.0f}% 미만일 때만")  # fmt: skip
    if rows and rows[-1]["detect_key"] == detect.key:
        since = hhmm(detect.as_of) if detect.as_of else hhmm(datetime.fromisoformat(
            rows[-1]["created_at"]))  # fmt: skip
        return Blocked("no_new_data", f"감지 데이터가 {since} 이후 그대로라 새로 만들 게 없어요")
    return None


# ---------------------------------------------------------------- 다시 추천 (백그라운드)


@dataclass
class BuildContext:
    """만들기 함수가 받는 것: 저장할 때 `save`(판 번호). 만들기는 잠금을 이미 가진 채로 돈다."""

    store_path: str
    day: Any
    rules: str
    trigger: str
    requested_by: str | None
    detect: Detect
    lock_token: str | None
    now: datetime | None = None
    version: int | None = None
    tokens: int | None = None
    _box: list[int] = field(default_factory=lambda: [0])

    def save(self, out: TrendInsightBriefOutput, tokens: int | None = None,
             expires_at: datetime | None = None) -> int:  # fmt: skip
        from data_agent.skills import trend_insight_brief as tib

        store = tib.BriefStore(self.store_path)
        try:
            self.tokens = tokens if tokens is not None else self._box[0]
            self.version = store.put(
                self.day, self.rules, out, self.now or datetime.now(UTC), expires_at,
                trigger=self.trigger, requested_by=self.requested_by,
                detect_as_of=self.detect.as_of, detect_key=self.detect.key, tokens=self.tokens,
            )  # fmt: skip
        finally:
            store.close()
        return self.version


Build = Callable[[BuildContext], Awaitable[None]]
_PENDING: set[asyncio.Task[None]] = set()


def _event(event: str, **payload: Any) -> None:
    """공통 로그에 한 줄 (저장소가 없으면 건너뜀)."""
    try:
        from ax_agent_template import common_log
        from ax_contracts.data_access.common_log import LogRecord

        store = common_log.get_store()
        if store is None:
            return
        store.write(LogRecord(
            log_id=f"log_{uuid.uuid4().hex}", occurred_at=datetime.now(UTC), source="data",
            kind="event_published", skill="trend_insight_brief",
            payload={"event": event, **payload},
        ))  # fmt: skip
    except Exception as error:  # noqa: BLE001 - 로그 실패로 다시 추천을 멈추지 않는다
        _log.warning("공통 로그를 남기지 못함: %s", type(error).__name__)


def start(env: Mapping[str, str], requested_by: str | None, build: Build, *,
          now: datetime | None = None, llm_share: float | None = None,
          detect: Detect | None = None) -> dict[str, Any]:  # fmt: skip
    """다시 추천 시작. {"status": "started"|"blocked", "job_id", "blocked": {code, text}}.

    `now`를 주면(평가·테스트) 새 판 시각도 그 값, 안 주면 저장하는 순간의 시각."""
    given = now
    now = now or datetime.now(UTC)
    day, rules = _day_rules(env, now)
    share = llm_budget.BUDGET.usage(env).share() if llm_share is None else llm_share
    detect = detect or detect_stamp(env)
    store = _store(env)
    try:
        why = blocked(store, day, rules, now, llm_share=share, detect=detect)
        token = None if why else store.claim_build(requested_by or "이름 없음", "manual", now,
                                                  lock_seconds(env))  # fmt: skip
        if why is None and token is None:  # 막는 조건을 본 사이 다른 쪽이 잠금을 걸었다
            why = blocked(store, day, rules, now, llm_share=share, detect=detect) or Blocked(
                "in_progress", "지금 만드는 중이에요")
        if why is not None:
            return {"status": "blocked", "job_id": None,
                    "blocked": {"code": why.code, "text": why.text}}  # fmt: skip
        job_id = f"regen_{uuid.uuid4().hex[:12]}"
        store.job_start(job_id, day, rules, requested_by, now)
        path = str(env.get(_db_env()) or _default_db())
    finally:
        store.close()
    ctx = BuildContext(path, day, rules, "manual", requested_by, detect, token, now=given)
    _event("brief_regen_started", job_id=job_id, requested_by=requested_by)
    task = asyncio.get_running_loop().create_task(_run(ctx, build, job_id))
    _PENDING.add(task)
    task.add_done_callback(_PENDING.discard)
    return {"status": "started", "job_id": job_id, "blocked": None}


def _db_env() -> str:
    from data_agent.skills import trend_insight_brief as tib

    return tib.DB_ENV


def _default_db() -> Path:
    from data_agent.skills import trend_insight_brief as tib

    return tib.DEFAULT_DB


async def _run(ctx: BuildContext, build: Build, job_id: str) -> None:
    from data_agent.skills import trend_insight_brief as tib

    reason: str | None = None
    try:
        with llm_budget.counting() as box:
            ctx._box = box
            await build(ctx)
        if ctx.version is None:
            reason = "새 판을 저장하지 못했어요 (재료가 없거나 LLM 호출이 실패)"
    except Exception as error:  # noqa: BLE001 - 실패하면 이전 판 그대로, 이유만 남긴다
        reason = f"다시 추천을 만들지 못했어요 ({type(error).__name__})"
    store = tib.BriefStore(ctx.store_path)
    try:
        at = datetime.now(UTC)
        if reason is None:
            store.job_finish(job_id, "done", at, version=ctx.version, tokens=ctx.tokens)
        else:
            store.job_finish(job_id, "failed", at, tokens=box[0], reason=reason)
        if ctx.lock_token:
            store.release_build(ctx.lock_token)
    finally:
        store.close()
    _event("brief_regen_finished" if reason is None else "brief_regen_failed", job_id=job_id,
           requested_by=ctx.requested_by, version=ctx.version,
           tokens=ctx.tokens if reason is None else box[0], reason=reason)  # fmt: skip


async def wait_all() -> None:
    """진행 중인 다시 추천이 끝나기를 기다린다 (테스트용)."""
    while _PENDING:
        await asyncio.gather(*list(_PENDING), return_exceptions=True)


def status(env: Mapping[str, str], now: datetime | None = None) -> dict[str, Any]:
    """오늘 마지막 다시 추천 작업 {state, reason, version, job_id, requested_by, started_at}."""
    now = now or datetime.now(UTC)
    day, rules = _day_rules(env, now)
    store = _store(env)
    try:
        job = store.last_job(day, rules)
    finally:
        store.close()
    return job or {"state": None}


# ---------------------------------------------------------------- 응답 칸 채우기


def diff(prev: TrendInsightBriefOutput, cur: TrendInsightBriefOutput) -> dict[str, list[str]]:
    """앞 판 대비 새 주제·빠진 주제 (순위 순)."""
    before = [i.topic for i in prev.insights]
    after = [i.topic for i in cur.insights]
    return {"new": [t for t in after if t not in before],
            "gone": [t for t in before if t not in after]}  # fmt: skip


def estimate(store: Any, rows: list[dict[str, Any]], token_cap: int) -> tuple[float | None, str | None, bool]:  # noqa: E501
    """다시 추천 한 번에 드는 LLM % (최근 다시 추천 실제 토큰 중앙값 ÷ 하루 한도).
    기록이 없으면 오늘 첫 자동 브리프 토큰으로 추정 (estimated)."""
    manual = store.manual_tokens()
    if manual:
        pct = statistics.median(manual) / token_cap * 100
        return round(pct, 1), f"최근 다시 추천 {len(manual)}번의 토큰 중앙값", False
    auto = next((r["tokens"] for r in rows if r["trigger"] == "auto" and r["tokens"]), None)
    if auto:
        return round(auto / token_cap * 100, 1), "오늘 첫 자동 브리프 토큰으로 추정", True
    return None, None, True


def decorate(out: TrendInsightBriefOutput, env: Mapping[str, str], now: datetime | None = None,
             *, version: int | None = None) -> TrendInsightBriefOutput:  # fmt: skip
    """판 목록·판 이름·다시 추천 상태·diff·예상 LLM %를 채운다 (저장분만 읽음)."""
    now = now or datetime.now(UTC)
    day, rules = _day_rules(env, now)
    store = _store(env)
    try:
        rows = store.versions(day, rules)
        if version is not None and (picked := store.version(day, rules, version)) is not None:
            out = picked
        usage = llm_budget.BUDGET.usage(env)
        detect = detect_stamp(env)
        why = blocked(store, day, rules, now, llm_share=usage.share(), detect=detect)
        b = store.building(now)
        job = store.last_job(day, rules)
        mine = next((r for r in rows if r["version"] == out.version), rows[-1] if rows else None)
        prev = None
        if mine is not None and mine["version"] > 1:
            prev = store.version(day, rules, mine["version"] - 1)
        pct, basis, guessed = estimate(store, rows, usage.token_cap)
    finally:
        store.close()
    update: dict[str, Any] = {
        "versions": [BriefVersionInfo(
            version=r["version"], label=label(r), created_at=r["created_at"],
            trigger=r["trigger"], requested_by=r["requested_by"], detect_as_of=r["detect_as_of"],
        ) for r in rows],  # fmt: skip
        "latest_version": rows[-1]["version"] if rows else None,
        "regen_used": used(rows),
        "regen_limit": DAILY_REGEN,
        "regen_blocked": BriefRegenBlocked(code=why.code, text=why.text) if why else None,
        "building": BriefBuilding(who=b["who"], trigger=b["trigger"], started_at=b["started_at"],
                                  text=building_text(b)) if b else None,  # fmt: skip
        "regen_job": BriefRegenJob(**job) if job else None,
        "estimated_llm_pct": pct,
        "estimated_llm_basis": basis,
        "estimated": guessed,
        "llm_pct_now": round(usage.share() * 100, 1),
        "detect_latest_at": detect.as_of,
    }
    if mine is not None:
        update |= {
            "version": mine["version"],
            "version_label": label(mine),
            "created_at": mine["created_at"],
            "trigger": mine["trigger"],
            "requested_by": mine["requested_by"],
            "detect_as_of": mine["detect_as_of"],
        }
    if mine is not None and prev is not None:
        d = diff(prev, out)
        prev_row = next(r for r in rows if r["version"] == mine["version"] - 1)
        update["diff_from_previous"] = BriefDiff(
            from_version=prev_row["version"], from_label=label(prev_row), new_topics=d["new"],
            gone_topics=d["gone"], new_count=len(d["new"]), gone_count=len(d["gone"]),
        )  # fmt: skip
    # 검증을 거쳐 만든다 (저장된 시각 글자 → 시각, 칸 모양 확인)
    return TrendInsightBriefOutput.model_validate(out.model_dump() | update)
