"""data LLM 오류 원인 분류와 호출 중단. 멈춘 동안은 LLM을 아예 부르지 않는다.

- key_error: 공급자가 401·403 → 그날(한국 0시까지) 중단. 프로세스 메모리에만 둔다
- provider_limit: 429 → 1시간 뒤 다시 시도. 3번 연속 429면 그날 중단. 성공하면 연속 수 초기화.
  서버·수집기가 같이 보도록 `llm_budget`과 같은 SQLite 파일에 둔다 (없으면 메모리)
- our_cap: 우리 하루 상한(`llm_budget`) 도달. 셈 방식은 그대로, 상태 표시만
- request_error: 400 → 같은 요청(같은 모델·응답 형식)만 그날(한국 0시까지) 다시 보내지 않는다.
  다른 응답 형식 호출은 계속 한다. 공급자 오류 메시지 앞 200자(키·토큰 모양은 가림)를 상태에 둔다.
  설정을 고치면 재시작하므로 프로세스 메모리에만 둔다
- 화면 상태 우선순위: key_error → request_error → provider_limit → our_cap
- 멈춘 동안 부르면 `LLMHalted`(= LLMNotConfiguredError): 설정 없음·상한과 같은 대체 길(고정 문장·
  unknown)로 간다. 실패 호출로 하루 셈이 차지 않는다
- 상태(`status`)에는 키 이름(key_source)·모델 이름·base_url 호스트만 담는다. 키 값은 담지 않는다

여러 회사 6+ (2026-11-23): 멈춤을 회사마다 나눈다 (한 회사 키 401이 다른 회사를 멈추지 않게).
- key_error는 **쓰는 키** 단위(`llm_settings.key_scope()`): 회사 키면 그 회사·그 키만 멈추고,
  회사 키를 바꾸면 바로 풀린다(재시작 필요 없음). 서버 키면 `server` 하나라 서버 키를 쓰는
  회사들만 같이 멈춘다
- request_error·provider_limit은 요청 회사(`company_scope.active()`) 단위
- provider_limit 저장: 회사 칸이 있는 `llm_limit_company` 표에 쓴다. 옛 `llm_limit`(한 줄) 기록은
  캣북스 것으로 읽고, 캣북스를 새로 쓸 때 옛 줄을 지운다 (옛 파일을 그대로 연다)
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from ax_agent_template.llm import LLMNotConfiguredError
from ax_contracts.schemas.data import LlmStatus
from data_agent import company_scope

logger = logging.getLogger(__name__)
KST = timezone(timedelta(hours=9))
KEY_ERROR, PROVIDER_LIMIT, OUR_CAP = "key_error", "provider_limit", "our_cap"
REQUEST_ERROR = "request_error"
REQUEST_TEXT = (
    "LLM 요청 형식 오류 (400)",
    "공급자가 응답 형식(스키마)을 받지 않았습니다. 모델: {model} · 같은 요청은 오늘 다시 보내지"
    " 않습니다. 모델·주소 설정을 확인하세요.",
)
RETRY_AFTER = timedelta(hours=1)
LIMIT_STRIKES = 3
KEY_TEXT = (
    "LLM 키 오류 (401·403)",
    "쓰는 키: {source} · 오늘은 호출을 멈췄습니다. 키를 넣고 data를 재시작하세요.",
)
# 회사 키를 쓰다 401·403: 그 회사만 멈췄고, 키를 바꾸면 바로 다시 쓴다
COMPANY_KEY_TEXT = (
    "{source} · 이 회사만 오늘 호출을 멈췄어요. 시스템 관리 › 회사 연결 › 공통 AI 도구 연결에서"
    " 키를 바꾸면 바로 다시 써요."
)
LIMIT_TEXT = (
    "LLM 공급자 한도 초과 (429)",
    "공급자 쪽 하루 한도에 닿았습니다. 1시간 뒤 다시 시도하고, 3번 연속이면 오늘은 멈춥니다.",
)
CAP_TEXT = (
    "우리 하루 상한 도달",
    "정상 호출 {calls}/{call_cap}회 · 토큰 {tokens:,}/{token_cap:,} · 0시에 초기화됩니다.",
)


class LLMHalted(LLMNotConfiguredError):
    """오류 원인 때문에 지금은 LLM을 부르지 않음 (key_error·request_error·provider_limit)."""


def _now() -> datetime:
    return datetime.now(UTC)


def next_midnight(now: datetime) -> datetime:
    """다음 한국 0시 (UTC)."""
    day = now.astimezone(KST).date() + timedelta(days=1)
    return datetime(day.year, day.month, day.day, tzinfo=KST).astimezone(UTC)


@dataclass(frozen=True)
class Halt:
    reason: str
    until: datetime
    strikes: int = 0  # 연속 429 수
    request: str | None = None  # request_error: 막힌 응답 형식 이름
    model: str | None = None  # request_error: 그 요청의 모델
    message: str | None = None  # request_error: 공급자 메시지 앞 200자 (가림)


def request_key(model: str | None, request: str) -> str:
    """같은 요청의 기준: 모델 + 응답 형식(스키마) 이름."""
    return f"{model or '-'}|{request}"


def _scope(company: str | None, key_scope: str | None) -> tuple[str, str]:
    """(회사, 키 단위). 안 넘기면 지금 요청의 회사와 그 회사가 쓰는 키."""
    from data_agent import llm_settings

    cid = company_scope.scope_id(company) if company else company_scope.active()
    if key_scope is None:
        with company_scope.using(cid):
            key_scope = llm_settings.key_scope()
    return cid, key_scope


class HaltStore:
    """중단 상태. 회사마다 따로.

    key_error·request_error는 메모리, provider_limit은 SQLite(있으면)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._key_until: dict[str, datetime] = {}  # 키 단위 → 중단 끝 (메모리)
        self._bad: dict[tuple[str, str], Halt] = {}  # (회사, 요청 기준) → 중단 (메모리)
        self._mem: dict[str, tuple[int, datetime]] = {}  # 회사 → (연속 429, 다시 시도할 시각)
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                # 옛 한 줄 표(그대로 둔다, 캣북스 것으로 읽는다)와 회사 칸이 있는 새 표
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS llm_limit (id INTEGER PRIMARY KEY"
                    " CHECK (id = 1), strikes INTEGER NOT NULL, until TEXT NOT NULL)"
                )
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS llm_limit_company (tenant_id TEXT PRIMARY KEY,"
                    " strikes INTEGER NOT NULL, until TEXT NOT NULL)"
                )

    def _limit(self, cid: str) -> tuple[int, datetime] | None:
        if self._conn is None:
            return self._mem.get(cid)
        row = self._conn.execute(
            "SELECT strikes, until FROM llm_limit_company WHERE tenant_id = ?", (cid,)
        ).fetchone()
        if row is None and cid == company_scope.CATBOOKS:  # 옛 기록(회사 칸 없음)은 캣북스
            row = self._conn.execute("SELECT strikes, until FROM llm_limit WHERE id = 1").fetchone()
        return (int(row[0]), datetime.fromisoformat(row[1])) if row else None

    def _set_limit(self, cid: str, value: tuple[int, datetime] | None) -> None:
        if self._conn is None:
            if value is None:
                self._mem.pop(cid, None)
            else:
                self._mem[cid] = value
            return
        with self._conn:
            if cid == company_scope.CATBOOKS:
                self._conn.execute("DELETE FROM llm_limit")  # 옛 줄은 새 표로 옮겨 쓴다
            if value is None:
                self._conn.execute("DELETE FROM llm_limit_company WHERE tenant_id = ?", (cid,))
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO llm_limit_company VALUES (?, ?, ?)",
                    (cid, value[0], value[1].isoformat()),
                )

    def active(
        self, now: datetime | None = None, *, company: str | None = None,
        key_scope: str | None = None,
    ) -> Halt | None:  # fmt: skip
        """화면 상태용: key_error → request_error(가장 최근) → provider_limit. 없으면 None."""
        now = now or _now()
        cid, scope = _scope(company, key_scope)
        with self._lock:
            key_until = self._key_until.get(scope)
            if key_until is not None and now < key_until:
                return Halt(KEY_ERROR, key_until)
            bad = [h for (c, _), h in self._bad.items() if c == cid and now < h.until]
            limit = self._limit(cid)
        if bad:
            return bad[-1]
        if limit is not None and now < limit[1]:
            return Halt(PROVIDER_LIMIT, limit[1], limit[0])
        return None

    def check(
        self, now: datetime | None = None, request: str | None = None, *,
        company: str | None = None, key_scope: str | None = None,
    ) -> None:  # fmt: skip
        """멈춘 상태면 부르지 않고 `LLMHalted`. request_error는 같은 요청만 막는다."""
        now = now or _now()
        cid, scope = _scope(company, key_scope)
        with self._lock:
            key_until = self._key_until.get(scope)
            key_until = key_until if key_until is not None and now < key_until else None
            bad = self._bad.get((cid, request)) if request else None
            bad = bad if bad is not None and now < bad.until else None
            limit = self._limit(cid)
        limit_until = limit[1] if limit is not None and now < limit[1] else None
        for title, until in ((KEY_TEXT[0], key_until), (REQUEST_TEXT[0], bad and bad.until),
                             (LIMIT_TEXT[0], limit_until)):  # fmt: skip
            if until is not None:
                raise LLMHalted(f"{title}: {until.isoformat()}까지 LLM을 부르지 않음")

    def record_error(
        self, status_code: int | None, now: datetime | None = None, *,
        request_key: str | None = None, message: str | None = None, model: str | None = None,
        company: str | None = None, key_scope: str | None = None,
    ) -> Halt | None:  # fmt: skip
        """공급자 오류를 원인별로 기록한다. 400·401·403·429가 아니면 None (중단하지 않음)."""
        now = now or _now()
        cid, scope = _scope(company, key_scope)
        if status_code == 400 and request_key:
            name = request_key.split("|", 1)[-1]
            halt = Halt(REQUEST_ERROR, next_midnight(now), request=name, model=model,
                        message=(message or "")[:200] or None)  # fmt: skip
            with self._lock:
                self._bad.pop((cid, request_key), None)
                self._bad[(cid, request_key)] = halt  # 가장 최근 것이 끝으로
            logger.warning("data LLM request_error (400, %s, 회사 %s): 한국 0시까지 같은 요청 중단",
                           name, cid)  # fmt: skip
            return halt
        if status_code in (401, 403):
            with self._lock:
                self._key_until[scope] = next_midnight(now)
            who = "서버 키" if scope.startswith("server") else f"회사 {cid} 키"
            logger.warning("data LLM key_error (%s, %s): 한국 0시까지 호출 중단", status_code, who)
            return Halt(KEY_ERROR, next_midnight(now))
        if status_code == 429:
            with self._lock:
                old = self._limit(cid)
                strikes = (old[0] if old else 0) + 1
                until = next_midnight(now) if strikes >= LIMIT_STRIKES else now + RETRY_AFTER
                self._set_limit(cid, (strikes, until))
            logger.warning(
                "data LLM provider_limit (429, 연속 %d번, 회사 %s): %s까지 호출 중단",
                strikes,
                cid,
                until.isoformat(),
            )
            return Halt(PROVIDER_LIMIT, until, strikes)
        return None

    def record_success(self, *, company: str | None = None) -> None:
        """성공하면 그 회사의 연속 429 수를 지운다."""
        cid = company_scope.scope_id(company) if company else company_scope.active()
        with self._lock:
            if self._limit(cid) is not None:
                self._set_limit(cid, None)


def _store_path() -> str | None:
    from data_agent.llm_budget import DB_ENV

    return os.environ.get(DB_ENV) or None


HALT = HaltStore(_store_path())


def key_source(env: Mapping[str, str]) -> str:
    """쓰는 키의 이름 (값이 아님). 회사 키면 "<회사> 회사 키(…끝4자리)", 아니면 서버 키 이름."""
    from data_agent import llm_settings

    return llm_settings.source(env)[1]


def status(env: Mapping[str, str] | None = None, now: datetime | None = None) -> LlmStatus:
    """지금(요청 회사) data LLM 상태: 중단 원인(key_error·provider_limit) → 우리 상한 → ok."""
    from data_agent import llm_settings
    from data_agent.llm_budget import BUDGET

    env = os.environ if env is None else env
    now = now or _now()
    settings = llm_settings.settings(env)  # 회사 모델·주소 반영 (값은 이름·호스트만 쓴다)
    kind, source = llm_settings.source(env)
    usage = BUDGET.usage(env)
    out = LlmStatus(
        key_source=source,
        key_kind=kind,
        model=settings.model or None,
        base_host=urlparse(settings.base_url).hostname if settings.base_url else None,
        calls=usage.calls,
        call_cap=usage.call_cap,
        tokens=usage.tokens,
        token_cap=usage.token_cap,
    )
    halt = HALT.active(now)
    if halt is not None and halt.reason == KEY_ERROR:
        out.state, out.tone, out.title = KEY_ERROR, "red", KEY_TEXT[0]
        text = COMPANY_KEY_TEXT if kind == "company" else KEY_TEXT[1]
        out.detail = text.format(source=out.key_source)
        out.halted_until = halt.until
    elif halt is not None and halt.reason == REQUEST_ERROR:
        out.state, out.tone, out.title = REQUEST_ERROR, "red", REQUEST_TEXT[0]
        out.detail = REQUEST_TEXT[1].format(model=halt.model or out.model or "모름")
        out.halted_until, out.request, out.error_message = halt.until, halt.request, halt.message
    elif halt is not None:
        out.state, out.tone, out.title, out.detail = PROVIDER_LIMIT, "yellow", *LIMIT_TEXT
        out.halted_until, out.strikes = halt.until, halt.strikes
    elif usage.reached:
        out.state, out.tone, out.title = OUR_CAP, "blue", CAP_TEXT[0]
        out.detail = CAP_TEXT[1].format(
            calls=usage.calls,
            call_cap=usage.call_cap,
            tokens=usage.tokens,
            token_cap=usage.token_cap,
        )
        out.halted_until = next_midnight(now)
    return out
