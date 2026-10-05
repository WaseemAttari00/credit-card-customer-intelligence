"""Profitability, scores, segments and retention-priority rules."""
import numpy as np
import pytest

from src.config import load_config


@pytest.fixture(autouse=True)
def _needs_marts(require):
    require("mart.customer_profitability", "mart.customer_360", "mart.retention_priority", "ml.pd_scores")


def test_profit_components_add_up(q):
    p = q("SELECT * FROM mart.customer_profitability")
    rev = p.est_interest_income + p.est_interchange_income + p.est_fee_income
    cost = p.est_rewards_cost + p.est_funding_cost + p.est_servicing_cost
    assert np.allclose(p.est_monthly_revenue, rev)
    assert np.allclose(p.est_contribution_before_losses, rev - cost)
    assert np.allclose(p.est_risk_adjusted_contribution, rev - cost - p.ecl_next_month)


def test_profit_inputs_are_non_negative(q):
    p = q("SELECT * FROM mart.customer_profitability")
    for c in ["est_interest_income", "est_interchange_income", "est_fee_income", "est_rewards_cost",
              "est_funding_cost", "ecl_next_month", "est_monthly_purchases", "ead_estimate"]:
        assert (p[c] >= 0).all(), c


def test_ead_between_drawn_balance_and_limit(q):
    p = q("SELECT obs_balance_sep b, obs_credit_limit l, ead_estimate e FROM mart.customer_profitability")
    drawn = p.b.clip(lower=0)
    assert (p.e >= drawn - 1e-6).all()
    assert (p.e <= np.maximum(drawn, p.l) + 1e-6).all()


def test_interest_matches_hand_calculation(q):
    """Recompute one customer's estimated interest from the fact table and the configured APR."""
    apr = load_config()["profitability"]["apr"]
    cid = int(q("SELECT customer_id FROM core.fact_account_month WHERE month_index = 6 AND revolving_balance > 10000 "
                "ORDER BY customer_id LIMIT 1").customer_id[0])
    f = q("SELECT revolving_balance FROM core.fact_account_month WHERE customer_id = %s AND month_index >= 2", (cid,))
    expected = (f.revolving_balance * apr / 12).mean()
    got = q("SELECT est_interest_income FROM mart.customer_profitability WHERE customer_id = %s", (cid,)).iloc[0, 0]
    assert got == pytest.approx(expected)


def test_ecl_formula(q):
    a = load_config()["profitability"]
    p = q("SELECT pd_score, ead_estimate, chargeoff_share, ecl_next_month FROM mart.customer_profitability")
    assert p.chargeoff_share.nunique() == 1 and 0 < p.chargeoff_share[0] < 1
    assert np.allclose(p.ecl_next_month, p.pd_score * p.ead_estimate * a["lgd"] * p.chargeoff_share)


def test_ecl_level_matches_target_loss_rate(q):
    """Top-down calibration: 12 x monthly ECL / balances must equal the configured annual loss rate."""
    target = load_config()["profitability"]["target_annual_loss_rate"]
    r = q("SELECT 12 * sum(ecl_next_month) / sum(greatest(obs_balance_sep, 0)) rate FROM mart.customer_profitability")
    assert r.rate[0] == pytest.approx(target)


def test_scores_are_probabilities(q):
    s = q("SELECT min(pd_score) a, max(pd_score) b FROM ml.pd_scores")
    c = q("SELECT min(churn_score) a, max(churn_score) b FROM ml.churn_scores")
    assert 0 <= s.a[0] and s.b[0] <= 1 and 0 <= c.a[0] and c.b[0] <= 1


def test_deciles_are_balanced_and_ordered(q):
    d = q("SELECT pd_decile, count(*) n, avg(pd_score) m FROM ml.pd_scores GROUP BY 1 ORDER BY 1")
    assert list(d.pd_decile) == list(range(1, 11))
    assert d.n.max() - d.n.min() <= 1
    assert d.m.is_monotonic_decreasing      # decile 1 = highest risk


def test_churn_scores_only_for_active_customers(q):
    r = q("""SELECT count(*) n FROM ml.churn_scores s
             JOIN features.churn_snapshots f ON f.customer_id = s.customer_id AND f.snapshot_month_index = 6
             WHERE NOT f.is_eligible""")
    assert r.n[0] == 0


def test_every_customer_has_one_segment(q):
    r = q("SELECT count(*) n, count(DISTINCT customer_id) d, count(DISTINCT segment) k FROM ml.customer_segments")
    assert r.n[0] == r.d[0] == q("SELECT count(*) n FROM core.dim_customer").n[0]
    assert r.k[0] == 5


def test_customer_360_does_not_fan_out(q):
    r = q("SELECT count(*) n, count(DISTINCT customer_id) d FROM mart.customer_360")
    assert r.n[0] == r.d[0] == q("SELECT count(*) n FROM core.dim_customer").n[0]
    assert q("SELECT count(*) n FROM mart.retention_priority").n[0] == q("SELECT count(*) n FROM ml.churn_scores").n[0]


def test_high_credit_risk_never_targeted_for_retention(q):
    r = q("""SELECT count(*) n FROM mart.retention_priority
             WHERE pd_score >= 0.5 AND priority_tier IN ('1. Contact now', '2. Next wave')""")
    assert r.n[0] == 0


def test_contact_list_respects_budget_and_economics(q):
    budget = load_config()["retention"]["budget_share"]
    r = q("""SELECT count(*) FILTER (WHERE priority_tier = '1. Contact now') contact,
                    count(*) active,
                    count(*) FILTER (WHERE priority_tier IN ('1. Contact now', '2. Next wave')
                                     AND (expected_net_benefit <= 0 OR est_risk_adjusted_contribution <= 0)) bad
             FROM mart.retention_priority""")
    assert r.contact[0] <= np.floor(budget * r.active[0])
    assert r.bad[0] == 0


def test_contact_now_ranked_above_next_wave(q):
    r = q("""SELECT max(priority_rank) FILTER (WHERE priority_tier = '1. Contact now') last_contact,
                    min(priority_rank) FILTER (WHERE priority_tier = '2. Next wave') first_next,
                    min(expected_net_benefit) FILTER (WHERE priority_tier = '1. Contact now') min_contact,
                    max(expected_net_benefit) FILTER (WHERE priority_tier = '2. Next wave') max_next
             FROM mart.retention_priority""")
    if r.first_next.notna()[0] and r.last_contact.notna()[0]:
        assert r.last_contact[0] < r.first_next[0]
        assert r.min_contact[0] >= r.max_next[0]
