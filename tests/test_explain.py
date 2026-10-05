"""Reason-code formatting and filtering (no database needed)."""
import pandas as pd

from src.modeling.explain import _original_name, format_value, reason_codes


def test_reason_codes_pick_largest_effects_and_drop_negligible_ones():
    shap = pd.DataFrame({"status_latest": [1.0, -0.5], "util_latest": [0.4, 0.01], "credit_limit": [-0.3, -0.2],
                         "age": [0.01, 0.0]})
    X = pd.DataFrame({"status_latest": [2, -1], "util_latest": [0.95, 0.1], "credit_limit": [20000, 300000],
                      "age": [30, 45]})
    rc = reason_codes(shap, X)
    assert rc.drivers_up[0] == "Sep repayment status: 2 month(s) late; Utilization (Sep): 95%"   # age (0.01) dropped
    assert rc.drivers_down[0] == "Credit limit: NT$20,000"
    assert rc.drivers_up[1] == ""                                  # 0.01 is < 5% of 0.71 total attribution
    assert rc.drivers_down[1].startswith("Sep repayment status: paid duly")


def test_value_formatting():
    assert format_value("status_latest", 0) == "revolving, current"
    assert format_value("bill_t", 12345.6) == "NT$12,346"
    assert format_value("util_t", 0.256) == "26%"
    assert format_value("education", "University") == "University"
    assert format_value("payment_ratio_avg", float("nan")) == "n/a"


def test_transformed_feature_names_map_back():
    originals = ["education", "payment_ratio_avg", "util_t", "util_tm1"]
    assert _original_name("education_University", originals) == "education"
    assert _original_name("missingindicator_payment_ratio_avg", originals) == "payment_ratio_avg"
    assert _original_name("util_tm1", originals) == "util_tm1"
