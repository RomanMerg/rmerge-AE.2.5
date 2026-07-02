from app.tools.roi import calculate_roi


def test_calculate_roi_returns_expected_keys():
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert set(result.keys()) == {"annual_savings", "payback_weeks", "three_year_net_savings", "decision"}


def test_calculate_roi_annual_savings_calculation():
    # weekly_savings = 5 * 30 = 150; annual = 150 * 52 = 7800
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["annual_savings"] == 7800.0


def test_calculate_roi_payback_weeks_calculation():
    # payback = 1000 / 150 = 6.666... -> rounded to 1 decimal
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["payback_weeks"] == 6.7


def test_calculate_roi_decision_automate_when_payback_under_26_weeks():
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["decision"] == "automate"


def test_calculate_roi_decision_borderline_between_26_and_52_weeks():
    # weekly_savings = 2 * 20 = 40; payback = 1600 / 40 = 40.0
    result = calculate_roi(hours_saved_per_week=2, hourly_rate=20, setup_cost=1600)
    assert result["payback_weeks"] == 40.0
    assert result["decision"] == "borderline"


def test_calculate_roi_decision_not_worth_it_when_payback_over_52_weeks():
    # weekly_savings = 1 * 15 = 15; payback = 5000 / 15 = 333.3...
    result = calculate_roi(hours_saved_per_week=1, hourly_rate=15, setup_cost=5000)
    assert result["decision"] == "not worth it"


def test_calculate_roi_zero_weekly_savings_returns_not_worth_it_with_no_payback():
    result = calculate_roi(hours_saved_per_week=0, hourly_rate=30, setup_cost=1000)
    assert result["decision"] == "not worth it"
    assert result["payback_weeks"] is None


def test_calculate_roi_three_year_net_savings_calculation():
    # three_year_net = 7800 * 3 - 1000 = 22400
    result = calculate_roi(hours_saved_per_week=5, hourly_rate=30, setup_cost=1000)
    assert result["three_year_net_savings"] == 22400.0
