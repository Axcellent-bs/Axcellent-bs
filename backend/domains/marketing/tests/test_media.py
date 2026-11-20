"""generate_media: 목 모드, 사용량 예산, 프롬프트 규칙, 장부(재시작), 멱등, 사후 사용량."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, ExecuteDecision
from marketing_agent import handlers
from marketing_agent.main import app, load_card
from marketing_agent.media import pricing, registry, skill
from marketing_agent.media.base import MediaRequest, MediaResult, Usage
from marketing_agent.media.ledger import LedgerError, MediaLedger
from marketing_agent.media.mock import MockProvider
from marketing_agent.media.pricing import PriceNotFound, PriceTableError
from marketing_agent.media.usage import UsageAmount

CTX = "ctx_media_0001"
client = TestClient(app)
MONTH = datetime.now(UTC).strftime("%Y-%m")

PRICES = """
version = 1

[[price]]
provider = "fakeco"
model = "fm"
kind = "image"
resolution = "1024x1024"
quality = "medium"
input_usd_per_mtok = 5.00
output_usd_per_mtok = 30.00
output_tokens = 1767
source = "https://example.test/pricing"
checked_on = "2026-11-05"
"""


class FakeProvider:
    """공급자 함수 호출 횟수를 센다. usage·예외를 바꿔 끼울 수 있다."""

    name = "fakeco"

    def __init__(self) -> None:
        self.calls = 0
        self.usage: Usage | None = None
        self.model_id = "fm"
        self.error: Exception | None = None

    def generate(self, request: MediaRequest) -> MediaResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        data = b"fake-image-bytes"
        path = request.output_dir / "asset.jpg"
        path.write_bytes(data)
        return MediaResult(
            asset_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            provider=self.name,
            model_id=self.model_id,
            usage=self.usage,
            generated_at=datetime.now(UTC),
            provider_job_id="job_1",
        )


class Gate:
    def __init__(self, **fields: str) -> None:
        self.fields = {"execution_id": "exe_m1", "grade": "B", "status": "executed"} | fields
        self.seen: list[dict] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append({"action": action, "params": params, "key": context.idempotency_key})
        return ExecuteDecision(**self.fields)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()
    for name in (
        "MARKETING_IMAGE_PROVIDER",
        "MARKETING_IMAGE_MODEL",
        "MARKETING_VIDEO_PROVIDER",
        "MARKETING_VIDEO_MODEL",
        "MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY",
        "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY",
        "MARKETING_QUOTA_AUDIO_COUNT_MONTHLY",
        "MARKETING_MEDIA_KEY_OWNER",
        "MARKETING_COST_LIMITS_FILE",
        "MARKETING_MEDIA_LEDGER_DB",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("MARKETING_MEDIA_DIR", str(tmp_path / "media"))


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Gate:
    fake = Gate()
    monkeypatch.setattr(skill, "execute", fake)
    return fake


@pytest.fixture
def prices(tmp_path: Path) -> Path:
    path = tmp_path / "prices.toml"
    path.write_text(PRICES, encoding="utf-8")
    return path


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setitem(registry.ADAPTERS, "fakeco", lambda: provider)
    return provider


def _real_env(monkeypatch: pytest.MonkeyPatch, **over: str) -> None:
    """실제(가짜 이름) 공급자를 쓰는 설정. 우리 키라 이미지 월 예산(장)을 채운다."""
    values = {
        "MARKETING_IMAGE_PROVIDER": "fakeco",
        "MARKETING_IMAGE_MODEL": "fm",
        "MARKETING_QUOTA_IMAGE_COUNT_MONTHLY": "100",
    } | over
    for name, value in values.items():
        monkeypatch.setenv(name, value)


FIELDS = {"season": "spring", "color_theme": "soft pastel", "category": "그림책"}


def _request(**over) -> AgentRequest:
    input_ = {
        "post_draft_id": "DRAFT-1",
        "media_kind": "image",
        "scene_template_id": "cat_bookstore_image",
        "fields": FIELDS,
    } | over
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "generate_media",
            "input": input_,
        }
    )


async def _run(ledger: MediaLedger, prices: Path | None = None, **over):
    return await skill.generate_media(
        _request(**over),
        ledger=ledger,
        from_decision=handlers._from_decision,
        prices_path=prices,
    )


# ---- 목 모드 ----


@pytest.mark.asyncio
async def test_mock_image_and_video_work_without_any_settings(gate: Gate) -> None:
    ledger = MediaLedger()
    image = await _run(ledger)
    assert image.outcome == "generated" and image.output is not None
    out = image.output
    assert out["ai_generated"] is True and out["provider"] == "mock"
    assert out["cost_krw"] == out["cost_est_krw"] == 0 and out["usage_source"] == "estimate"
    assert out["usage"] == {
        "unit": "images",
        "amount": 1,
        "input_tokens": None,
        "output_tokens": None,
    }
    asset = Path(out["asset_ref"])
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == out["sha256"]
    meta = json.loads((asset.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["ai_generated"] is True and "No text" in meta["prompt"]

    video = await _run(
        ledger, media_kind="video", scene_template_id="cat_bookstore_video", post_draft_id="D2"
    )
    assert video.outcome == "generated" and video.output is not None
    assert video.output["muted"] is True and video.output["duration_s"] == 15
    assert video.output["ai_generated"] is True and video.output["cost_krw"] == 0
    assert video.output["usage"]["unit"] == "seconds" and video.output["usage"]["amount"] == 15
    assert [s["action"] for s in gate.seen] == ["generate_media", "generate_media"]
    assert gate.seen[1]["params"]["duration_s"] == 15


@pytest.mark.asyncio
async def test_gate_key_and_params(gate: Gate) -> None:
    result = await _run(MediaLedger(), claimed_cost_krw=100)  # 예전 입력 칸은 받지만 보내지 않는다
    assert result.outcome == "generated"
    seen = gate.seen[0]
    prefix = f"{CTX}:generate_media:DRAFT-1:"
    assert seen["key"].startswith(prefix) and len(seen["key"]) == len(prefix) + 10
    params = seen["params"]
    assert "cost_krw" not in params and "claimed_cost_krw" not in params  # 원화는 안 보낸다
    assert params["usage_unit"] == "images" and params["usage_amount"] == 1
    assert params["media_kind"] == "image" and params["ai_generated"] is True
    assert "duration_s" not in params and len(params["summary"]) <= 200
    assert "원" not in params["summary"] and "1장" in params["summary"]


@pytest.mark.asyncio
async def test_over_quota_even_in_mock_never_calls_provider(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    original = MockProvider.generate
    monkeypatch.setattr(
        MockProvider, "generate", lambda self, req: (calls.append(1), original(self, req))[1]
    )
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "1")
    ledger = MediaLedger()
    assert (await _run(ledger)).outcome == "generated"
    result = await _run(ledger, post_draft_id="DRAFT-2")
    assert result.outcome == "quota_exceeded" and len(calls) == 1 and len(gate.seen) == 1
    assert result.output == {
        "media_kind": "image",
        "unit": "images",
        "requested": 1,
        "month_used": 1,
        "limit": 1,
        "remaining_pct": 0,
    }


# ---- 실제 공급자 거부 (호출 전) ----


@pytest.mark.asyncio
async def test_real_provider_name_without_adapter_is_refused(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_IMAGE_PROVIDER="openai", MARKETING_IMAGE_MODEL="gpt-image-2")
    result = await _run(MediaLedger())
    assert result.outcome == "provider_adapter_missing" and gate.seen == []


@pytest.mark.asyncio
async def test_server_key_without_a_quota_is_refused_but_a_customer_key_is_not(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    monkeypatch.delenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY")
    refused = await _run(MediaLedger())
    assert refused.outcome == "quota_not_configured" and refused.output == {"media_kind": "image"}
    assert fake.calls == 0 and gate.seen == []
    monkeypatch.setenv("MARKETING_MEDIA_KEY_OWNER", "customer")  # 고객사 키는 예산을 안 건다
    ledger = MediaLedger()
    for n in range(3):
        done = await _run(ledger, post_draft_id=f"DRAFT-{n}")
        assert done.outcome == "generated"
    assert fake.calls == 3 and ledger.month_usage(MONTH, "image") == 3  # 센 기록은 남는다


@pytest.mark.asyncio
async def test_missing_model_refuses_real_provider(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_IMAGE_MODEL="")
    assert (await _run(MediaLedger())).outcome == "model_not_configured"
    assert fake.calls == 0 and gate.seen == []


@pytest.mark.asyncio
async def test_no_price_table_entry_is_needed_to_generate(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """단가표는 호출 허용 조건이 아니다. 표에 없는 해상도·품질도 만든다(예산은 장 수로 센다)."""
    _real_env(monkeypatch)
    for n, over in enumerate(({"resolution": "1024x1280"}, {"quality": "high"})):
        result = await _run(MediaLedger(), prices, post_draft_id=f"DRAFT-{n}", **over)
        assert result.outcome == "generated", over
    assert fake.calls == 2


def test_shipped_price_table_has_no_entry_for_unconfirmed_combinations() -> None:
    table = pricing.load_table()
    assert table and all(e.source.startswith("https://") and e.checked_on for e in table)
    for model in ("gemini-omni-1.1-flash", "gemini-omni-1.2-flash"):
        with pytest.raises(PriceNotFound):  # Omni 9:16 1080p는 문서 확인 전이라 단가표에 없다
            pricing.find_entry(
                provider="gemini",
                model=model,
                kind="video",
                resolution="1080p",
                quality=None,
            )
    with pytest.raises(PriceNotFound):  # 15초를 넘는 영상
        entry = pricing.find_entry(
            provider="gemini",
            model="gemini-omni-1.1-flash",
            kind="video",
            resolution="720p",
            quality=None,
        )
        pricing.output_tokens_for(entry, 16)
    entry = pricing.find_entry(
        provider="gemini",
        model="gemini-omni-1.1-flash",
        kind="video",
        resolution="720p",
        quality=None,
    )
    assert pricing.output_tokens_for(entry, 8) == 46_336


def test_price_table_without_source_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text(PRICES.replace('source = "https://example.test/pricing"\n', ""), "utf-8")
    with pytest.raises(PriceTableError):
        pricing.load_table(bad)


def test_krw_estimate_rounds_up_to_integer_won() -> None:
    from decimal import Decimal

    assert pricing.krw_estimate(Decimal("0.0540"), Decimal("1500"), Decimal("10")) == 90
    assert pricing.krw_estimate(Decimal("0.001"), Decimal("1500"), Decimal("0")) == 2


# ---- 사용량 예산 ----


@pytest.mark.asyncio
async def test_monthly_quota_counts_settled_and_reserved(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_QUOTA_IMAGE_COUNT_MONTHLY="1")
    ledger = MediaLedger()
    first = await _run(ledger)
    assert first.outcome == "generated" and fake.calls == 1
    second = await _run(ledger, post_draft_id="DRAFT-2")
    assert second.outcome == "quota_exceeded"
    assert fake.calls == 1 and len(gate.seen) == 1  # 공급자도 관문도 부르지 않았다


@pytest.mark.asyncio
async def test_monthly_quota_counts_open_reservation(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_QUOTA_IMAGE_COUNT_MONTHLY="1")
    ledger = MediaLedger()
    held = ledger.reserve(
        key="other",
        month=MONTH,
        media_kind="image",
        provider="fakeco",
        model_id="fm",
        usage=UsageAmount("images", 1),
        monthly_cap=1,
        now=datetime.now(UTC),
    )
    assert held.outcome == "reserved"
    result = await _run(ledger)  # 1(예약) + 1 > 1
    assert result.outcome == "quota_exceeded" and fake.calls == 0


@pytest.mark.asyncio
async def test_each_kind_has_its_own_budget_and_unit(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    """영상 초와 이미지 장은 합치지 않는다. 이미지 예산이 차도 영상은 자기 예산으로 센다."""
    monkeypatch.setenv("MARKETING_QUOTA_IMAGE_COUNT_MONTHLY", "1")
    monkeypatch.setenv("MARKETING_QUOTA_VIDEO_SECONDS_MONTHLY", "20")
    ledger = MediaLedger()
    assert (await _run(ledger)).outcome == "generated"
    assert (await _run(ledger, post_draft_id="I2")).outcome == "quota_exceeded"
    video = dict(media_kind="video", scene_template_id="cat_bookstore_video")
    assert (await _run(ledger, post_draft_id="V1", **video)).outcome == "generated"  # 15초
    over = await _run(ledger, post_draft_id="V2", **video)  # 15 + 15 > 20
    assert over.outcome == "quota_exceeded" and over.output is not None
    assert (over.output["unit"], over.output["requested"], over.output["month_used"]) == (
        "seconds",
        15,
        15,
    )
    assert ledger.month_usage_all(MONTH) == {"video": 15, "image": 1, "audio": 0}


@pytest.mark.asyncio
async def test_month_usage_survives_restart(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _real_env(monkeypatch, MARKETING_QUOTA_IMAGE_COUNT_MONTHLY="1")
    db = str(tmp_path / "ledger.db")
    first = MediaLedger(db)
    assert (await _run(first)).outcome == "generated"
    assert first.month_usage(MONTH, "image") == 1
    first.close()

    reopened = MediaLedger(db)  # 다시 시작
    assert reopened.month_usage(MONTH, "image") == 1
    over = await _run(reopened, post_draft_id="DRAFT-2")
    assert over.outcome == "quota_exceeded" and fake.calls == 1


def test_old_ledger_file_without_usage_columns_is_upgraded(tmp_path: Path) -> None:
    """원화 장부(사용량 칸 없음)를 열면 칸을 더하고 옛 행은 사용량 0으로 센다."""
    import sqlite3

    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE media_ledger (
            seq INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT NOT NULL, month TEXT NOT NULL,
            status TEXT NOT NULL, media_kind TEXT NOT NULL, provider TEXT NOT NULL,
            model_id TEXT NOT NULL, krw_est INTEGER NOT NULL, usd_est TEXT, usd_actual TEXT,
            krw_actual INTEGER, fx TEXT, fx_as_of TEXT, usage_source TEXT, result_json TEXT,
            created_at TEXT NOT NULL, settled_at TEXT);
        INSERT INTO media_ledger (key, month, status, media_kind, provider, model_id, krw_est,
            created_at) VALUES ('k', '2026-11', 'settled', 'image', 'p', 'm', 90, 'x');
        """
    )
    conn.commit()
    conn.close()
    ledger = MediaLedger(db)
    assert ledger.month_usage("2026-11", "image") == 0


def test_ledger_that_cannot_open_fails_at_startup(tmp_path: Path) -> None:
    with pytest.raises(LedgerError):
        MediaLedger(str(tmp_path / "no_such_dir" / "ledger.db"))


# ---- 프롬프트 규칙 ----


@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"category": "010-1234-5678"}, "pii"),
        ({"category": "buy_abc123"}, "pii"),
        ({"category": "kim@example.com"}, "pii"),
        ({"category": "주문 문의"}, "pii"),
        ({"category": "123456789"}, "pii"),
        ({"category": "text saying SALE"}, "text_request"),
        ({"category": "글자 크게"}, "text_request"),
        ({"category": "Nike shoes"}, "real_person_or_brand"),
        ({"category": "a celebrity"}, "real_person_or_brand"),
        ({"category": "a rather long free sentence about cats and shelves"}, "free_text"),
        ({"category": "novel!"}, "free_text"),
        ({"season": "monsoon"}, "value_not_allowed"),
        ({"prompt": "draw anything I say"}, "field_not_allowed"),
        ({"category": None}, "field_value_invalid"),
    ],
)
@pytest.mark.asyncio
async def test_prompt_rules_reject(gate: Gate, fields: dict, code: str) -> None:
    result = await _run(MediaLedger(), fields=FIELDS | fields)
    assert result.outcome == "prompt_rejected" and result.output is not None
    assert result.output["reason_code"] == code
    assert gate.seen == []
    shown = json.dumps(result.output, ensure_ascii=False)
    assert "010-1234" not in shown and "kim@example" not in shown  # 입력 값은 되돌리지 않는다


@pytest.mark.asyncio
async def test_missing_field_unknown_template_and_free_prompt_key_are_rejected(
    gate: Gate,
) -> None:
    missing = await _run(MediaLedger(), fields={"season": "spring"})
    assert missing.output and missing.output["reason_code"] == "field_missing"
    unknown = await _run(MediaLedger(), scene_template_id="free_form")
    assert unknown.output and unknown.output["reason_code"] == "unknown_template"
    free = await _run(MediaLedger(), prompt="a cat holding a sign")  # 자유 프롬프트 필드는 없다
    assert free.outcome == "invalid_input"
    kind = await _run(MediaLedger(), media_kind="video")
    assert kind.outcome == "invalid_input"  # 이미지 템플릿에 영상 요청
    assert gate.seen == []


# ---- 멱등 ----


@pytest.mark.asyncio
async def test_same_key_returns_previous_result(gate: Gate) -> None:
    ledger = MediaLedger()
    first = await _run(ledger)
    again = await _run(ledger)
    assert first.output and again.output
    assert again.output["replayed"] is True and first.output["replayed"] is False
    assert {k: v for k, v in again.output.items() if k != "replayed"} == {
        k: v for k, v in first.output.items() if k != "replayed"
    }
    assert len(gate.seen) == 1  # 관문도 다시 부르지 않는다
    assert ledger.month_usage(MONTH, "image") == 1  # 두 번 세지 않는다


@pytest.mark.asyncio
async def test_changed_settings_make_a_new_key(gate: Gate) -> None:
    ledger = MediaLedger()
    await _run(ledger)
    await _run(ledger, quality="high")
    assert gate.seen[0]["key"] != gate.seen[1]["key"]


# ---- 관문·공급자 오류 ----


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "held", "review_id": "rev_1", "hold_reason": "over_threshold"},
        {"status": "rejected", "grade": "C"},
        {"status": "failed"},
    ],
)
@pytest.mark.asyncio
async def test_gate_not_executed_never_calls_provider(
    monkeypatch: pytest.MonkeyPatch, fields: dict
) -> None:
    blocked = Gate(**fields)
    monkeypatch.setattr(skill, "execute", blocked)
    calls = []
    original = MockProvider.generate
    monkeypatch.setattr(
        MockProvider, "generate", lambda self, req: (calls.append(1), original(self, req))[1]
    )
    ledger = MediaLedger()
    result = await _run(ledger)
    assert result.outcome != "generated" and calls == []
    assert ledger.month_usage(MONTH, "image") == 0  # 예약을 풀었다
    assert ledger.settled_for(blocked.seen[0]["key"]) is None


@pytest.mark.asyncio
async def test_provider_error_keeps_estimate_and_hides_message(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    fake.error = RuntimeError("api key sk-SECRET failed")
    ledger = MediaLedger()
    result = await _run(ledger, prices)
    assert result.outcome == "provider_failed" and fake.calls == 1
    assert "SECRET" not in json.dumps(result.output) + str(result.outcome)
    assert ledger.month_usage(MONTH, "image") == 1  # 과금 여부를 모르므로 추정 사용량을 센다
    fake.error = None  # 재시도는 사용량을 다시 계산해 새로 예약한다
    retry = await _run(ledger, prices)
    assert retry.outcome == "generated" and len(gate.seen) == 2


# ---- 사후 사용량 ----


@pytest.mark.asyncio
async def test_settle_with_actual_usage_records_tokens(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    fake.usage = Usage(input_tokens=100, output_tokens=1000)
    ledger = MediaLedger()
    result = await _run(ledger)
    assert result.output is not None
    out = result.output
    assert out["usage_source"] == "actual" and out["cost_krw"] == 0  # 원화는 계산하지 않는다
    assert out["usage"] == {
        "unit": "images",
        "amount": 1,
        "input_tokens": 100,
        "output_tokens": 1000,
    }
    row = ledger.settled_for(gate.seen[0]["key"])
    assert row is not None and row.usage_source == "actual"
    assert (row.usage_unit, row.usage_actual) == ("images", 1)
    assert (row.input_tokens, row.output_tokens) == (100, 1000)
    assert ledger.month_usage(MONTH, "image") == 1


@pytest.mark.asyncio
async def test_settle_without_tokens_keeps_the_unit_usage(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    ledger = MediaLedger()
    result = await _run(ledger)
    assert result.output is not None
    assert result.output["usage_source"] == "estimate"
    assert result.output["usage"]["input_tokens"] is None
    assert result.output["usage"]["output_tokens"] is None
    assert ledger.month_usage(MONTH, "image") == 1


@pytest.mark.asyncio
async def test_settle_with_partial_tokens_is_still_an_estimate(
    gate: Gate, fake: FakeProvider, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    fake.usage = Usage(input_tokens=5, output_tokens=None)
    fake.model_id = "fm-other"  # 응답이 알려 준 모델 이름이 달라도 막지 않는다(단가표를 안 본다)
    result = await _run(MediaLedger())
    assert result.output is not None and result.output["usage_source"] == "estimate"
    assert result.output["usage"]["input_tokens"] == 5
    assert result.output["model_id"] == "fm-other"


# ---- 서버 연결 ----


def test_card_lists_generate_media_and_a2a_runs_it(monkeypatch: pytest.MonkeyPatch) -> None:
    skills = {s.id: s for s in load_card().skills}
    assert skills["generate_media"].grade == "B" and skills["generate_media"].verifier == "safety"
    assert "generate_media" in handlers.HANDLERS
    monkeypatch.setattr(skill, "execute", Gate())
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "generate_media",
        "input": {
            "post_draft_id": "DRAFT-9",
            "media_kind": "image",
            "scene_template_id": "cat_bookstore_image",
            "fields": FIELDS,
        },
    }
    out = client.post("/a2a", json=body).json()
    # 총괄이 부르면 긴 작업: 접수 즉시 WORKING + task_id (결과는 task.completed)
    assert out["outcome"] == "accepted_async" and out["status"] == "working"
    assert out["output"]["task_id"].startswith("tsk_")


@pytest.mark.asyncio
async def test_video_provider_error_detail_is_kept_without_secret(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from marketing_agent.media.gemini_video import GeminiVideoError

    _real_env(monkeypatch)
    fake.error = GeminiVideoError("http_400: store=true is required")
    result = await _run(MediaLedger(), prices)
    assert result.outcome == "provider_failed"
    assert result.output["error_type"] == "GeminiVideoError"
    assert result.output["detail"] == "http_400: store=true is required"
