"""고객 속성: 고객사 주문 원본 → 성별·연령대 code (11/26, 새 회사 고객·시장 분석)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ax_contracts.data_access.shop_customers import CustomerRow
from ax_dashboard_api.site_adapter import customer_attrs, resources
from ax_dashboard_api.site_adapter.mapping import ResourceMap, SiteMapping, pick

NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)


def _orders_map(nubi: Any, **extra: str) -> ResourceMap:
    m = SiteMapping.model_validate(nubi("nubi_mapping.json")).resources["orders"]
    return m.model_copy(update={"fields": {**m.fields, **extra}})


def _booking(nubi_row: dict[str, Any], travelers: list[dict[str, Any]]) -> dict[str, Any]:
    return {**nubi_row, "travelers": travelers}


def test_pick_reads_list_index() -> None:
    row = {"travelers": [{"gender": "F"}, {"gender": "M"}]}
    assert pick(row, "travelers.1.gender") == "M"


def test_nubi_lead_adult_traveler_gives_gender_and_age_band(nubi: Any) -> None:
    raw = nubi("nubi_bookings_p1.json")["data"][0]
    row = _booking(raw, [
        {"type": "child", "gender": "M", "birth_date": "2018"},
        {"type": "adult", "gender": "F", "birth_date": "1994"},
    ])  # fmt: skip
    assert customer_attrs.attributes(row, _orders_map(nubi), NOW) == {
        "gender": "female",
        "age_band": "30s",
    }


def test_mapping_fields_win_over_detection(nubi: Any) -> None:
    raw = nubi("nubi_bookings_p1.json")["data"][0]
    row = _booking(raw, [{"type": "adult", "gender": "F", "birth_date": "1960-03-01"},
                         {"type": "adult", "gender": "M", "birth_date": "2004"}])  # fmt: skip
    m = _orders_map(
        nubi, **{"attr.gender": "travelers.1.gender", "attr.birth": "travelers.1.birth_date"}
    )
    assert customer_attrs.attributes(row, m, NOW) == {"gender": "male", "age_band": "20s"}


def test_site_without_attributes_gives_empty(nubi: Any) -> None:
    """스터디온처럼 회원에 이름·연락처만 있는 사이트: 속성 비움(지어내지 않음)."""
    row = {"id": 1, "member": {"id": "m1", "name": "김*수", "email": "a@b.c", "phone": "010"}}
    assert customer_attrs.attributes(row, _orders_map(nubi), NOW) == {}


def test_age_band_edges() -> None:
    assert customer_attrs.age_band("2010", NOW) == "10s"
    assert customer_attrs.age_band("1976-01-01", NOW) == "50s_plus"
    assert customer_attrs.age_band("2020", NOW) is None
    assert customer_attrs.age_band("unknown", NOW) is None
    assert customer_attrs.gender_code("여") == "female"
    assert customer_attrs.gender_code("other") is None


def test_customers_from_uses_latest_order_attributes() -> None:
    def o(buyer: str, day: str, attrs: dict[str, str]) -> dict[str, Any]:
        at = f"2026-11-{day}T00:00:00Z"
        return {"buyer_id": buyer, "created_at": at, "updated_at": at, "attributes": attrs}

    orders = [
        o("u1", "01", {"gender": "female", "age_band": "20s"}),
        o("u1", "20", {"age_band": "30s"}),
        o("u2", "02", {}),
    ]
    rows = {r["buyer_id"]: CustomerRow.model_validate(r) for r in resources.customers_from(orders)}
    assert rows["u1"].attributes == {"gender": "female", "age_band": "30s"}
    assert rows["u2"].attributes == {}
