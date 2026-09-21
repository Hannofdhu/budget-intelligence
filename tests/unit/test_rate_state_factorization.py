from dataclasses import replace
from itertools import product
from unittest.mock import patch

import pytest

from packages.optimization.cp_sat_business import _rate_states
from packages.optimization.engine import RateDecision, apply_equal_percentage_point_water_level


@pytest.mark.parametrize("direction", ["UP", "DOWN"])
def test_factorized_states_match_cartesian_oracle_with_clipping_and_locks(direction: str) -> None:
    renewal = RateDecision("renewal", "track", "RENEWAL_RATE", 500000, 499900, 500100)
    share = RateDecision("share", "track", "NEW_STUDENT_SHARE", 200000, 199800, 200200)
    locked = replace(renewal, stable_id="locked", locked=True)
    other_campus = replace(renewal, stable_id="other-campus", lower_bound_ppm=499800, upper_bound_ppm=500200)
    local = (renewal, share, locked)
    all_rates = (*local, other_campus)
    expected = set()
    for renewal_level, share_level in product(range(3), repeat=2):
        values = {row.stable_id: row.current_value_ppm for row in local}
        for kind, level in (("RENEWAL_RATE", renewal_level), ("NEW_STUDENT_SHARE", share_level)):
            values.update(
                apply_equal_percentage_point_water_level(
                    all_rates, track="track", rate_type=kind, direction=direction, water_level_steps=level
                )
            )
        expected.add(tuple(sorted((row.stable_id, values[row.stable_id]) for row in local)))
    with patch(
        "packages.optimization.cp_sat_business.apply_equal_percentage_point_water_level",
        wraps=apply_equal_percentage_point_water_level,
    ) as water:
        actual = _rate_states(all_rates=all_rates, local_rates=local, track_directions={"track": direction})
    assert set(actual) == expected
    assert len(actual) == 6  # Clipped renewal levels collapse; no unique decision disappears.
    assert water.call_count == 6  # 3 + 3, instead of 3 * 3 * 2.
    assert all(dict(row)["locked"] == 500000 for row in actual)


def test_full_one_point_grid_preserves_all_10201_decisions_with_202_water_evaluations() -> None:
    rates = (
        RateDecision("renewal", "track", "RENEWAL_RATE", 800000, 800000, 810000),
        RateDecision("share", "track", "NEW_STUDENT_SHARE", 200000, 200000, 210000),
    )
    with patch(
        "packages.optimization.cp_sat_business.apply_equal_percentage_point_water_level",
        wraps=apply_equal_percentage_point_water_level,
    ) as water:
        actual = _rate_states(all_rates=rates, local_rates=rates, track_directions={"track": "UP"})
    assert len(actual) == 10201
    assert water.call_count == 202
    assert actual[0] == (("renewal", 800000), ("share", 200000))
    assert actual[-1] == (("renewal", 810000), ("share", 210000))
