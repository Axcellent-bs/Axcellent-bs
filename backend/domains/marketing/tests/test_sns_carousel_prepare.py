"""prepare_sns_post_from_media 캐러셀: 장마다 생성·라벨, 총비용 미리 계산, 상한·fail-closed.

실제 공급자는 부르지 않는다(목 어댑터·가짜 공급자·가짜 관문). 끝에서 끝까지 도는 시험은 ffmpeg·
ffprobe가 있어야 하고(없으면 사유를 밝히고 건너뜀), 비용·거절 시험은 도구 없이 돈다.
"""

from __future__ import annotations

import hashlib
import itertools
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, ExecuteDecision
from marketing_agent import handlers, sns, sns_label, sns_media, sns_prepare
from marketing_agent.main import app
from marketing_agent.media import registry, skill
from marketing_agent.media.base import MediaRequest, MediaResult
from marketing_agent.media.ledger import MediaLedger

CTX = "ctx_carp_0001"
client = TestClient(app)
FFMPEG = shutil.which("ffmpeg") and shutil.which("ffprobe")
needs_ffmpeg = pytest.mark.skipif(
    not FFMPEG, reason="ffmpeg·ffprobe가 없어 실제 변환 시험을 건너뜀"
)
SEASONS = ["spring", "summer", "autumn", "winter"]
THEMES = ["warm cream", "soft pastel", "deep blue", "forest green", "sunset orange"]
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
"""  # 1장 추정 약 89원(환율 1500·여유 10%)


class Gate:
    """B등급 생성 관문 대역. `hold_on`번째 호출부터 보류한다."""

    def __init__(self, hold_on: int | None = None) -> None:
        self.hold_on = hold_on
        self.seen: list[str] = []

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append(action)
        if self.hold_on is not None and len(self.seen) >= self.hold_on:
            return ExecuteDecision(
                execution_id=f"exe_{len(self.seen)}",
                grade="C",
                status="held",
                review_id="rev_1",
                hold_reason="over_threshold",
            )
        return ExecuteDecision(execution_id=f"exe_{len(self.seen)}", grade="B", status="executed")


class FakeProvider:
    name = "fakeco"

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, request: MediaRequest) -> MediaResult:
        self.calls += 1
        data = b"fake-image-bytes"
        path = request.output_dir / "asset.jpg"
        path.write_bytes(data)
        return MediaResult(
            asset_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            provider=self.name,
            model_id="fm",
            usage=None,
            generated_at=datetime.now(UTC),
            provider_job_id="job_1",
        )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    media = tmp_path / "media"
    media.mkdir()
    monkeypatch.setenv(sns_media.MEDIA_DIR_ENV, str(media))
    for name in (
        "MARKETING_IMAGE_PROVIDER",
        "MARKETING_IMAGE_MODEL",
        "MARKETING_VIDEO_PROVIDER",
        "MARKETING_AUDIO_MODE",
        "MARKETING_FX_USD_KRW",
        "MARKETING_FX_AS_OF",
        "MARKETING_FX_MAX_AGE_DAYS",
        "MARKETING_COST_SAFETY_MARGIN_PCT",
        "MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM",
        "MARKETING_MEDIA_MONTHLY_BUDGET_KRW",
        sns_label.CONFIG_ENV,
        sns_label.FONT_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    events.reset_mock_bus()
    handlers.reset_state()


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch) -> Gate:
    fake = Gate()
    monkeypatch.setattr(skill, "execute", fake)
    return fake


@pytest.fixture
def no_tools_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sns_prepare, "preflight_tools", lambda: None)


@pytest.fixture
def prices(tmp_path: Path) -> Path:
    path = tmp_path / "prices.toml"
    path.write_text(PRICES, encoding="utf-8")
    return path


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeProvider:
    provider = FakeProvider()
    monkeypatch.setitem(registry.ADAPTERS, "fakeco", lambda: provider)
    return provider


def _real_env(monkeypatch: pytest.MonkeyPatch, **over: str) -> None:
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


def _coupon() -> dict:
    return {
        "campaign_id": "cmp_1",
        "unit_value": 2000,
        "constraints": [
            {"code": code, "text": f"{label} 안내 {i}"}
            for i, (code, label) in enumerate(sns.CONSTRAINT_LABELS.items(), 1)
        ],
    }


def _scenes(count: int) -> list[dict]:
    combos = itertools.product(SEASONS, THEMES)
    return [
        {
            "scene_template_id": "cat_bookstore_image",
            "fields": {"season": season, "color_theme": theme, "category": "그림책"},
        }
        for season, theme in itertools.islice(combos, count)
    ]


def _input(count: int = 2, **over: Any) -> dict:
    return {"coupon": _coupon(), "media_kind": "image", "carousel_items": _scenes(count)} | over


def _request(data: dict) -> AgentRequest:
    return AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "mock:orchestrator",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
            },
            "skill": "prepare_sns_post_from_media",
            "input": data,
        }
    )


async def _prepare(data: dict, prices_path: Path | None = None):
    return await sns_prepare.prepare_sns_post_from_media(
        _request(data),
        notices=handlers.STATE.notices,
        store=handlers.STATE.sns,
        ledger=handlers.STATE.media_ledger,
        from_decision=handlers._from_decision,
        prices_path=prices_path,
    )


# --- 입력 경계 (도구 없이) ---


@pytest.mark.parametrize("count", [1, 11])
@pytest.mark.asyncio
async def test_item_count_out_of_range_is_rejected_before_anything(
    count: int, gate: Gate, no_tools_check: None
) -> None:
    result = await _prepare(_input(count))
    assert result.outcome == "invalid_input" and gate.seen == []


@pytest.mark.asyncio
async def test_input_shape_rules(gate: Gate, no_tools_check: None) -> None:
    duplicate = _input(2)
    duplicate["carousel_items"][1] = dict(duplicate["carousel_items"][0])
    for bad in (
        duplicate,  # 같은 장면 두 번
        _input(2, media_kind="video"),  # 영상 섞기는 범위 밖
        _input(2, scene_template_id="cat_bookstore_image"),  # 단일 입력과 섞기
        _input(2, fields={"season": "spring"}),
        _input(2, quality="low"),
    ):
        assert (await _prepare(bad)).outcome == "invalid_input"
    assert gate.seen == []


@pytest.mark.asyncio
async def test_unknown_source_stops_before_generation(gate: Gate, no_tools_check: None) -> None:
    result = await _prepare(_input(2, coupon=None, notice_id="ntc_none"))
    assert result.outcome == "notice_not_found" and gate.seen == []


@pytest.mark.asyncio
async def test_missing_tools_stop_before_generation(
    gate: Gate, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sns_prepare, "preflight_tools", lambda: "ffmpeg_not_found")
    result = await _prepare(_input(2))
    assert result.outcome == "post_tools_unavailable" and gate.seen == []


# --- 비용: 장수만큼 누적, 상한 초과면 한 장도 만들지 않는다 ---


@pytest.mark.asyncio
async def test_plan_cost_equals_what_generate_media_charges(
    gate: Gate, fake_provider: FakeProvider, prices: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _real_env(monkeypatch)
    from marketing_agent.media.config import MediaSettings

    settings = MediaSettings.from_env()
    data = {
        "post_draft_id": "DRAFT-1",
        "media_kind": "image",
        "scene_template_id": "cat_bookstore_image",
        "fields": _scenes(1)[0]["fields"],
    }
    request = _request(data).model_copy(update={"skill": "generate_media"})
    ledger = MediaLedger()
    plan = skill.plan_cost(request, ledger=ledger, settings=settings, prices_path=prices)
    assert isinstance(plan, skill.CostPlan) and plan.settled is False and 80 <= plan.krw_est <= 100
    done = await skill.generate_media(
        request, ledger=ledger, from_decision=handlers._from_decision, prices_path=prices
    )
    assert done.output is not None and done.output["cost_est_krw"] == plan.krw_est
    again = skill.plan_cost(request, ledger=ledger, settings=settings, prices_path=prices)
    assert isinstance(again, skill.CostPlan) and again.settled is True  # 재생이라 비용이 없다


@pytest.mark.asyncio
async def test_total_over_monthly_cap_makes_no_image_at_all(
    gate: Gate,
    fake_provider: FakeProvider,
    prices: Path,
    no_tools_check: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MONTHLY_BUDGET_KRW="250")  # 3장 x 약 89 = 267 > 250
    result = await _prepare(_input(3), prices)
    assert result.status == "failed" and result.outcome == "budget_exceeded"
    out = result.output or {}
    assert out["stage"] == "cost_preflight" and out["item_count"] == 3
    assert out["cost_krw"] > 250 and out["limit_krw"] == 250
    assert fake_provider.calls == 0 and gate.seen == []  # 공급자도 관문도 부르지 않았다


@pytest.mark.asyncio
async def test_existing_month_spend_counts_toward_the_carousel_total(
    gate: Gate,
    fake_provider: FakeProvider,
    prices: Path,
    no_tools_check: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MONTHLY_BUDGET_KRW="250")
    reserved = handlers.STATE.media_ledger.reserve(
        key="other",
        month=datetime.now(UTC).strftime("%Y-%m"),
        media_kind="image",
        provider="fakeco",
        model_id="fm",
        krw_est=100,
        usd_est=None,
        monthly_cap=250,
        now=datetime.now(UTC),
    )
    assert reserved.outcome == "reserved"
    result = await _prepare(_input(2), prices)  # 100(이미 씀) + 2 x 90 = 280 > 250
    assert result.outcome == "budget_exceeded" and fake_provider.calls == 0


@pytest.mark.asyncio
async def test_single_item_cap_and_missing_price_stop_before_generation(
    gate: Gate,
    fake_provider: FakeProvider,
    prices: Path,
    no_tools_check: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_env(monkeypatch, MARKETING_MEDIA_MAX_COST_KRW_PER_ITEM="50")
    over = await _prepare(_input(2), prices)
    assert over.outcome == "cost_over_single" and (over.output or {})["item_index"] == 1
    _real_env(monkeypatch)
    items = _input(2)
    items["carousel_items"][1]["resolution"] = "1024x1280"  # 단가표에 없는 조합(fail-closed)
    missing = await _prepare(items, prices)
    assert missing.outcome == "price_not_found" and (missing.output or {})["item_index"] == 2
    assert fake_provider.calls == 0 and gate.seen == []


@pytest.mark.asyncio
async def test_real_provider_without_cost_limits_is_refused(
    gate: Gate,
    fake_provider: FakeProvider,
    prices: Path,
    no_tools_check: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _real_env(monkeypatch)
    monkeypatch.delenv("MARKETING_MEDIA_MONTHLY_BUDGET_KRW")
    result = await _prepare(_input(2), prices)
    assert result.outcome == "cost_limit_not_configured" and fake_provider.calls == 0


# --- 관문은 우회하지 않는다 ---


@needs_ffmpeg
@pytest.mark.asyncio
async def test_gate_hold_on_second_item_stops_without_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = Gate(hold_on=2)
    monkeypatch.setattr(skill, "execute", fake)
    result = await _prepare(_input(3))
    assert result.outcome == "held"
    out = result.output or {}
    assert (out["stage"], out["item_index"], out["completed_items"]) == ("generate_media", 2, 1)
    assert fake.seen == ["generate_media", "generate_media"]  # 3번째는 부르지 않았다
    assert not any(handlers.STATE.sns._conn.execute("SELECT 1 FROM sns_drafts"))  # noqa: SLF001


# --- 끝에서 끝까지 (ffmpeg) ---


@needs_ffmpeg
@pytest.mark.parametrize("count", [2, 10])
@pytest.mark.asyncio
async def test_mock_carousel_end_to_end_is_publishable_and_never_published(
    count: int, gate: Gate
) -> None:
    result = await _prepare(_input(count))
    assert result.outcome is None and result.status == "completed", result.output
    out = result.output or {}
    assert out["stage"] == "draft_ready" and out["item_count"] == count
    assert gate.seen == ["generate_media"] * count  # publish_sns 관문은 부르지 않았다
    check = out["check"]
    assert check["verdict"] == "approved" and check["publishable"] is True
    assert (check["ai_label_missing_count"], check["text_mismatch_count"]) == (0, 0)
    assert check["media_details"]["item_count"] == count
    publish_input = out["publish_input"]
    assert len(publish_input["asset_refs"]) == len(publish_input["asset_sha256s"]) == count
    for ref, sha in zip(publish_input["asset_refs"], publish_input["asset_sha256s"], strict=True):
        asset = sns_media.resolve_asset(ref)
        assert ref.endswith("labeled.jpg") and not Path(ref).is_absolute()
        assert sns_media.sha256_file(asset) == sha
        assert sns_media.check_image(asset).problems == []
        assert sns_media.read_machine_mark(asset, "image")["ai_generated"] == "true"
    assert out["cost"]["total_cost_krw"] == 100 * count  # 목 이미지 1장 100원 x 장수
    assert "사람 승인" in out["next_step"]
    assert str(sns_media.media_root()) not in str(out)  # 절대 경로가 결과에 없다


@needs_ffmpeg
@pytest.mark.asyncio
async def test_second_call_replays_every_item_without_new_cost(gate: Gate) -> None:
    first = (await _prepare(_input(2))).output or {}
    ledger = handlers.STATE.media_ledger
    month = datetime.now(UTC).strftime("%Y-%m")
    spent = ledger.month_total(month)
    second = (await _prepare(_input(2))).output or {}
    assert all(item["replayed"] for item in second["items"])
    assert all(item["label"]["reused"] for item in second["items"])
    assert second["post_draft_id"] == first["post_draft_id"]
    assert ledger.month_total(month) == spent
