"""Model input integrity and saved-model sanity checks."""
import joblib
import numpy as np
import pytest

from src.config import path
from src.modeling.features import CHURN_FEATURES, PD_FEATURES, load_churn_data, load_pd_data

PROTECTED = {"sex", "marital_status"}
ID_OR_LABEL = {"customer_id", "target", "churn_label", "defaulted_next_month", "snapshot_month_index", "is_eligible"}


def test_feature_lists_exclude_ids_labels_and_protected_attributes():
    for feats in (PD_FEATURES, CHURN_FEATURES):
        assert not set(feats) & ID_OR_LABEL
        assert not set(feats) & PROTECTED
        assert len(feats) == len(set(feats))


@pytest.fixture(scope="module")
def pd_data(require):
    require("features.customer_risk_features", "core.fact_default_outcome")
    return load_pd_data()


@pytest.fixture(scope="module")
def churn_data(require):
    require("features.churn_snapshots")
    return load_churn_data()


def test_pd_frame_complete_and_finite(pd_data):
    assert pd_data.customer_id.is_unique
    assert set(PD_FEATURES) <= set(pd_data.columns)
    num = pd_data[PD_FEATURES].select_dtypes("number")
    assert np.isfinite(num.fillna(0).to_numpy()).all()
    # only the payment-ratio features may be missing (nothing owed in any month)
    allowed_missing = {"payment_ratio_avg", "payment_ratio_latest", "payment_ratio_min"}
    assert set(num.columns[num.isna().any()]) <= allowed_missing
    assert pd_data.target.isin([0, 1]).all()


def test_churn_frame_only_eligible_and_finite(churn_data):
    assert churn_data.is_eligible.all()
    assert not churn_data.duplicated(["customer_id", "snapshot_month_index"]).any()
    num = churn_data[CHURN_FEATURES].select_dtypes("number")
    assert np.isfinite(num.fillna(0).to_numpy()).all()


def test_saved_models_load_and_predict(pd_data, churn_data):
    pd_path, churn_path = path("models/pd_model.joblib"), path("models/churn_model.joblib")
    if not (pd_path.exists() and churn_path.exists()):
        pytest.skip("models not trained yet")
    p = joblib.load(pd_path).predict_proba(pd_data[PD_FEATURES].head(200))[:, 1]
    c = joblib.load(churn_path).predict_proba(churn_data[CHURN_FEATURES].head(200))[:, 1]
    assert ((0 <= p) & (p <= 1)).all() and ((0 <= c) & (c <= 1)).all()
    assert p.std() > 0 and c.std() > 0       # not a constant model


def test_oof_pd_scores_cover_all_customers_and_beat_random(q, require):
    require("ml.pd_scores")
    r = q("""SELECT count(*) n, avg(pd_score) m, avg(o.defaulted_next_month::INT) y
             FROM ml.pd_scores JOIN core.fact_default_outcome o USING (customer_id)""")
    assert r.n[0] == q("SELECT count(*) n FROM core.dim_customer").n[0]
    assert abs(r.m[0] - r.y[0]) < 0.02          # calibrated in the large
    top = q("""SELECT avg(o.defaulted_next_month::INT) y FROM ml.pd_scores p
               JOIN core.fact_default_outcome o USING (customer_id) WHERE p.pd_decile = 1""").y[0]
    assert top > 2 * r.y[0]                      # top decile at least 2x the base rate
