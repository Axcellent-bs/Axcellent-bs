"""`publish_sns` 보강: FINISHED 대기·2207027 재시도·config_issue·24시간 상한·중단·PNG 거절.

실제 Instagram·Redis는 쓰지 않는다(목 어댑터·가짜 관문). 시간은 주입한 sleep으로 즉시 흐른다.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    ExecuteDecision,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from marketing_agent import handlers, sns_media
from marketing_agent.sns_publish import instagram, pending, publisher
from marketing_agent.sns_publish.config import LIMITS_CONFIG_ENV, LimitsConfigError, load_limits
from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

CTX = "ctx_hard_0001"
TENANT = "shop-demo"
SHA = "a" * 64


class FakeGate:
    def __init__(self) -> None:
        self.by_key: dict[str, ExecuteDecision] = {}
        self.calls: list[str] = []
        self.n = 0

    async def execute(self, action: str, params: dict, meta: RequestMeta, evidence: Any):
        key = meta.idempotency_key or ""
        self.calls.append(key)
        if key not in self.by_key:
            self.n += 1
            self.by_key[key] = ExecuteDecision(
                execution_id=f"exe_{self.n}",
                grade="C",
                status="held",
                hold_reason=HoldReason.OVER_THRESHOLD,
                review_id=f"rev_{self.n}",
            )
        return self.by_key[key]

    def approve_all(self) -> None:
        for key, d in self.by_key.items():
            self.by_key[key] = d.model_copy(update={"status": "executed"})


def _limits_file(tmp_path: Path, cap: int = 45, cleared: str = "") -> Path:
    path = tmp_path / "limits.toml"
    path.write_text(
        f'[publish]\nmax_posts_per_24h = {cap}\nhalt_cleared_before = "{cleared}"\n',
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    for name in (
        "MARKETING_SNS_PROVIDER",
        "REDIS_URL",
        "MARKETING_SNS_MAX_RETRIES",
        "MARKETING_SNS_STATUS_POLL_S",
        "MARKETING_SNS_STATUS_MAX_POLLS",
        LIMITS_CONFIG_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARKETING_SNS_PENDING_DB", str(tmp_path / "pending.db"))
    events.reset_mock_bus()
    handlers.reset_state()
    publisher.reset_runtime()
    instagram.reset_adapter()
    _no_sleep()
    yield
    publisher.reset_runtime()
    instagram.reset_adapter()
    events.reset_mock_bus()


sleeps: list[float] = []


def _no_sleep() -> None:
    sleeps.clear()

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    publisher.RT.sleep = record


@pytest.fixture
def adapter() -> MockInstagramAdapter:
    fake = MockInstagramAdapter()
    instagram.set_adapter(fake)
    return fake


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> FakeGate:
    fake = FakeGate()
    monkeypatch.setattr(publisher, "execute", fake.execute)
    return fake


def _request(draft: str = "spd_abc") -> AgentRequest:
    return AgentRequest(
        meta=RequestMeta(
            context_id=CTX,
            token="mock:orchestrator",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill="publish_sns",
        input={
            "post_draft_id": draft,
            "asset_ref": f"assets/{draft}.jpg",
            "asset_sha256": SHA,
            "caption": "캡션 이 글과 이미지는 AI로 만들었습니다.",
            "media_kind": "image",
            "share_to_feed": True,
        },
    )


def _row(draft: str = "spd_abc") -> pending.PendingPost:
    rows = [r for r in publisher.store()._query("") if r.post_draft_id == draft]  # noqa: SLF001
    assert len(rows) == 1
    return rows[0]


async def _approve(gate: FakeGate, draft: str = "spd_abc") -> pending.PendingPost:
    held = await publisher.publish_sns(_request(draft))
    assert held.status == TaskStatus.INPUT_REQUIRED, held
    gate.approve_all()
    await publisher.apply_decision(_row(draft), "approved", "event")
    return _row(draft)


def _seed_published(count: int, age: timedelta = timedelta(minutes=5)) -> None:
    """게시 기록 `count`건을 `age` 전에 게시된 것으로 넣는다."""
    store = publisher.store()
    stamp = (datetime.now(UTC) - age).isoformat()
    for i in range(count):
        key = f"seed-{age.total_seconds():.0f}-{i}"
        row = pending.PendingPost(
            key, TENANT, CTX, f"seed_{i}", SHA, None, None, pending.PUBLISHED, {}
        )
        assert store.create(row)
        store._conn.execute(  # noqa: SLF001
            "UPDATE sns_pending SET updated_at = ?, publish_sent = 1 WHERE idempotency_key = ?",
            (stamp, key),
        )
    store._conn.commit()  # noqa: SLF001


# -- FINISHED 대기 ---------------------------------------------------------------


def test_default_poll_is_once_a_minute_for_five_minutes() -> None:
    cfg = publisher.config()
    assert (cfg.status_poll_s, cfg.status_max_polls) == (60.0, 5)
    assert cfg.max_posts_per_24h == 45


@pytest.mark.asyncio
async def test_waits_for_finished_one_minute_apart_then_publishes(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.in_progress_polls = 2
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert sleeps == [60.0, 60.0]
    assert adapter.calls["media_publish"] == 1


@pytest.mark.asyncio
async def test_timeout_after_five_polls_does_not_publish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.in_progress_polls = 99
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "container_not_ready_timeout")
    assert adapter.calls["get_container_status"] == 5
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_container_error_fails_without_new_container_or_publish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.container_error(1, None)  # 사유 코드를 모르는 ERROR
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_container_error")
    assert adapter.calls["create_container"] == 1  # 새 컨테이너로 다시 하지 않는다
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_not_ready_2207027_on_publish_retries_once_after_waiting(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("media_publish", 9007, 2207027, times=1)
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert adapter.calls["media_publish"] == 2
    assert 60.0 in sleeps


@pytest.mark.asyncio
async def test_not_ready_2207027_twice_fails(gate: FakeGate, adapter: MockInstagramAdapter) -> None:
    adapter.fail_on("media_publish", 9007, 2207027, times=5)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, "ig_not_ready")
    assert adapter.calls["media_publish"] == 2  # 처음 1 + 재시도 1


# -- config_issue ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_config_issue_is_recorded_and_marks_reconcile_needed(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.config_issue = "CAPTION_NOT_ATTACHED"
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert row.result["config_issue"] == "CAPTION_NOT_ATTACHED"
    assert row.result["reconcile_needed"] is True
    out = publisher._output(row)  # noqa: SLF001
    assert out["reconcile_needed"] is True and out["config_issue"] == "CAPTION_NOT_ATTACHED"
    assert "reconcile_needed" in publisher._evidence(row).data_summary  # noqa: SLF001


@pytest.mark.asyncio
async def test_no_config_issue_means_no_reconcile_flag(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    row = await _approve(gate)
    assert "reconcile_needed" not in row.result


# -- 24시간 상한 -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_44_posts_pass_and_45_are_refused_before_approval(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    _seed_published(44)
    assert (await _approve(gate)).status == pending.PUBLISHED  # 45번째가 들어간다
    refused = await publisher.publish_sns(_request("spd_next"))
    assert refused.status == TaskStatus.FAILED and refused.outcome == "daily_limit_reached"
    assert len(set(gate.calls)) == 1  # 승인 요청까지 가지 않았다


@pytest.mark.asyncio
async def test_exactly_45_existing_refuses(gate: FakeGate, adapter: MockInstagramAdapter) -> None:
    _seed_published(45)
    refused = await publisher.publish_sns(_request())
    assert refused.outcome == "daily_limit_reached" and gate.calls == []
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_posts_outside_the_24h_window_are_not_counted(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    _seed_published(45, age=timedelta(hours=24, minutes=1))
    assert (await _approve(gate)).status == pending.PUBLISHED


@pytest.mark.asyncio
async def test_count_survives_restart(gate: FakeGate, adapter: MockInstagramAdapter) -> None:
    _seed_published(45)
    publisher.reset_runtime()  # 프로세스를 다시 띄운 것과 같다(저장소 파일에서 다시 읽는다)
    _no_sleep()
    refused = await publisher.publish_sns(_request())
    assert refused.outcome == "daily_limit_reached"


@pytest.mark.asyncio
async def test_limit_is_checked_again_right_before_media_publish(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    _seed_published(44)
    held = await publisher.publish_sns(_request())
    assert held.status == TaskStatus.INPUT_REQUIRED  # 승인 요청 시점에는 통과
    _seed_published(1, age=timedelta(minutes=1))  # 승인 대기 중에 다른 게시가 늘었다
    gate.approve_all()
    await publisher.apply_decision(_row(), "approved", "event")
    row = _row()
    assert (row.status, row.failure_code) == (pending.FAILED, "daily_limit_reached")
    assert adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_account_quota_usage_wins_when_larger(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.quota_usage = 45  # 로컬 기록은 0건
    refused = await publisher.publish_sns(_request())
    assert refused.outcome == "daily_limit_reached"
    assert adapter.limit_reads == 1 and adapter.calls["media_publish"] == 0


@pytest.mark.asyncio
async def test_account_quota_total_below_our_cap_is_respected(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.quota_usage, adapter.quota_total = 30, 30
    assert (await publisher.publish_sns(_request())).outcome == "daily_limit_reached"


@pytest.mark.asyncio
async def test_limit_lookup_failure_uses_local_count_and_records_it(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("get_publishing_limit", -1, None, times=99)
    _seed_published(10)
    row = await _approve(gate)
    assert row.status == pending.PUBLISHED
    assert row.result["limit_check_failed"] is True
    assert publisher._output(row)["limit_check_failed"] is True  # noqa: SLF001


@pytest.mark.asyncio
async def test_limit_lookup_failure_still_refuses_at_45_local(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    adapter.fail_on("get_publishing_limit", -1, None, times=99)
    _seed_published(45)
    refused = await publisher.publish_sns(_request())
    assert refused.outcome == "daily_limit_reached"


@pytest.mark.asyncio
async def test_cap_comes_from_the_config_file(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(LIMITS_CONFIG_ENV, str(_limits_file(tmp_path, cap=3)))
    publisher.RT.config = None
    _seed_published(3)
    assert (await publisher.publish_sns(_request())).outcome == "daily_limit_reached"


@pytest.mark.asyncio
async def test_unreadable_limits_file_blocks_publishing(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(LIMITS_CONFIG_ENV, str(tmp_path / "missing.toml"))
    publisher.RT.config = None
    assert (await publisher.publish_sns(_request())).outcome == "limits_config_error"
    assert gate.calls == []


def test_limits_loader_rejects_bad_values(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv(
        LIMITS_CONFIG_ENV, str(_limits_file(tmp_path, cleared="2026-11-13T09:00:00"))
    )
    with pytest.raises(LimitsConfigError):  # 시각대가 없다
        load_limits()
    monkeypatch.setenv(LIMITS_CONFIG_ENV, str(_limits_file(tmp_path, cap=-1)))
    with pytest.raises(LimitsConfigError):
        load_limits()


# -- publish_halted --------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "code", "subcode", "failure"),
    [
        ("media_publish", 9, 2207042, "ig_daily_limit"),
        ("media_publish", 25, 2207050, "ig_account_restricted"),
        ("create_container", 4, 2207051, "ig_spam_suspected"),
    ],
)
@pytest.mark.asyncio
async def test_halt_codes_stop_all_later_publishing(
    gate: FakeGate,
    adapter: MockInstagramAdapter,
    op: str,
    code: int,
    subcode: int,
    failure: str,
) -> None:
    adapter.fail_on(op, code, subcode, times=1)
    row = await _approve(gate)
    assert (row.status, row.failure_code) == (pending.FAILED, failure)
    refused = await publisher.publish_sns(_request("spd_other"))
    assert refused.status == TaskStatus.FAILED and refused.outcome == "publish_halted"
    assert len(set(gate.calls)) == 1  # 새 승인 요청도 만들지 않는다


@pytest.mark.asyncio
async def test_halt_blocks_an_already_held_post_and_survives_restart(
    gate: FakeGate, adapter: MockInstagramAdapter
) -> None:
    held = await publisher.publish_sns(_request("spd_held"))
    assert held.status == TaskStatus.INPUT_REQUIRED
    publisher.store().halt_set("ig_spam_suspected")
    publisher.reset_runtime()  # 재시작해도 중단은 남는다
    _no_sleep()
    gate.approve_all()
    await publisher.apply_decision(_row("spd_held"), "approved", "event")
    row = _row("spd_held")
    assert (row.status, row.failure_code) == (pending.FAILED, "publish_halted")
    assert sum(adapter.calls.values()) == 0


@pytest.mark.asyncio
async def test_halt_is_released_by_the_config_file_only(
    gate: FakeGate, adapter: MockInstagramAdapter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    publisher.store().halt_set("ig_account_restricted")
    assert (await publisher.publish_sns(_request())).outcome == "publish_halted"
    time.sleep(0.01)
    cleared = datetime.now(UTC).isoformat()  # 사람이 확인하고 파일에 적은 시각
    monkeypatch.setenv(LIMITS_CONFIG_ENV, str(_limits_file(tmp_path, cleared=cleared)))
    publisher.RT.config = None
    assert (await _approve(gate)).status == pending.PUBLISHED
    # 해제 뒤에 다시 같은 오류가 나면 다시 멈춘다
    time.sleep(0.01)
    publisher.store().halt_set("ig_daily_limit")
    assert (await publisher.publish_sns(_request("spd_again"))).outcome == "publish_halted"


def test_sns_pending_db_gets_new_tables_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE sns_pending (idempotency_key TEXT PRIMARY KEY)")
    conn.close()
    store = pending.PendingStore(str(path))
    store.close()
    store = pending.PendingStore(str(path))  # 다시 열어도 `IF NOT EXISTS`라 실패하지 않는다
    assert store.halt_latest() is None
    store.close()


# -- PNG 거절 --------------------------------------------------------------------


def test_old_png_artifact_is_rejected_with_a_clear_reason(tmp_path: Path) -> None:
    png = tmp_path / "labeled.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    report = sns_media.check_image(png)
    assert report.problems == ["image_not_jpeg"]
    assert report.details["detected_format"] == "png"
