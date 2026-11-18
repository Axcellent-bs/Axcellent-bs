"""짝 제안: 고객사 응답 첫 줄들의 칸 이름으로 shop-api 칸 짝을 고른다(규칙만, LLM 없음)."""

from typing import Any

from ax_dashboard_api.site_adapter import propose


def test_nubi_bookings_get_the_recorded_mapping(nubi: Any) -> None:
    rows = nubi("nubi_bookings_p1.json")["data"] + nubi("nubi_bookings_p2.json")["data"]
    expected = nubi("nubi_mapping.json")["resources"]["orders"]["fields"]
    assert propose.fields("orders", rows) == expected


def test_nubi_tours_get_the_recorded_mapping(nubi: Any) -> None:
    rows = nubi("nubi_tours.json")["data"]
    expected = nubi("nubi_mapping.json")["resources"]["products"]["fields"]
    assert propose.fields("products", rows) == expected


def test_unknown_fields_stay_empty_for_a_person_to_fill() -> None:
    rows = [{"no": 1, "when": "2026-11-01"}]
    found = propose.fields("orders", rows)
    assert found["order_id"] == "" and found["paid_amount"] == ""
    assert "updated_at" not in found  # 고르는 칸은 찾았을 때만


def test_nested_keys_are_matched_with_a_dot_path() -> None:
    rows = [{"id": "o1", "customer": {"id": "c1"}}]
    assert propose.fields("orders", rows)["buyer_id"] == "customer.id"


def test_status_words_are_sorted_into_shop_statuses() -> None:
    found = propose.statuses(
        [
            "pending_deposit",
            "deposit_paid",
            "fully_paid",
            "completed",
            "cancel_requested",
            "cancelled",
            "expired",
            "unpaid",
            "refunded",
            "weird",
        ]
    )
    assert found == {
        "pending_deposit": "pending_payment",
        "deposit_paid": "paid",
        "fully_paid": "paid",
        "completed": "paid",
        "cancel_requested": "paid",
        "cancelled": "cancelled",
        "expired": "cancelled",
        "unpaid": "pending_payment",
        "refunded": "cancelled",
        "weird": "",
    }


def test_status_values_come_from_the_rows() -> None:
    rows = [{"status": "a_paid"}, {"status": "a_paid"}, {"status": 3}, {"x": 1}]
    assert propose.status_values(rows, "status") == ["a_paid"]


def test_negated_status_words_are_not_paid() -> None:
    found = propose.statuses(["unconfirmed", "incomplete", "not_paid", "unsettled"])
    assert set(found.values()) == {"pending_payment"}
