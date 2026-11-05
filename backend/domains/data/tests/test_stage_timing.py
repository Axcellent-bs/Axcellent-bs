"""단계별 시간 기록: 단계 이름과 ms만 남기고, 값·키는 남기지 않는다."""

import asyncio
import sqlite3

from data_agent import stage_timing
from data_agent.stage_timing import note_cache, recording, stage, timed_handler


def test_line_has_stage_names_and_ms_only() -> None:
    lines: list[str] = []
    stage_timing.install_sqlite_timer()
    with recording("trend_popular", lines.append):
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE t (secret TEXT)")
        conn.execute("INSERT INTO t VALUES ('토큰값')")
        assert conn.execute("SELECT secret FROM t").fetchall() == [("토큰값",)]
        conn.close()
        with stage("llm"):
            pass
        note_cache("hit")
    (line,) = lines
    assert line.startswith("[timing] skill=trend_popular total_ms=")
    for name in ("sqlite_ms=", "external_ms=", "llm_ms=", "compute_ms=", "cache=hit"):
        assert name in line
    assert "토큰값" not in line and "secret" not in line


def test_outside_recording_does_nothing() -> None:
    with stage("llm"):
        pass
    note_cache("hit")  # 기록 중이 아니면 무시


def test_concurrent_requests_do_not_mix(monkeypatch) -> None:  # noqa: ANN001
    lines: list[str] = []

    async def slow(_: object) -> str:
        with stage("external"):
            await asyncio.sleep(0.05)
        return "ok"

    async def fast(_: object) -> str:
        return "ok"

    async def main() -> None:
        await asyncio.gather(timed_handler("a", slow)(None), timed_handler("b", fast)(None))

    monkeypatch.setattr(stage_timing._log, "info", lines.append)

    asyncio.run(main())
    by = {ln.split()[1]: ln for ln in lines}
    assert "external_ms=0" not in by["skill=a"]
    assert "external_ms=0 " in by["skill=b"]
