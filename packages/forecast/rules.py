"""Versioned FY27 forecasting defaults, separate from formal confirmation.

The user adopted the existing defaults as fixed calculation policy. This does
not turn estimates into actuals or remove any source-quality confirmation gate.
"""

from typing import Any

FIXED_RULES_VERSION = "fy27-fixed-rules-v1"
Json = dict[str, Any]


def build_fixed_assumptions(baseline: Json, snapshot: Json, prior_assumptions: Json) -> Json:
    """Inherit saved parameters first; add only missing policy-owned defaults."""
    # Lazy import: trial.py reuses the pure engine's dimension constants.
    from packages.forecast.trial import build_trial_assumptions

    version = prior_assumptions.get("fixed_rules_version")
    if version is not None and version != FIXED_RULES_VERSION:
        raise ValueError("未知固定预测规则版本，不能自动替换")
    result = build_trial_assumptions(baseline, snapshot, prior_assumptions)
    result["trial_mode"] = False
    result["fixed_rules_version"] = FIXED_RULES_VERSION
    return result
