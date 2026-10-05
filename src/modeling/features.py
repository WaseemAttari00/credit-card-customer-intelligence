"""Feature lists, data loading and preprocessing for the two models.

Sex and marital status are deliberately NOT used as model inputs. Lenders generally cannot
use them in credit decisions (e.g. ECOA in the US), and I wanted the same rule for the
retention model. They are still kept in the data so I can check how scores differ by group.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, OneHotEncoder, StandardScaler

from src.db import read_sql

CATEGORICAL = ["education"]

PD_AMOUNTS = ["credit_limit", "bill_latest", "bill_avg_6m", "payment_total_6m", "payment_latest",
              "new_charges_avg", "available_credit"]
PD_OTHER = ["age", "status_latest", "status_prev", "max_status_6m", "months_delinquent_6m", "months_60plus_6m",
            "months_since_delinquent", "delinquent_streak", "util_latest", "util_avg_6m", "util_max_6m",
            "util_trend_6m", "months_over_limit", "months_credit_balance", "payment_ratio_avg",
            "payment_ratio_latest", "payment_ratio_min", "months_paid_in_full", "months_missed_payment",
            "payment_to_limit_6m", "new_charges_to_limit", "new_charges_trend", "months_inactive_5m"]
PD_FEATURES = PD_AMOUNTS + PD_OTHER + CATEGORICAL

CHURN_AMOUNTS = ["credit_limit", "bill_t", "bill_tm1", "payment_t", "payment_tm1", "charges_t"]
CHURN_OTHER = ["age", "util_t", "util_tm1", "util_change", "payment_to_limit_t", "charges_to_limit_t",
               "payment_ratio_t", "paid_in_full_t", "zero_balance_t", "zero_balance_tm1", "balance_retention_t",
               "delinquent_60plus_t", "delinquent_60plus_tm1"]
CHURN_FEATURES = CHURN_AMOUNTS + CHURN_OTHER + CATEGORICAL


def signed_log1p(x):
    return np.sign(x) * np.log1p(np.abs(x))


def clip_ratio(x):
    # ratios like utilization can be extreme (over-limit, tiny limits); clip for the linear model
    return np.clip(x, -5, 10)


def make_preprocessor(amount_cols, other_cols, scale: bool) -> ColumnTransformer:
    """Linear models get log-transformed amounts, clipped ratios and scaling.
    Tree models only need imputation + one-hot encoding (they are scale invariant)."""
    num_steps_amount = [("impute", SimpleImputer(strategy="median"))]
    num_steps_other = [("impute", SimpleImputer(strategy="median", add_indicator=True))]
    if scale:
        num_steps_amount += [("log", FunctionTransformer(signed_log1p, feature_names_out="one-to-one")),
                             ("scale", StandardScaler())]
        num_steps_other += [("clip", FunctionTransformer(clip_ratio, feature_names_out="one-to-one")),
                            ("scale", StandardScaler())]
    return ColumnTransformer([
        ("amt", Pipeline(num_steps_amount), amount_cols),
        ("num", Pipeline(num_steps_other), other_cols),
        ("cat", OneHotEncoder(handle_unknown="ignore", sparse_output=False), CATEGORICAL),
    ], verbose_feature_names_out=False)


def load_pd_data() -> pd.DataFrame:
    """Customer-level features joined to the Oct 2005 default label."""
    return read_sql("""
        SELECT f.*, o.defaulted_next_month::INT AS target
        FROM features.customer_risk_features f
        JOIN core.fact_default_outcome o USING (customer_id)
        ORDER BY f.customer_id""")


def load_churn_data() -> pd.DataFrame:
    """Eligible (active) customer-snapshots, with label where it exists."""
    return read_sql("""
        SELECT * FROM features.churn_snapshots
        WHERE is_eligible
        ORDER BY snapshot_month_index, customer_id""")
