import math

import pytest

from backend.engine import forecast as f
from backend.simulator.scenarios import Simulator


def test_occupancy_arithmetic_and_floor():
    assert f.update_occupancy(100, 30, 10) == 120
    assert f.update_occupancy(5, 0, 50) == 0


@pytest.mark.parametrize("bad", [-1, float("nan"), float("inf"), None, "5", True])
def test_invalid_inputs_rejected(bad):
    with pytest.raises(ValueError):
        f.update_occupancy(bad, 1, 1)
    with pytest.raises(ValueError):
        f.forecast_occupancy(10, bad, 1, 5)


def test_forecast_horizons_baseline():
    assert f.forecast_all(100, 10, 4) == {1: 106.0, 3: 118.0, 5: 130.0, 10: 160.0}
    assert f.forecast_all(20, 1, 10) == {1: 11.0, 3: 0.0, 5: 0.0, 10: 0.0}  # floored at zero


def test_time_to_threshold():
    assert f.time_to_threshold(100, 10, 4, 160) == 10.0
    assert f.time_to_threshold(100, 4, 10, 160) is None
    assert f.time_to_threshold(100, 5, 5, 160) is None
    assert f.time_to_threshold(200, 1, 0, 160) == 0.0


def test_risk_levels_and_bad_thresholds():
    assert f.risk_level(100, 1000, 0.7, 0.9) == "normal"
    assert f.risk_level(700, 1000, 0.7, 0.9) == "warning"
    assert f.risk_level(950, 1000, 0.7, 0.9) == "critical"
    with pytest.raises(ValueError):
        f.risk_level(1, 1000, 0.9, 0.7)
    with pytest.raises(ValueError):
        f.risk_level(1, 0, 0.7, 0.9)


def test_rates():
    assert f.rate_per_minute(30, 30) == 60
    assert f.rolling_rate([(60, 10), (60, 20)]) == 15
    assert f.rolling_rate([]) == 0
    with pytest.raises(ValueError):
        f.rate_per_minute(1, 0)


def test_staleness_and_quality():
    assert f.is_stale(None, 100, 30)
    assert f.is_stale(0, 31, 30)
    assert not f.is_stale(0, 30, 30)
    assert f.data_quality(None, 180, 0)["label"] == "none"
    fresh = f.data_quality(10, 180, 3)
    stale = f.data_quality(400, 180, 3)
    assert fresh["confidence"] > 0.75 and stale["confidence"] == 0 and "data is stale" in stale["reasons"]


def test_exit_bottleneck_is_separate_from_venue_occupancy():
    ok = f.exit_bottleneck(10, 60, 10)
    bad = f.exit_bottleneck(80, 60, 20)
    assert not ok["flag"] and bad["flag"] and bad["pressure"] == 1.33
    assert f.exit_bottleneck(5, 0, 0)["flag"]


def test_simulator_is_reproducible():
    a = [Simulator("arrival_surge", 7, 1000).step() for _ in range(1)]
    s1, s2 = Simulator("arrival_surge", 7, 1000), Simulator("arrival_surge", 7, 1000)
    assert [s1.step() for _ in range(20)] == [s2.step() for _ in range(20)]
    assert a[0]["source"] == "simulated"


def test_scenarios_behave():
    surge = Simulator("arrival_surge", 1, 1000)
    for _ in range(15):
        last = surge.step()
    assert last["occupancy"] > 700
    normal = Simulator("normal_flow", 1, 1000)
    for _ in range(30):
        n = normal.step()
    assert n["occupancy"] < 700
    stale = Simulator("stale_sensor", 1, 1000)
    emits = [stale.step()["emit"] for _ in range(8)]
    assert emits[:4] == [True] * 4 and not any(emits[4:])
    reduced = Simulator("reduced_exit", 1, 1000, exit_capacity=60)
    for _ in range(5):
        r = reduced.step()
    assert f.exit_bottleneck(r["exit_demand"], r["exit_capacity"], r["exit_rate"])["flag"]
    with pytest.raises(ValueError):
        Simulator("nope")
    assert not math.isnan(r["occupancy"])
