def calculate_roi(hours_saved_per_week: float, hourly_rate: float, setup_cost: float) -> dict:
    """Calculate automation ROI for a small business task.

    Returns annual savings, payback period in weeks, 3-year net savings, and a
    recommendation (automate / borderline / not worth it) based on payback time.
    """
    weekly_savings = hours_saved_per_week * hourly_rate
    annual_savings = weekly_savings * 52

    if weekly_savings <= 0:
        payback_weeks = None
        decision = "not worth it"
    else:
        payback_weeks = round(setup_cost / weekly_savings, 1)
        if payback_weeks <= 26:
            decision = "automate"
        elif payback_weeks <= 52:
            decision = "borderline"
        else:
            decision = "not worth it"

    three_year_net_savings = round((annual_savings * 3) - setup_cost, 2)

    return {
        "annual_savings": round(annual_savings, 2),
        "payback_weeks": payback_weeks,
        "three_year_net_savings": three_year_net_savings,
        "decision": decision,
    }
