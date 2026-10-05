"""SHAP explanations and plain-English reason codes.

SHAP values are computed on the uncalibrated model output: log-odds for LightGBM and logistic
regression, probability for the random forest (sklearn trees output probabilities). Calibration is a
monotonic mapping on top of that, so the direction and ranking of drivers still hold.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import shap
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression

FEATURE_LABELS = {
    # PD features
    "credit_limit": "Credit limit", "age": "Age", "education": "Education",
    "status_latest": "Sep repayment status", "status_prev": "Aug repayment status",
    "max_status_6m": "Worst repayment status (6m)", "months_delinquent_6m": "Months delinquent (6m)",
    "months_60plus_6m": "Months 60+ days late (6m)", "months_since_delinquent": "Months since last delinquency",
    "delinquent_streak": "Consecutive late months", "util_latest": "Utilization (Sep)",
    "util_avg_6m": "Average utilization (6m)", "util_max_6m": "Peak utilization (6m)",
    "util_trend_6m": "Utilization trend (per month)", "bill_latest": "Balance (Sep)",
    "bill_avg_6m": "Average balance (6m)", "months_over_limit": "Months over limit",
    "months_credit_balance": "Months with credit balance", "payment_ratio_avg": "Avg share of bill paid",
    "payment_ratio_latest": "Share of bill paid (Sep)", "payment_ratio_min": "Lowest share of bill paid",
    "months_paid_in_full": "Months paid in full", "months_missed_payment": "Months with no payment",
    "payment_total_6m": "Total paid (6m)", "payment_latest": "Payment (Sep)",
    "payment_to_limit_6m": "Total paid / limit", "new_charges_avg": "Avg new charges",
    "new_charges_to_limit": "Avg new charges / limit", "new_charges_trend": "New charges trend",
    "months_inactive_5m": "Inactive months", "available_credit": "Available credit",
    # churn features
    "bill_t": "Balance (this month)", "bill_tm1": "Balance (last month)", "util_t": "Utilization (this month)",
    "util_tm1": "Utilization (last month)", "util_change": "Utilization change",
    "payment_t": "Payment (this month)", "payment_tm1": "Payment (last month)",
    "payment_to_limit_t": "Payment / limit", "charges_t": "New charges (this month)",
    "charges_to_limit_t": "New charges / limit", "payment_ratio_t": "Share of last bill paid",
    "paid_in_full_t": "Paid in full this month", "zero_balance_t": "Zero balance this month",
    "zero_balance_tm1": "Zero balance last month", "balance_retention_t": "Balance kept vs last month",
    "delinquent_60plus_t": "60+ days late now", "delinquent_60plus_tm1": "60+ days late last month",
}

AMOUNT_FEATURES = {"credit_limit", "bill_latest", "bill_avg_6m", "payment_total_6m", "payment_latest",
                   "new_charges_avg", "available_credit", "bill_t", "bill_tm1", "payment_t", "payment_tm1",
                   "charges_t", "new_charges_trend"}
PCT_FEATURES = {"util_latest", "util_avg_6m", "util_max_6m", "util_trend_6m", "payment_ratio_avg",
                "payment_ratio_latest", "payment_ratio_min", "payment_to_limit_6m", "new_charges_to_limit",
                "util_t", "util_tm1", "util_change", "payment_to_limit_t", "charges_to_limit_t",
                "payment_ratio_t", "balance_retention_t"}
STATUS_FEATURES = {"status_latest", "status_prev", "max_status_6m"}
FLAG_FEATURES = {"paid_in_full_t", "zero_balance_t", "zero_balance_tm1", "delinquent_60plus_t", "delinquent_60plus_tm1"}


def _unwrap(model):
    """Return the sklearn Pipeline (prep + model) inside an optional calibration wrapper."""
    if isinstance(model, CalibratedClassifierCV):
        return model.calibrated_classifiers_[0].estimator
    return model


def _original_name(col: str, originals: list[str]) -> str:
    if col.startswith("missingindicator_"):
        return col[len("missingindicator_"):]
    if col in originals:
        return col
    for o in originals:  # one-hot columns look like "education_University"
        if col.startswith(o + "_"):
            return o
    return col


def shap_frame(model, X: pd.DataFrame, background: pd.DataFrame | None = None) -> pd.DataFrame:
    """SHAP values (log-odds units), one column per ORIGINAL feature, indexed like X."""
    pipe = _unwrap(model)
    prep, est = pipe.named_steps["prep"], pipe.named_steps["model"]
    Xt = prep.transform(X)
    names = list(prep.get_feature_names_out())
    if isinstance(est, LogisticRegression):
        bg = prep.transform(background) if background is not None else Xt
        sv = shap.LinearExplainer(est, bg).shap_values(Xt)
    else:
        sv = shap.TreeExplainer(est).shap_values(Xt)
        if isinstance(sv, list):
            sv = sv[1]
        if np.ndim(sv) == 3:
            sv = sv[:, :, 1]
    df = pd.DataFrame(sv, columns=names, index=X.index)
    groups = [_original_name(c, list(X.columns)) for c in names]
    return df.T.groupby(groups).sum().T[[c for c in X.columns if c in set(groups)]]


def format_value(feature: str, value) -> str:
    if isinstance(value, str):
        return value
    if pd.isna(value):
        return "n/a"
    if feature in STATUS_FEATURES:
        v = int(value)
        return {-2: "no use", -1: "paid duly", 0: "revolving, current"}.get(v, f"{v} month(s) late")
    if feature in FLAG_FEATURES:
        return "yes" if value else "no"
    if feature in AMOUNT_FEATURES:
        return f"NT${value:,.0f}"
    if feature in PCT_FEATURES:
        return f"{value:.0%}"
    if isinstance(value, (int, np.integer)) or float(value).is_integer():
        return f"{int(value)}"
    return f"{value:.2f}"


def reason_codes(shap_df: pd.DataFrame, X: pd.DataFrame, n_up: int = 3, n_down: int = 2,
                 min_share: float = 0.05) -> pd.DataFrame:
    """Top features pushing the score up and down for each row, as readable text.
    A feature is only reported if it carries at least `min_share` of that row's total absolute
    attribution, so negligible effects don't show up as "reasons"."""
    up, down = [], []
    vals = shap_df.to_numpy()
    cols = np.array(shap_df.columns)
    totals = np.abs(vals).sum(axis=1)
    for i in range(len(shap_df)):
        order = np.argsort(-vals[i])
        floor = min_share * totals[i]
        pos = [j for j in order[:n_up] if vals[i, j] > floor]
        neg = [j for j in order[::-1][:n_down] if vals[i, j] < -floor]
        row = X.iloc[i]
        up.append("; ".join(f"{FEATURE_LABELS.get(cols[j], cols[j])}: {format_value(cols[j], row[cols[j]])}" for j in pos))
        down.append("; ".join(f"{FEATURE_LABELS.get(cols[j], cols[j])}: {format_value(cols[j], row[cols[j]])}" for j in neg))
    return pd.DataFrame({"drivers_up": up, "drivers_down": down}, index=shap_df.index)


def global_importance(shap_df: pd.DataFrame) -> pd.DataFrame:
    imp = shap_df.abs().mean().sort_values(ascending=False)
    out = imp.rename("mean_abs_shap").reset_index().rename(columns={"index": "feature"})
    out["feature_label"] = out.feature.map(lambda f: FEATURE_LABELS.get(f, f))
    out["rank"] = np.arange(1, len(out) + 1)
    return out
