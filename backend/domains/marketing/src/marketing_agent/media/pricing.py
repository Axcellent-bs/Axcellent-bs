"""단가표(`prices.toml`) 읽기와 비용 계산. 표에 없는 조합은 `PriceNotFound`로 거부한다."""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from marketing_agent.media.base import MediaKind

PRICES_PATH = Path(__file__).with_name("prices.toml")
_REQUIRED = ("provider", "model", "kind", "resolution", "source", "checked_on")
_REQUIRED_TOKEN = ("input_usd_per_mtok", "output_usd_per_mtok")
PER_MTOK = Decimal(1_000_000)


class PriceNotFound(Exception):
    """단가표에 없는 조합(또는 표가 허용하는 범위를 넘는 요청)이다. 호출하지 않는다."""


class PriceTableError(Exception):
    """단가표 파일이 깨졌다(필수 항목·출처·확인 날짜 누락)."""


@dataclass(frozen=True)
class PriceEntry:
    provider: str
    model: str
    kind: MediaKind
    resolution: str
    quality: str | None
    input_usd_per_mtok: Decimal
    output_usd_per_mtok: Decimal
    output_tokens: int | None  # 이미지: 장당
    output_tokens_per_second: int | None  # 영상
    max_duration_s: int | None
    source: str
    checked_on: str
    per_item_usd: Decimal | None = None  # 오디오: 곡(클립)당 고정 가격. 토큰 단가는 0


def _per_item(item: dict) -> Decimal:
    if "per_item_usd" not in item:
        raise PriceTableError(f"곡당 가격(per_item_usd)이 없다: {item['model']}")
    try:
        value = Decimal(str(item["per_item_usd"]))
    except ArithmeticError:
        raise PriceTableError(f"per_item_usd가 숫자가 아니다: {item['model']}") from None
    if not value.is_finite() or value <= 0:
        raise PriceTableError(f"per_item_usd는 0보다 커야 한다: {item['model']}")
    return value


def load_table(path: Path = PRICES_PATH) -> list[PriceEntry]:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise PriceTableError(f"단가표를 읽을 수 없다: {type(error).__name__}") from error
    entries: list[PriceEntry] = []
    for item in raw.get("price", []):
        missing = [k for k in _REQUIRED if k not in item]
        if missing:
            raise PriceTableError(f"단가표 항목에 {missing}가 없다: {item.get('model')}")
        if item["kind"] not in ("image", "video", "audio"):
            raise PriceTableError(f"kind가 image·video·audio가 아니다: {item['kind']}")
        if item["kind"] == "audio":
            per_item = _per_item(item)
            entries.append(
                PriceEntry(
                    provider=item["provider"],
                    model=item["model"],
                    kind="audio",
                    resolution=item["resolution"],
                    quality=item.get("quality"),
                    input_usd_per_mtok=Decimal(0),
                    output_usd_per_mtok=Decimal(0),
                    output_tokens=None,
                    output_tokens_per_second=None,
                    max_duration_s=item.get("max_duration_s"),
                    source=item["source"],
                    checked_on=item["checked_on"],
                    per_item_usd=per_item,
                )
            )
            continue
        missing = [k for k in _REQUIRED_TOKEN if k not in item]
        if missing:
            raise PriceTableError(f"단가표 항목에 {missing}가 없다: {item.get('model')}")
        has_tokens = (item["kind"] == "image" and "output_tokens" in item) or (
            item["kind"] == "video" and "output_tokens_per_second" in item
        )
        if not has_tokens:
            raise PriceTableError(f"출력 토큰 수가 없다: {item['model']} {item['resolution']}")
        entries.append(
            PriceEntry(
                provider=item["provider"],
                model=item["model"],
                kind=item["kind"],
                resolution=item["resolution"],
                quality=item.get("quality"),
                input_usd_per_mtok=Decimal(str(item["input_usd_per_mtok"])),
                output_usd_per_mtok=Decimal(str(item["output_usd_per_mtok"])),
                output_tokens=item.get("output_tokens"),
                output_tokens_per_second=item.get("output_tokens_per_second"),
                max_duration_s=item.get("max_duration_s"),
                source=item["source"],
                checked_on=item["checked_on"],
            )
        )
    return entries


@lru_cache(maxsize=8)
def _cached(path: Path) -> tuple[PriceEntry, ...]:
    return tuple(load_table(path))


def find_entry(
    *,
    provider: str,
    model: str,
    kind: MediaKind,
    resolution: str,
    quality: str | None,
    path: Path | None = None,
) -> PriceEntry:
    wanted = (provider, model, kind, resolution, quality)
    entries = _cached(path or PRICES_PATH)
    for entry in entries:
        if (entry.provider, entry.model, entry.kind, entry.resolution, entry.quality) == wanted:
            return entry
    # quality가 정의되지 않은 항목은 quality와 무관한 단가다. 이미지 장면 템플릿은 quality를 고정
    # ("medium")하는데 gemini 이미지 API에는 quality 설정이 없고 단가표 항목도 quality가 없다
    # (openai gpt-image-2만 quality별 항목). 정확히 맞는 항목이 없을 때만 이 항목을 쓰고,
    # quality가 정의된 항목끼리는 계속 정확히 비교한다(없는 quality는 거부).
    if quality is not None:
        for entry in entries:
            if (entry.provider, entry.model, entry.kind, entry.resolution) == wanted[:4] and (
                entry.quality is None
            ):
                return entry
    raise PriceNotFound(f"{provider}/{model or '(모델 없음)'} {kind} {resolution} {quality or ''}")


def output_tokens_for(entry: PriceEntry, duration_s: int | None) -> int:
    if entry.kind == "audio":
        return 0  # 곡당 고정 가격이라 토큰을 세지 않는다
    if entry.kind == "image":
        return int(entry.output_tokens or 0)
    if duration_s is None or duration_s < 1:
        raise PriceNotFound("영상 길이가 없다")
    if entry.max_duration_s is not None and duration_s > entry.max_duration_s:
        raise PriceNotFound(f"길이 {duration_s}초는 표의 상한 {entry.max_duration_s}초를 넘는다")
    return int(entry.output_tokens_per_second or 0) * duration_s


def input_tokens_estimate(prompt: str) -> int:
    """입력 토큰 추정. 보수적으로 UTF-8 바이트 수의 절반(올림)이다. 입력 단가가 낮아 영향은 작다."""
    return math.ceil(len(prompt.encode("utf-8")) / 2)


def usd_for(entry: PriceEntry, *, input_tokens: int, output_tokens: int) -> Decimal:
    if entry.per_item_usd is not None:
        return entry.per_item_usd
    return (
        Decimal(input_tokens) * entry.input_usd_per_mtok
        + Decimal(output_tokens) * entry.output_usd_per_mtok
    ) / PER_MTOK


def estimate_usd(entry: PriceEntry, *, prompt: str, duration_s: int | None) -> Decimal:
    return usd_for(
        entry,
        input_tokens=input_tokens_estimate(prompt),
        output_tokens=output_tokens_for(entry, duration_s),
    )


def krw_estimate(usd_est: Decimal, fx_usd_krw: Decimal, margin_pct: Decimal) -> int:
    """`ceil(usd_est × FX × (1 + margin))`, 정수 원."""
    return math.ceil(usd_est * fx_usd_krw * (1 + margin_pct / 100))


def krw_actual(usd_actual: Decimal, fx_usd_krw: Decimal) -> int:
    """확정액: 실제 사용량 비용을 환율로만 환산해 올림한다(여유분은 추정에만 붙인다)."""
    return math.ceil(usd_actual * fx_usd_krw)
