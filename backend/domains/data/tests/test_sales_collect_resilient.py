"""판매 기록 수집기 견고함.

연결 실패 밖의 오류(저장소·형식 오류)에도 끝나지 않고 다음 주기를 돈다.
"""

import asyncio
import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel

from data_agent import sales_collect
from data_agent.sales_collect import MockSalesSource, collect_once, describe, mock_rows
from data_agent.sales_store import SalesStore

TENANT = "shop-demo"
SECRET = "Bearer tok_SECRET_123 WHERE buyer_id='buy_0001'"  # 예외 문장에 섞일 수 있는 값


class StopLoop(Exception):
    """시험에서 `run()`의 무한 반복을 끝내는 신호 (sleep에서 던진다)."""


def _failing_upsert(monkeypatch: pytest.MonkeyPatch, fails: int, error: Exception) -> list[int]:
    """처음 `fails`번은 저장 중 예외, 그 뒤는 원래대로."""
    calls = [0]
    original = SalesStore.upsert

    def upsert(self, *args, **kwargs):  # noqa: ANN001, ANN202
        calls[0] += 1
        if calls[0] <= fails:
            raise error
        return original(self, *args, **kwargs)

    monkeypatch.setattr(SalesStore, "upsert", upsert)
    return calls


def _run_cycles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cycles: int) -> int | None:
    """`run(every=60)`을 `cycles`번 돌리고 멈춘다. 끝까지 돌았으면 None."""
    slept = [0]

    async def fake_sleep(_: float) -> None:
        slept[0] += 1
        if slept[0] >= cycles:
            raise StopLoop

    monkeypatch.setattr(sales_collect.asyncio, "sleep", fake_sleep)
    env = {"DATA_SALES_DB": str(tmp_path / "sales.db"), "DATA_MOCK": "1"}
    with pytest.raises(StopLoop):
        asyncio.run(sales_collect.run(60, env))
    return slept[0]


def test_store_error_is_recorded_and_next_cycle_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _failing_upsert(monkeypatch, 1, sqlite3.OperationalError(SECRET))
    assert _run_cycles(monkeypatch, tmp_path, 2) == 2
    assert calls[0] >= 2  # 두 번째 주기에도 저장을 시도했다
    store = SalesStore(tmp_path / "sales.db")
    assert store.order_count(TENANT) == len(mock_rows())  # 다음 주기에 다 받았다
    state = store.sync_state(TENANT, "shop-api")
    assert state.last_error is None and state.last_synced_at is not None


def test_last_error_is_kept_without_secret_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _failing_upsert(monkeypatch, 1, sqlite3.OperationalError(SECRET))
    store = SalesStore(":memory:")
    result = asyncio.run(collect_once(MockSalesSource(mock_rows(3)), store))
    assert result.errors == ["판매 저장소 오류 [OperationalError]"]
    state = store.sync_state(TENANT, "shop-api")
    assert state.last_error == "판매 저장소 오류 [OperationalError]"
    assert state.last_synced_at is None
    assert "SECRET" not in (state.last_error or "") and "buy_0001" not in result.line()


def test_validation_error_is_described_by_kind_only() -> None:
    class Row(BaseModel):
        amount: int

    try:
        Row.model_validate({"amount": SECRET})
    except Exception as error:  # noqa: BLE001
        text = describe(error)
    assert text == "판매 기록 형식 오류 (1개 칸) [ValidationError]"
    assert describe(RuntimeError(SECRET)) == "예상 못 한 오류 [RuntimeError]"


def test_once_mode_exits_1_on_store_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _failing_upsert(monkeypatch, 1, sqlite3.DatabaseError("disk image is malformed"))
    env = {"DATA_SALES_DB": str(tmp_path / "sales.db"), "DATA_MOCK": "1"}
    assert asyncio.run(sales_collect.run(None, env)) == 1
    assert asyncio.run(sales_collect.run(None, env)) == 0  # 고쳐지면 0


def test_same_error_in_a_row_logs_one_line_and_counts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _failing_upsert(monkeypatch, 3, sqlite3.OperationalError("database is locked"))
    _run_cycles(monkeypatch, tmp_path, 4)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("[sales]")]
    errors = [ln for ln in lines if "판매 저장소 오류 [OperationalError]" in ln]
    assert len(errors) == 2  # 처음 한 줄 + 회복할 때 "3번 연속" 한 줄
    assert "3번 연속" in errors[1]
    assert "저장" in lines[-1] and "실패" not in lines[-1]  # 마지막은 성공 줄


def test_source_creation_error_does_not_end_the_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(env):  # noqa: ANN001, ANN202
        raise NotImplementedError(SECRET)

    monkeypatch.setattr(sales_collect, "get_source", broken)
    assert _run_cycles(monkeypatch, tmp_path, 2) == 2
    out = capsys.readouterr().out
    assert "판매 기록 출처를 만들 수 없음 예상 못 한 오류 [NotImplementedError]" in out
    assert "SECRET" not in out


def test_finish_sync_failure_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise sqlite3.OperationalError(SECRET)

    store = SalesStore(":memory:")
    monkeypatch.setattr(store, "finish_sync", broken)
    result = asyncio.run(collect_once(MockSalesSource(mock_rows(2)), store))
    assert result.errors == ["수집 상태 기록 실패: 판매 저장소 오류 [OperationalError]"]
