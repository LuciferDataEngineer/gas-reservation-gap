import pytest

from gasgap.scenario import cover, demand_calibrated, reserve


def test_reserve_is_a_fifth():
    assert reserve(1423.14) == pytest.approx(284.628)


def test_cover_leaves_the_surplus():
    result = cover(780, 135)
    assert result["covered"] == 135
    assert result["left_over"] == pytest.approx(645)
    assert result["still_short"] == 0


def test_spare_does_not_consume_the_reserve():
    result = cover(780, -322)
    assert result["covered"] == 0
    assert result["left_over"] == 780


def test_shortfall_larger_than_the_reserve_stays_short():
    result = cover(780, 1098)
    assert result["still_short"] == pytest.approx(318)


def test_cap_binds_when_demand_plus_buffer_is_larger():
    result = demand_calibrated(375, 1360)
    assert result["cap_binds"] is True
    assert result["obligation"] == pytest.approx(272)


def test_negative_volume_is_rejected():
    with pytest.raises(ValueError):
        reserve(-1)
