"""data LLM 오류 원인 분류와 호출 중단. 멈춘 동안은 LLM을 아예 부르지 않는다.

- key_error: 공급자가 401·403 → 그날(한국 0시까지) 중단. 키는 프로세스가 켤 때 읽으므로 이 상태는
  프로세스 메모리에만 둔다 (키를 바꾸고 data를 재시작하면 풀린다)
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


class HaltStore:
    """중단 상태. key_error는 메모리, provider_limit은 SQLite(있으면)."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._key_until: datetime | None = None
        self._bad: dict[str, Halt] = {}  # request_error: 요청 기준 → 중단 (메모리)
        self._mem: tuple[int, datetime] | None = None  # (연속 429, 다시 시도할 시각)
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS llm_limit (id INTEGER PRIMARY KEY"
                    " CHECK (id = 1), strikes INTEGER NOT NULL, until TEXT NOT NULL)"
                )

    def _limit(self) -> tuple[int, datetime] | None:
        if self._conn is None:
            return self._mem
        row = self._conn.execute("SELECT strikes, until FROM llm_limit WHERE id = 1").fetchone()
        return (int(row[0]), datetime.fromisoformat(row[1])) if row else None

    def _set_limit(self, value: tuple[int, datetime] | None) -> None:
        if self._conn is None:
            self._mem = value
            return
        with self._conn:
            if value is None:
                self._conn.execute("DELETE FROM llm_limit")
            else:
                self._conn.execute(
                    "INSERT OR REPLACE INTO llm_limit VALUES (1, ?, ?)",
                    (value[0], value[1].isoformat()),
                )

    def active(self, now: datetime | None = None) -> Halt | None:
        """화면 상태용: key_error → request_error(가장 최근) → provider_limit. 없으면 None."""
        now = now or _now()
        with self._lock:
            if self._key_until is not None and now < self._key_until:
                return Halt(KEY_ERROR, self._key_until)
            bad = [h for h in self._bad.values() if now < h.until]
            limit = self._limit()
        if bad:
            return bad[-1]
        if limit is not None and now < limit[1]:
            return Halt(PROVIDER_LIMIT, limit[1], limit[0])
        return None

    def check(self, now: datetime | None = None, request: str | None = None) -> None:
        """멈춘 상태면 부르지 않고 `LLMHalted`. request_error는 같은 요청만 막는다."""
        now = now or _now()
        with self._lock:
            key_until = self._key_until if self._key_until and now < self._key_until else None
            bad = self._bad.get(request) if request else None
            bad = bad if bad is not None and now < bad.until else None
            limit = self._limit()
        limit_until = limit[1] if limit is not None and now < limit[1] else None
        for title, until in ((KEY_TEXT[0], key_until), (REQUEST_TEXT[0], bad and bad.until),
                             (LIMIT_TEXT[0], limit_until)):  # fmt: skip
            if until is not None:
                raise LLMHalted(f"{title}: {until.isoformat()}까지 LLM을 부르지 않음")

    def record_error(
        self, status_code: int | None, now: datetime | None = None, *,
        request_key: str | None = None, message: str | None = None, model: str | None = None,
    ) -> Halt | None:  # fmt: skip
        """공급자 오류를 원인별로 기록한다. 400·401·403·429가 아니면 None (중단하지 않음)."""
        now = now or _now()
        if status_code == 400 and request_key:
            name = request_key.split("|", 1)[-1]
            halt = Halt(REQUEST_ERROR, next_midnight(now), request=name, model=model,
                        message=(message or "")[:200] or None)  # fmt: skip
            with self._lock:
                self._bad.pop(request_key, None)
                self._bad[request_key] = halt  # 가장 최근 것이 끝으로
            logger.warning("data LLM request_error (400, %s): 한국 0시까지 같은 요청 중단", name)
            return halt
        if status_code in (401, 403):
            with self._lock:
                self._key_until = next_midnight(now)
            logger.warning("data LLM key_error (%s): 한국 0시까지 호출 중단", status_code)
            return Halt(KEY_ERROR, next_midnight(now))
        if status_code == 429:
            with self._lock:
                old = self._limit()
                strikes = (old[0] if old else 0) + 1
                until = next_midnight(now) if strikes >= LIMIT_STRIKES else now + RETRY_AFTER
                self._set_limit((strikes, until))
            logger.warning(
                "data LLM provider_limit (429, 연속 %d번): %s까지 호출 중단",
                strikes,
                until.isoformat(),
            )
            return Halt(PROVIDER_LIMIT, until, strikes)
        return None

    def record_success(self) -> None:
        """성공하면 연속 429 수를 지운다."""
        with self._lock:
            if self._limit() is not None:
                self._set_limit(None)


def _store_path() -> str | None:
    from data_agent.llm_budget import DB_ENV

    return os.environ.get(DB_ENV) or None


HALT = HaltStore(_store_path())


def key_source(env: Mapping[str, str]) -> str:
    """쓰는 키의 이름 (값이 아님): data 전용 → 공통 폴백 → 없음. LLMSettings와 같은 순서."""
    if (env.get("DATA_LLM_API_KEY") or "").strip():
        return "DATA_LLM_API_KEY"
    if (env.get("LLM_API_KEY") or "").strip():
        return "공통 LLM_API_KEY(폴백)"
    return "키 없음"


def _pick(env: Mapping[str, str], name: str) -> str | None:
    return (env.get(f"DATA_{name}") or "").strip() or (env.get(name) or "").strip() or None


def status(env: Mapping[str, str] | None = None, now: datetime | None = None) -> LlmStatus:
    """지금 data LLM 상태: 중단 원인(key_error·provider_limit) → 우리 상한(our_cap) → ok."""
    from data_agent.llm_budget import BUDGET

    env = os.environ if env is None else env
    now = now or _now()
    base = _pick(env, "LLM_BASE_URL")
    usage = BUDGET.usage(env)
    out = LlmStatus(
        key_source=key_source(env),
        model=_pick(env, "LLM_MODEL"),
        base_host=urlparse(base).hostname if base else None,
        calls=usage.calls,
        call_cap=usage.call_cap,
        tokens=usage.tokens,
        token_cap=usage.token_cap,
    )
    halt = HALT.active(now)
    if halt is not None and halt.reason == KEY_ERROR:
        out.state, out.tone, out.title = KEY_ERROR, "red", KEY_TEXT[0]
        out.detail = KEY_TEXT[1].format(source=out.key_source)
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
