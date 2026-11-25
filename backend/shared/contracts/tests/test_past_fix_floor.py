"""과거 처리 재실행 유사도 하한: 총괄과 infra가 같이 보는 판정 함수."""

from __future__ import annotations

import math

import pytest

from ax_contracts.schemas.infra import PAST_FIX_MIN_SIMILARITY, meets_past_fix_floor


def test_floor_is_095() -> None:
    assert PAST_FIX_MIN_SIMILARITY == 0.95


@pytest.mark.parametrize("value", [0.95, 0.97, 1, 1.0])
def test_at_or_above_the_floor_passes(value) -> None:
    assert meets_past_fix_floor(value) is True


@pytest.mark.parametrize(
    "value",
    [0.949, 0.949999, 0, -0.1, 1.01, math.nan, math.inf, -math.inf, None, "0.97", True, False],
)
def test_below_out_of_range_or_not_a_number_fails(value) -> None:
    assert meets_past_fix_floor(value) is False
