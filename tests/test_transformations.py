"""Checks on the SQL data model: grains, keys, month mapping, recalculated measures."""
import numpy as np
import pandas as pd
import pytest

from src.config import load_config


@pytest.fixture(autouse=True)
def _needs_core(require):
    require("staging.stg_credit_card_clients", "core.fact_account_month", "core.dim_customer")


def test_staging_matches_raw_row_count(q):
    raw = q("SELECT count(*) n FROM raw.credit_card_clients").n[0]
    stg = q("SELECT count(*) n FROM staging.stg_credit_card_clients").n[0]
    rej = q("SELECT count(*) n FROM staging.rejected_rows").n[0]
    assert raw == stg + rej


def test_dim_customer_one_row_per_customer(q):
    r = q("SELECT count(*) n, count(DISTINCT customer_id) d FROM core.dim_customer")
    stg = q("SELECT count(*) n FROM staging.stg_credit_card_clients").n[0]
    assert r.n[0] == r.d[0] == stg


def test_fact_grain_is_customer_by_month(q):
    r = q("""SELECT count(*) n, count(DISTINCT (customer_id, month_index)) d,
                    min(month_start) mn, max(month_start) mx FROM core.fact_account_month""")
    customers = q("SELECT count(*) n FROM core.dim_customer").n[0]
    assert r.n[0] == r.d[0] == customers * 6
    assert str(r.mn[0]) == "2005-04-01" and str(r.mx[0]) == "2005-09-01"


def test_month_mapping_against_raw_source(q):
    """Source suffix 1 = September. Check staging/fact picked the right raw columns."""
    raw = q('SELECT "ID"::BIGINT customer_id, "BILL_AMT1"::NUMERIC sep_bill, "BILL_AMT6"::NUMERIC apr_bill, '
            '"PAY_0"::INT sep_status, "PAY_AMT2"::NUMERIC aug_pay FROM raw.credit_card_clients ORDER BY 1 LIMIT 500')
    fact = q("""SELECT customer_id,
                   max(bill_amount)    FILTER (WHERE month_index = 6) sep_bill,
                   max(bill_amount)    FILTER (WHERE month_index = 1) apr_bill,
                   max(status_code)    FILTER (WHERE month_index = 6) sep_status,
                   max(payment_amount) FILTER (WHERE month_index = 5) aug_pay
                FROM core.fact_account_month WHERE customer_id <= 500 GROUP BY 1 ORDER BY 1""")
    m = raw.merge(fact, on="customer_id", suffixes=("_raw", "_fact"))
    assert len(m) == 500
    for col in ["sep_bill", "apr_bill", "sep_status", "aug_pay"]:
        assert np.allclose(m[f"{col}_raw"].astype(float), m[f"{col}_fact"].astype(float)), col


def test_derived_measures_match_independent_calculation(q):
    """Recompute est_new_charges, utilization, payment_ratio in pandas from the wide staging table."""
    stg = q("SELECT * FROM staging.stg_credit_card_clients WHERE customer_id % 97 = 0")
    fact = q("SELECT * FROM core.fact_account_month WHERE customer_id % 97 = 0").set_index(["customer_id", "month_index"])
    for _, s in stg.iterrows():
        for t in range(2, 7):
            row = fact.loc[(s.customer_id, t)]
            bill, prev, pay = s[f"bill_m{t}"], s[f"bill_m{t-1}"], s[f"payment_m{t}"]
            assert row.est_new_charges == pytest.approx(bill - prev + pay)
            assert row.utilization == pytest.approx(bill / s.credit_limit)
            if prev > 0:
                assert row.payment_ratio == pytest.approx(pay / prev)
                assert bool(row.paid_in_full) == (pay >= prev)
            else:
                assert pd.isna(row.payment_ratio)
            assert bool(row.is_inactive) == (bill <= 0 and (bill - prev + pay) <= 0)


def test_april_has_no_charge_based_measures(q):
    r = q("""SELECT count(*) FILTER (WHERE month_index = 1 AND (est_new_charges IS NOT NULL OR is_inactive IS NOT NULL)) bad_apr,
                    count(*) FILTER (WHERE month_index > 1 AND (est_new_charges IS NULL OR is_inactive IS NULL)) bad_later
             FROM core.fact_account_month""")
    assert r.bad_apr[0] == 0 and r.bad_later[0] == 0


def test_payment_timing_assumption_holds(q):
    """Payment in month t should pay the bill of month t-1 more often than the bill of month t.
    This is the assumption behind est_new_charges."""
    r = q("""SELECT avg((payment_amount = prev_bill_amount)::INT) lag1,
                    avg((payment_amount = bill_amount)::INT)      lag0
             FROM core.fact_account_month WHERE month_index > 1 AND payment_amount > 0""")
    assert r.lag1[0] > 2 * r.lag0[0]


def test_dimension_decoding_has_no_nulls(q):
    r = q("""SELECT count(*) FILTER (WHERE sex IS NULL OR education IS NULL OR marital_status IS NULL
                                     OR age_band IS NULL OR limit_tier IS NULL) n FROM core.dim_customer""")
    assert r.n[0] == 0


def test_undocumented_codes_mapped_to_unknown(q):
    r = q("""SELECT count(*) FILTER (WHERE education_code IN (0,5,6) AND education <> 'Unknown') bad_e,
                    count(*) FILTER (WHERE marriage_code = 0 AND marital_status <> 'Unknown') bad_m
             FROM core.dim_customer""")
    assert r.bad_e[0] == 0 and r.bad_m[0] == 0


def test_label_table_one_row_per_customer(q):
    r = q("SELECT count(*) n, count(DISTINCT customer_id) d FROM core.fact_default_outcome")
    assert r.n[0] == r.d[0] == q("SELECT count(*) n FROM core.dim_customer").n[0]


def test_portfolio_mart_reconciles_to_fact(q):
    p = q("SELECT month_index, total_payments, accounts FROM mart.portfolio_monthly ORDER BY 1")
    f = q("SELECT month_index, sum(payment_amount) s, count(*) n FROM core.fact_account_month GROUP BY 1 ORDER BY 1")
    assert np.allclose(p.total_payments, f.s) and (p.accounts.values == f.n.values).all()


def test_roll_rate_shares_sum_to_one(q):
    r = q("""SELECT from_month_index, from_bucket, sum(share_of_from_bucket) s
             FROM mart.roll_rates GROUP BY 1, 2""")
    assert np.allclose(r.s, 1.0)


def test_roll_rate_counts_match_customer_pairs(q):
    # every customer contributes exactly one transition per month pair (Apr->May ... Aug->Sep)
    n = q("SELECT sum(accounts) n FROM mart.roll_rates").n[0]
    assert n == 5 * q("SELECT count(*) n FROM core.dim_customer").n[0]


def test_config_month_mapping_is_complete():
    months = load_config()["months"]
    assert [m["month_index"] for m in months] == [1, 2, 3, 4, 5, 6]
    assert {m["status_col"] for m in months} == {"PAY_0", "PAY_2", "PAY_3", "PAY_4", "PAY_5", "PAY_6"}
