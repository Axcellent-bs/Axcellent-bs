"""data AI가 요청 회사의 AI 키를 먼저 쓴다 (여러 회사 6+, 2026-11-23). 값은 더미만 쓴다.

고객지원 #1588 `test_company_llm.py`와 같은 모양.
data는 요청마다 회사가 다르므로(`company_scope.active()`) 설치의 지금 회사가 아니라
**요청 회사**의 키를 쓰는지, 키 오류 멈춤이 회사마다 나뉘는지 본다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from ax_agent_template import company_ai
from ax_agent_template.companies import CATBOOKS
from ax_agent_template.llm import LLMResponseError
from data_agent import company_scope, data_chat, handlers, llm_budget, llm_halt, llm_settings, main
from data_agent.response_cache import ResponseCache, data_stamp

SERVER = "SERVER-DEFAULT-NOT-COMPANY-0000"
NUBI_KEY = "DUMMY-NUBI-COMPANY-KEY-1111"
NUBI_KEY_2 = "DUMMY-NUBI-COMPANY-KEY-2222"
CAT_KEY = "DUMMY-CATBOOKS-COMPANY-KEY-3333"
NUBI = "nubitour"
T0 = datetime(2026, 11, 23, 3, 0, tzinfo=UTC)
MIDNIGHT = datetime(2026, 11, 23, 15, 0, tzinfo=UTC)


class Answer(BaseModel):
    x: int


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> dict[str, company_ai.CompanyLlm]:
    """서버 설정 + 회사별 가짜 키 저장소. 기본은 회사 키 없음."""
    for name in ("DATA_LLM_MODEL", "DATA_LLM_BASE_URL", "DATA_LLM_API_KEY", "AX_TENANT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LLM_MODEL", "server-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://server.example/v1")
    monkeypatch.setenv("LLM_API_KEY", SERVER)
    keys: dict[str, company_ai.CompanyLlm] = {}
    monkeypatch.setattr(company_ai, "company_llm", lambda cid=None: keys.get(cid or ""))
    names = {NUBI: "누비투어", "catbooks": "캣북스"}
    monkeypatch.setattr(
        llm_settings,
        "get_company",
        lambda cid: CATBOOKS.model_copy(update={"company_id": cid, "name": names[cid]})
        if cid in names else None,  # fmt: skip
    )
    clock = {"now": T0}
    monkeypatch.setattr(llm_halt, "HALT", llm_halt.HaltStore(None))
    monkeypatch.setattr(llm_budget, "BUDGET", llm_budget.Budget(None))
    monkeypatch.setattr(llm_halt, "_now", lambda: clock["now"])
    return keys


def _as(cid: str, fn):  # noqa: ANN001, ANN202
    with company_scope.using(cid):
        return fn()


# ---------------------------------------------------------------- 어느 키를 쓰나
def test_each_request_uses_its_own_company_key(env: dict) -> None:
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY, model="nubi-model")
    env["catbooks"] = company_ai.CompanyLlm(api_key=CAT_KEY, model="cat-model",
                                            base_url="https://cat.example/v1")  # fmt: skip
    # 같은 프로세스에서 두 회사 요청을 번갈아
    for _ in range(2):
        nubi = _as(NUBI, handlers.get_llm).settings
        cat = _as("catbooks", handlers.get_llm).settings
        assert (nubi.api_key, nubi.model, nubi.base_url) == (
            NUBI_KEY, "nubi-model", "https://server.example/v1")  # 회사 주소가 비면 서버 값
        assert (cat.api_key, cat.model, cat.base_url) == (CAT_KEY, "cat-model",
                                                          "https://cat.example/v1")  # fmt: skip
    chat = _as(NUBI, data_chat.default_llm)
    assert chat is not None and chat.settings.api_key == NUBI_KEY


def test_concurrent_requests_keep_their_company() -> None:
    async def one(cid: str) -> str:
        with company_scope.using(cid):
            await asyncio.sleep(0)
            return handlers.get_llm().settings.api_key or ""

    keys = {NUBI: company_ai.CompanyLlm(api_key=NUBI_KEY)}

    async def both() -> list[str]:
        return list(await asyncio.gather(one(NUBI), one("catbooks"), one(NUBI)))

    import data_agent.llm_settings as ls

    original = ls.company_ai.company_llm
    ls.company_ai.company_llm = lambda cid=None: keys.get(cid or "")  # type: ignore[assignment]
    try:
        assert asyncio.run(both()) == [NUBI_KEY, SERVER, NUBI_KEY]
    finally:
        ls.company_ai.company_llm = original  # type: ignore[assignment]


def test_without_a_company_key_the_server_settings_stay(env: dict) -> None:
    settings = _as(NUBI, handlers.get_llm).settings
    assert (settings.api_key, settings.model) == (SERVER, "server-model")
    assert _as(NUBI, lambda: llm_settings.source()) == ("server", "공통 LLM_API_KEY(폴백)")


def test_not_db_mode_means_no_company_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """키 저장소가 db 모드가 아니면 회사 키를 읽지 않는다(실제 company_ai.key 경로)."""
    import ax_secret_store

    monkeypatch.undo()  # 가짜 키 저장소 대신 실제 company_ai
    monkeypatch.setenv("LLM_MODEL", "server-model")
    monkeypatch.setenv("LLM_BASE_URL", "https://server.example/v1")
    monkeypatch.setenv("LLM_API_KEY", SERVER)
    monkeypatch.setattr(ax_secret_store, "is_db_mode", lambda: False)
    assert company_ai.key(company_ai.COMPANY_KEY, NUBI) == ""
    assert _as(NUBI, lambda: llm_settings.settings().api_key) == SERVER


def test_company_model_and_address_turn_llm_on(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_MODEL")
    monkeypatch.delenv("LLM_BASE_URL")
    assert _as(NUBI, main.llm_configured) is False
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY, model="m", base_url="https://n.example")
    assert _as(NUBI, main.llm_configured) is True
    assert _as("catbooks", main.llm_configured) is False  # 다른 회사는 그대로


def test_company_key_is_read_on_every_call(env: dict) -> None:
    assert _as(NUBI, handlers.get_llm).settings.api_key == SERVER
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY)
    assert _as(NUBI, handlers.get_llm).settings.api_key == NUBI_KEY


# ---------------------------------------------------------------- 키 오류 멈춤은 회사마다
def _client(codes: list[int], seen: list[str]) -> handlers.TimedLLMClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"].removeprefix("Bearer "))
        code = codes.pop(0) if codes else 200
        if code != 200:
            return httpx.Response(code, json={"error": {"message": "x"}})
        body = {"choices": [{"message": {"content": json.dumps({"x": 1})}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1}}  # fmt: skip
        return httpx.Response(200, json=body)

    settings = llm_settings.settings()
    return handlers.TimedLLMClient("data", settings, transport=httpx.MockTransport(handler))


def _ask(cid: str, codes: list[int], seen: list[str]) -> Answer:
    async def go() -> Answer:
        with company_scope.using(cid):
            return await _client(codes, seen).structured("질문", Answer)

    return asyncio.run(go())


def test_company_key_401_halts_only_that_company(env: dict) -> None:
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY)
    seen: list[str] = []
    with pytest.raises(LLMResponseError):
        _ask(NUBI, [401], seen)
    with pytest.raises(llm_halt.LLMHalted):  # 누비투어: 오늘은 부르지 않는다
        _ask(NUBI, [], seen)
    assert _ask("catbooks", [], seen).x == 1  # 캣북스(서버 키)는 계속 부른다
    assert seen == [NUBI_KEY, SERVER]
    halt = _as(NUBI, lambda: llm_halt.HALT.active())
    assert halt is not None and (halt.reason, halt.until) == ("key_error", MIDNIGHT)
    assert _as("catbooks", lambda: llm_halt.HALT.active()) is None
    # 키를 바꾸면 바로 풀린다 (재시작 없음)
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY_2)
    assert _ask(NUBI, [], seen).x == 1
    assert seen[-1] == NUBI_KEY_2


def test_server_key_error_halts_server_key_companies_only(env: dict) -> None:
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY)
    seen: list[str] = []
    with pytest.raises(LLMResponseError):
        _ask("catbooks", [403], seen)  # 서버 키 오류
    with pytest.raises(llm_halt.LLMHalted):
        _ask("acme", [], seen)  # 서버 키를 쓰는 다른 회사도 멈춤
    assert _ask(NUBI, [], seen).x == 1  # 회사 키를 쓰는 회사는 계속
    status = _as("catbooks", llm_halt.status)
    assert status.detail == ("쓰는 키: 공통 LLM_API_KEY(폴백) · 오늘은 호출을 멈췄습니다."
                             " 키를 넣고 data를 재시작하세요.")  # fmt: skip


def test_429_and_400_stay_in_one_company(env: dict) -> None:
    seen: list[str] = []
    with pytest.raises(LLMResponseError):
        _ask(NUBI, [429], seen)  # 누비투어 429 → 누비투어만 1시간 대기
    assert _as(NUBI, lambda: llm_halt.HALT.active()).reason == "provider_limit"
    with pytest.raises(LLMResponseError):
        _ask("acme", [400], seen)  # 다른 회사의 400 → 그 회사의 같은 요청만
    with pytest.raises(llm_halt.LLMHalted):
        _ask("acme", [], seen)
    assert _as("acme", lambda: llm_halt.HALT.active()).reason == "request_error"
    assert _as("catbooks", lambda: llm_halt.HALT.active()) is None
    assert _ask("catbooks", [], seen).x == 1  # 캣북스는 둘 다 상관없이 계속
    assert len(seen) == 3


def test_old_halt_file_is_read_as_catbooks(tmp_path: Path) -> None:
    path = tmp_path / "budget.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE llm_limit (id INTEGER PRIMARY KEY CHECK (id = 1),"
                 " strikes INTEGER NOT NULL, until TEXT NOT NULL)")  # fmt: skip
    until = T0 + timedelta(hours=1)
    conn.execute("INSERT INTO llm_limit VALUES (1, 2, ?)", (until.isoformat(),))
    conn.commit()
    conn.close()
    store = llm_halt.HaltStore(path)
    old = store.active(T0, company="catbooks", key_scope="server")
    assert old is not None and (old.reason, old.strikes, old.until) == ("provider_limit", 2, until)
    assert store.active(T0, company=NUBI, key_scope="server") is None  # 다른 회사는 아님
    # 새로 쓰면 회사 칸에 쓰고 옛 줄은 지운다
    store.record_error(429, T0, company="catbooks", key_scope="server")
    sql = "SELECT tenant_id, strikes FROM llm_limit_company"
    assert sqlite3.connect(path).execute(sql).fetchall() == [("catbooks", 3)]
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM llm_limit").fetchone() == (0,)
    store.record_error(429, T0, company=NUBI, key_scope="server")
    assert store.active(T0, company=NUBI, key_scope="server").strikes == 1


# ---------------------------------------------------------------- 상태
def test_status_shows_company_key_tail_only(env: dict) -> None:
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY, model="nubi-model",
                                      base_url="https://nubi.example/v1")  # fmt: skip
    status = _as(NUBI, llm_halt.status)
    assert (status.key_kind, status.key_source) == ("company", "누비투어 회사 키(…1111)")
    assert (status.model, status.base_host) == ("nubi-model", "nubi.example")
    assert NUBI_KEY not in status.model_dump_json()
    cat = _as("catbooks", llm_halt.status)
    assert (cat.key_kind, cat.key_source) == ("server", "공통 LLM_API_KEY(폴백)")
    llm_halt.HALT.record_error(401, company=NUBI, key_scope=_as(NUBI, llm_settings.key_scope))
    halted = _as(NUBI, llm_halt.status)
    assert halted.state == "key_error"
    assert halted.detail == (
        "누비투어 회사 키(…1111) · 이 회사만 오늘 호출을 멈췄어요. 시스템 관리 › 회사 연결 ›"
        " 공통 AI 도구 연결에서 키를 바꾸면 바로 다시 써요."
    )
    assert _as("catbooks", llm_halt.status).state == "ok"


def test_status_without_any_key(env: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_API_KEY")
    status = _as(NUBI, llm_halt.status)
    assert (status.key_kind, status.key_source) == ("none", "키 없음")


def test_no_key_value_in_logs_or_status(env: dict, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY)
    with pytest.raises(LLMResponseError):
        _ask(NUBI, [401], [])
    text = _as(NUBI, llm_halt.status).model_dump_json()
    text += "\n".join(r.getMessage() for r in caplog.records)
    for value in (NUBI_KEY, SERVER):
        assert value not in text
    assert "key_error" in text


# ---------------------------------------------------------------- 응답 캐시
def test_response_cache_is_split_by_company(env: dict) -> None:
    cache = ResponseCache(None)
    nubi_key = _as(NUBI, lambda: ResponseCache.key("trend", {"q": 1}, "s", "r"))
    cat_key = _as("catbooks", lambda: ResponseCache.key("trend", {"q": 1}, "s", "r"))
    assert nubi_key != cat_key
    assert cache is not None


def test_data_stamp_follows_the_company_model(env: dict, monkeypatch: pytest.MonkeyPatch,
                                              tmp_path: Path) -> None:  # fmt: skip
    monkeypatch.delenv("LLM_MODEL")
    base = {"DATA_YOUTUBE_DB": str(tmp_path / "yt.db")}
    env[NUBI] = company_ai.CompanyLlm(api_key=NUBI_KEY, model="nubi-model")
    env_ = base | {"LLM_BASE_URL": "https://s"}
    nubi = _as(NUBI, lambda: json.loads(data_stamp(env_, "b", T0)))
    cat = _as("catbooks", lambda: json.loads(data_stamp(env_, "b", T0)))
    assert nubi[-2] is True and cat[-2] is False  # LLM 켜짐 판단에 회사 모델을 반영
