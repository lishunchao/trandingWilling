"""Deterministic checks for paper-tracker cost migration."""

from __future__ import annotations

import copy

import qingyun_paper_tracker as tracker


def test_cost_migration_is_exact_and_idempotent():
    state = {"trades": [{"entry": 100.0, "stop": 99.0, "net_r": 1.5}]}
    assert tracker.normalize_trade_costs(state) == 1
    expected = 1.5 - 100.0 * tracker.FUNDING_PROXY
    assert abs(state["trades"][0]["net_r"] - expected) < 1e-12
    snapshot = copy.deepcopy(state)
    assert tracker.normalize_trade_costs(state) == 0
    assert state == snapshot
    assert state["cost_model"] == tracker.COST_MODEL_ID


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    for test in tests:
        test()
    print({"passed": len(tests)})
