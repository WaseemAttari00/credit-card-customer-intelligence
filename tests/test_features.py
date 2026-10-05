"""Feature tables: independent recalculation and leakage checks."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.config import PROJECT_ROOT, load_config


@pytest.fixture(autouse=True)
def _needs_features(require):
    require("features.customer_risk_features", "features.churn_snapshots")


def test_feature_sql_never_reads_the_label():
    """Target leakage guard: no feature SQL file may reference the label table or column."""
    for f in (PROJECT_ROOT / "sql" / "features").glob("*.sql"):
        text = f.read_text(encoding="utf-8").lower()
        code = "\n".join(line.split("--")[0] for line in text.splitlines())  # ignore comments
        assert "fact_default_outcome" not in code, f.name
        assert "default_next_month" not in code, f.name


def test_risk_features_one_row_per_customer(q):
    r = q("SELECT count(*) n, count(DISTINCT customer_id) d FROM features.customer_risk_features")
    assert r.n[0] == r.d[0] == q("SELECT count(*) n FROM core.dim_customer").n[0]


def test_risk_features_match_pandas_recalculation(q):
    stg = q("SELECT * FROM staging.stg_credit_card_clients WHERE customer_id % 53 = 0 ORDER BY customer_id")
    feat = q("SELECT * FROM features.customer_risk_features WHERE customer_id % 53 = 0 ORDER BY customer_id")
    status = stg[[f"status_m{i}" for i in range(1, 7)]].to_numpy()
    bills = stg[[f"bill_m{i}" for i in range(1, 7)]].to_numpy().astype(float)
    util = bills / stg.credit_limit.to_numpy()[:, None].astype(float)

    assert (feat.months_delinquent_6m.values == (status >= 1).sum(axis=1)).all()
    assert (feat.months_60plus_6m.values == (status >= 2).sum(axis=1)).all()
    assert (feat.status_latest.values == status[:, 5]).all()
    assert np.allclose(feat.util_avg_6m.values, util.mean(axis=1))
    assert np.allclose(feat.util_max_6m.values, util.max(axis=1))
    # utilization trend = OLS slope over month index 1..6
    slopes = np.polyfit(np.arange(1, 7), util.T, 1)[0]
    assert np.allclose(feat.util_trend_6m.values, slopes, atol=1e-9)
    # months since last delinquency and trailing streak
    for i in range(len(stg)):
        late = np.where(status[i] >= 1)[0] + 1
        assert feat.months_since_delinquent.iloc[i] == (6 - late.max() if len(late) else 6)
        streak = 0
        for s in status[i][::-1]:
            if s >= 1:
                streak += 1
            else:
                break
        assert feat.delinquent_streak.iloc[i] == streak


def test_churn_snapshot_uses_only_past_months_and_label_uses_next_two(q):
    snap = q("SELECT * FROM features.churn_snapshots WHERE customer_id % 61 = 0")
    fact = q("SELECT customer_id, month_index, bill_amount, is_inactive FROM core.fact_account_month "
             "WHERE customer_id % 61 = 0").set_index(["customer_id", "month_index"])
    for _, r in snap.iterrows():
        t = r.snapshot_month_index
        assert r.bill_t == fact.loc[(r.customer_id, t)].bill_amount
        assert r.bill_tm1 == fact.loc[(r.customer_id, t - 1)].bill_amount
        assert bool(r.is_eligible) == (not fact.loc[(r.customer_id, t)].is_inactive)
        if t + 2 <= 6:
            expected = fact.loc[(r.customer_id, t + 1)].is_inactive and fact.loc[(r.customer_id, t + 2)].is_inactive
            assert r.churn_label == int(expected)
        else:
            assert pd.isna(r.churn_label)


def test_train_labels_end_before_test_snapshot():
    c = load_config()["churn"]
    assert c["train_snapshot"] + c["prediction_months"] <= c["test_snapshot"]
    assert c["test_snapshot"] + c["prediction_months"] <= 6     # test label must be observable
    assert c["score_snapshot"] == 6


def test_churn_rate_is_plausible(q):
    r = q("SELECT snapshot_month_index s, avg(churn_label) rate FROM features.churn_snapshots "
          "WHERE is_eligible AND churn_label IS NOT NULL GROUP BY 1")
    assert r.rate.between(0.005, 0.05).all()


def test_ratios_are_bounded(q):
    r = q("""SELECT min(payment_ratio_avg) mn_pr, max(payment_ratio_avg) mx_pr,
                    min(months_paid_in_full) mn_pif, max(months_paid_in_full) mx_pif,
                    min(util_avg_6m) mn_u, max(util_avg_6m) mx_u
             FROM features.customer_risk_features""")
    assert 0 <= r.mn_pr[0] and r.mx_pr[0] <= 1           # capped share of bill paid
    assert 0 <= r.mn_pif[0] and r.mx_pif[0] <= 5
    # utilization can be negative (credit balance) or > 1 (over limit) but not absurd
    assert -2 < r.mn_u[0] and r.mx_u[0] < 7
