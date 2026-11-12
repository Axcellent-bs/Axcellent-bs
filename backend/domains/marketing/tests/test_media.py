"""generate_media: 목 모드, 상한, 환율, 단가표, 프롬프트 규칙, 장부(재시작), 멱등, 사후 정산."""

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
        "MARKETING_FX_USD_KRW",
        "MARKETING_FX_AS_OF",
        "MARKETING_FX_MAX_AGE_DAYS",
        "MARKETING_COST_SAFETY_MARGIN_PCT",
        "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM",
        "MARKETING_MEDIA_MONTHLY_BUDGET_KRW",
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
    """실제(가짜 이름) 공급자를 쓰는 설정. 환율·상한을 모두 채운다."""
    values = {
        "MARKETING_IMAGE_PROVIDER": "fakeco",
        "MARKETING_IMAGE_MODEL": "fm",
        "MARKETING_FX_USD_KRW": "1500",
        "MARKETING_FX_AS_OF": datetime.now(UTC).date().isoformat(),
        "MARKETING_FX_MAX_AGE_DAYS": "7",
        "MARKETING_COST_SAFETY_MARGIN_PCT": "10",
        "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM": "1000",
        "MARKETING_MEDIA_MONTHLY_BUDGET_KRW": "100000",
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
    assert out["cost_krw"] == out["cost_est_krw"] == 100 and out["usage_source"] == "estimate"
    asset = Path(out["asset_ref"])
    assert hashlib.sha256(asset.read_bytes()).hexdigest() == out["sha256"]
    meta = json.loads((asset.parent / "meta.json").read_text(encoding="utf-8"))
    assert meta["ai_generated"] is True and "No text" in meta["prompt"]

    video = await _run(
        ledger, media_kind="video", scene_template_id="cat_bookstore_video", post_draft_id="D2"
    )
    assert video.outcome == "generated" and video.output is not None
    assert video.output["muted"] is True and video.output["duration_s"] == 8
    assert video.output["ai_generated"] is True and video.output["cost_krw"] == 1000
    assert [s["action"] for s in gate.seen] == ["generate_media", "generate_media"]
    assert gate.seen[1]["params"]["duration_s"] == 8


@pytest.mark.asyncio
async def test_gate_key_and_params(gate: Gate) -> None:
    result = await _run(MediaLedger(), claimed_cost_krw=100)
    assert result.outcome == "generated"
    seen = gate.seen[0]
    prefix = f"{CTX}:generate_media:DRAFT-1:"
    assert seen["key"].startswith(prefix) and len(seen["key"]) == len(prefix) + 10
    params = seen["params"]
    assert params["cost_krw"] == 100 and params["claimed_cost_krw"] == 100
    assert params["media_kind"] == "image" and params["ai_generated"] is True
    assert "duration_s" not in params and len(params["summary"]) <= 200


@pytest.mark.asyncio
async def test_over_limit_even_in_mock_never_calls_provider(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []
    original = MockProvider.generate
    monkeypatch.setattr(
        MockProvider, "generate", lambda self, req: (calls.append(1), original(self, req))[1]
    )
    monkeypatch.setenv("MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM", "50")
    result = await _run(MediaLedger())
    assert result.outcome == "cost_over_single" and calls == [] and gate.seen == []


# ---- 실제 공급자 거부 (호출 전) ----


@pytest.mark.asyncio
async def test_real_provider_name_without_adapter_is_refused(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_IMAGE_PROVIDER="openai", MARKETING_IMAGE_MODEL="gpt-image-2")
    result = await _run(MediaLedger())
    assert result.outcome == "provider_adapter_missing" and gate.seen == []


@pytest.mark.asyncio
async def test_fx_not_configured_refuses_real_provider(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    for name in ("MARKETING_FX_USD_KRW", "MARKETING_FX_AS_OF"):
        monkeypatch.delenv(name)
    result = await _run(MediaLedger(), prices)
    assert result.outcome == "fx_not_configured"
    assert fake.calls == 0 and gate.seen == []


@pytest.mark.asyncio
async def test_stale_fx_refuses_real_provider(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    old = (datetime.now(UTC) - timedelta(days=30)).date().isoformat()
    _real_env(monkeypatch, MARKETING_FX_AS_OF=old, MARKETING_FX_MAX_AGE_DAYS="7")
    result = await _run(MediaLedger(), prices)
    assert result.outcome == "fx_stale" and fake.calls == 0 and gate.seen == []


@pytest.mark.asyncio
async def test_missing_limits_or_model_refuse_real_provider(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    monkeypatch.delenv("MARKETING_MEDIA_MONTHLY_BUDGET_KRW")
    assert (await _run(MediaLedger(), prices)).outcome == "cost_limit_not_configured"
    _real_env(monkeypatch, MARKETING_IMAGE_MODEL="")
    assert (await _run(MediaLedger(), prices)).outcome == "model_not_configured"
    assert fake.calls == 0 and gate.seen == []


@pytest.mark.asyncio
async def test_combination_missing_from_price_table_is_refused(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    # 표에는 1024x1024 medium뿐이다. 1024x1280(4:5)·high는 [확인 필요]라 항목이 없다
    for over in ({"resolution": "1024x1280"}, {"quality": "high"}):
        result = await _run(MediaLedger(), prices, **over)
        assert result.outcome == "price_not_found", over
    assert fake.calls == 0 and gate.seen == []


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
    with pytest.raises(PriceNotFound):  # 8초를 넘는 영상
        entry = pricing.find_entry(
            provider="gemini",
            model="gemini-omni-1.1-flash",
            kind="video",
            resolution="720p",
            quality=None,
        )
        pricing.output_tokens_for(entry, 9)
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


# ---- 상한 ----


@pytest.mark.asyncio
async def test_single_cap_over_never_calls_provider(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM="10")
    ledger = MediaLedger()
    result = await _run(ledger, prices)
    assert result.outcome == "cost_over_single"
    assert fake.calls == 0 and gate.seen == [] and ledger.month_total(MONTH) == 0


@pytest.mark.asyncio
async def test_monthly_cap_counts_settled_and_reserved(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MONTHLY_BUDGET_KRW="100")
    ledger = MediaLedger()
    first = await _run(ledger, prices)  # 추정 약 90원
    assert first.outcome == "generated" and fake.calls == 1
    second = await _run(ledger, prices, post_draft_id="DRAFT-2")
    assert second.outcome == "budget_exceeded"
    assert fake.calls == 1 and len(gate.seen) == 1  # 공급자도 관문도 부르지 않았다


@pytest.mark.asyncio
async def test_monthly_cap_counts_open_reservation(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MONTHLY_BUDGET_KRW="100")
    ledger = MediaLedger()
    held = ledger.reserve(
        key="other",
        month=MONTH,
        media_kind="image",
        provider="fakeco",
        model_id="fm",
        krw_est=60,
        usd_est=None,
        monthly_cap=100,
        now=datetime.now(UTC),
    )
    assert held.outcome == "reserved"
    result = await _run(ledger, prices)  # 60(예약) + 약 90 > 100
    assert result.outcome == "budget_exceeded" and fake.calls == 0


@pytest.mark.asyncio
async def test_month_total_survives_restart(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MONTHLY_BUDGET_KRW="150")
    db = str(tmp_path / "ledger.db")
    first = MediaLedger(db)
    assert (await _run(first, prices)).outcome == "generated"
    total = first.month_total(MONTH)
    assert total > 0
    first.close()

    reopened = MediaLedger(db)  # 다시 시작
    assert reopened.month_total(MONTH) == total
    over = await _run(reopened, prices, post_draft_id="DRAFT-2")
    assert over.outcome == "budget_exceeded" and fake.calls == 1


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
    assert ledger.month_total(MONTH) == 100  # 두 번 세지 않는다


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
    assert ledger.month_total(MONTH) == 0  # 예약을 풀었다
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
    assert ledger.month_total(MONTH) > 0  # 과금 여부를 모르므로 추정액을 센다
    fake.error = None  # 재시도는 비용을 다시 계산해 새로 예약한다
    retry = await _run(ledger, prices)
    assert retry.outcome == "generated" and len(gate.seen) == 2


# ---- 사후 정산 ----


@pytest.mark.asyncio
async def test_settle_with_actual_usage(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    fake.usage = Usage(input_tokens=100, output_tokens=1000)
    ledger = MediaLedger()
    result = await _run(ledger, prices)
    assert result.output is not None
    # (100×5 + 1000×30) / 1M = 0.0305 USD × 1500 = 45.75 → 46원 (여유분은 추정에만 붙는다)
    assert result.output["usage_source"] == "actual" and result.output["cost_krw"] == 46
    assert result.output["cost_est_krw"] > 46 and result.output["model_id"] == "fm"
    row = ledger.settled_for(gate.seen[0]["key"])
    assert row is not None and row.usage_source == "actual" and row.krw_actual == 46
    assert row.fx is not None and row.fx_as_of == datetime.now(UTC).date().isoformat()
    assert ledger.month_total(MONTH) == 46


@pytest.mark.asyncio
async def test_settle_without_usage_keeps_estimate(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    ledger = MediaLedger()
    result = await _run(ledger, prices)
    assert result.output is not None
    assert result.output["usage_source"] == "estimate"
    assert result.output["cost_krw"] == result.output["cost_est_krw"]
    assert ledger.month_total(MONTH) == result.output["cost_est_krw"]


@pytest.mark.asyncio
async def test_settle_with_unknown_response_model_falls_back_to_estimate(
    gate: Gate, fake: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    fake.usage = Usage(input_tokens=1, output_tokens=1)
    fake.model_id = "fm-unpriced"
    result = await _run(MediaLedger(), prices)
    assert result.output is not None and result.output["usage_source"] == "estimate"
    assert result.output["model_id"] == "fm-unpriced"


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
    assert out["outcome"] == "generated" and out["output"]["ai_generated"] is True


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
