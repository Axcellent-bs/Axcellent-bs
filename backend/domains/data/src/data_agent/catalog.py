"""회사별 지표 카탈로그 (`ax_contracts.schemas.data.MetricCatalog`).

data 엔진에서 회사마다 다른 것(지표 목록·나쁜 방향·장애 쪽 실패 코드·한국어 이름·단위·
카테고리 이름)을 코드가 아니라 카탈로그 파일에서 읽는다. 캣북스는 `catalogs/catbooks.toml`.

- 어느 카탈로그를 쓸지: 환경변수 `DATA_TENANT_CATALOG` (기본 `catbooks`).
  이름이면 `catalogs/<이름>.toml`, `.toml`로 끝나면 그 파일 경로다.
- 테스트는 `use_catalog()`로 바꿔 끼운다.
- 카탈로그에 없는 지표는 이름을 그대로 쓰고 나쁜 방향은 증가로 본다(지어내지 않는다).
- shop-api 같은 커넥터(지표를 어디서 받는지)는 아직 카탈로그로 옮기지 않았다(다음 PR).
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path

from ax_contracts.schemas.data import CatalogMetric, MetricCatalog

CATALOG_ENV = "DATA_TENANT_CATALOG"
DEFAULT_CATALOG = "catbooks"
CATALOG_DIR = Path(__file__).resolve().parent / "catalogs"

_loaded: dict[str, MetricCatalog] = {}
_override: MetricCatalog | None = None


def load_catalog(name_or_path: str) -> MetricCatalog:
    """카탈로그 파일을 읽어 계약 형식으로 확인한다. 이름이면 `catalogs/<이름>.toml`."""
    if name_or_path.endswith(".toml"):
        path = Path(name_or_path)
    else:
        path = CATALOG_DIR / f"{name_or_path}.toml"
    with path.open("rb") as f:
        return MetricCatalog.model_validate(tomllib.load(f))


def current(env: Mapping[str, str] | None = None) -> MetricCatalog:
    """지금 쓰는 카탈로그. `use_catalog`로 바꿔 끼웠으면 그것, 아니면 `DATA_TENANT_CATALOG`."""
    if _override is not None:
        return _override
    key = ((os.environ if env is None else env).get(CATALOG_ENV) or DEFAULT_CATALOG).strip()
    if key not in _loaded:
        _loaded[key] = load_catalog(key)
    return _loaded[key]


def use_catalog(catalog: MetricCatalog | None) -> None:
    """카탈로그를 바꿔 끼운다(테스트용). None이면 환경변수 설정으로 돌아간다."""
    global _override
    _override = catalog


def metric(metric_id: str) -> CatalogMetric | None:
    return next((m for m in current().metrics if m.id == metric_id), None)


def metric_ids() -> list[str]:
    """감시하는 지표 id (카탈로그 순서)."""
    return [m.id for m in current().metrics]


def label(metric_id: str) -> str:
    """지표 한국어 이름. 카탈로그에 없으면 id 그대로."""
    m = metric(metric_id)
    return m.label if m else metric_id


def is_drop(metric_id: str) -> bool:
    """감소가 이상인 지표인지 (예: 주문 수)."""
    m = metric(metric_id)
    return bool(m and m.bad_direction == "down")


def rate_metric_ids() -> frozenset[str]:
    """분모가 있는 비율 지표 (예: 결제 실패율)."""
    return frozenset(m.id for m in current().metrics if m.denominator)


def fault_codes() -> frozenset[str]:
    """장애 쪽 실패 코드 (카탈로그 지표들의 합)."""
    return frozenset(code for m in current().metrics for code in m.fault_codes)


def category_label(code: str) -> str:
    """카테고리 한국어 이름. 카탈로그에 없으면 코드 그대로."""
    return current().categories.get(code, code)


def with_subject(word: str) -> str:
    """주격 조사를 붙인다: 받침이 있으면 "이", 없으면 "가" (예: 주문이, 출고가)."""
    last = word[-1:] if word else ""
    if "가" <= last <= "힣":
        return word + ("이" if (ord(last) - ord("가")) % 28 else "가")
    return word + "이(가)"
