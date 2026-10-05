"""Train, evaluate and score the two models, then write results back to Postgres.

PD model (probability of default)
    unit: customer, features: Apr-Sep 2005, target: missed payment in Oct 2005
    split: stratified 80/20 customer split (there is only one label month, so an
           out-of-time test is not possible); model selection by 5-fold CV on the 80%
    scores used downstream: out-of-fold predictions, so no customer is scored by a
           model that saw their own label

Dormancy ("churn") model
    unit: customer x snapshot month, features: months t-1..t, target: inactive in t+1 and t+2
    split: train on the May snapshot, test on the Jul snapshot (out-of-time; May labels
           end in Jul, so nothing from the test period is used in training)
    scores used downstream: model refit on all labeled snapshots (May-Jul), applied to Sep
"""
from __future__ import annotations

import json
import logging

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss
from sklearn.model_selection import GridSearchCV, StratifiedKFold, cross_val_predict, train_test_split
from sklearn.pipeline import Pipeline

from src.config import load_config, path
from src.db import connect, replace_table
from src.evaluation import metrics as M
from src.evaluation import plots
from src.modeling import explain
from src.modeling.features import (CHURN_AMOUNTS, CHURN_FEATURES, CHURN_OTHER, PD_AMOUNTS, PD_FEATURES, PD_OTHER,
                                   load_churn_data, load_pd_data, make_preprocessor)

log = logging.getLogger(__name__)

GRIDS = {
    "logistic_regression": {"model__C": [0.01, 0.1, 1.0]},
    # Depth-limited: fully grown trees overfit badly on the rare dormancy target (train AUC 0.96
    # vs CV 0.89 in a first run) and make exact TreeSHAP far too slow to explain 27k customers.
    "random_forest": {"model__min_samples_leaf": [20, 50], "model__max_depth": [8, 12]},
    "lightgbm": {"model__num_leaves": [7, 15, 31], "model__min_child_samples": [50, 150]},
}
GRIDS["lightgbm_weighted"] = GRIDS["lightgbm"]

PD_RISK_BANDS = [(0.0, 0.10, "1. Low (<10%)"), (0.10, 0.30, "2. Medium (10-30%)"),
                 (0.30, 0.50, "3. High (30-50%)"), (0.50, 1.01, "4. Very high (50%+)")]


def make_pipeline(kind: str, amount_cols, other_cols, seed: int) -> Pipeline:
    if kind == "logistic_regression":
        prep, est = make_preprocessor(amount_cols, other_cols, scale=True), LogisticRegression(max_iter=5000)
    elif kind == "random_forest":
        prep = make_preprocessor(amount_cols, other_cols, scale=False)
        est = RandomForestClassifier(n_estimators=200, max_features="sqrt", n_jobs=-1, random_state=seed)
    elif kind in ("lightgbm", "lightgbm_weighted"):
        prep = make_preprocessor(amount_cols, other_cols, scale=False)
        est = LGBMClassifier(n_estimators=400, learning_rate=0.03, subsample=0.8, subsample_freq=1,
                             colsample_bytree=0.8, reg_lambda=1.0, random_state=seed, verbose=-1, n_jobs=4,
                             class_weight="balanced" if kind == "lightgbm_weighted" else None)
    else:
        raise ValueError(kind)
    return Pipeline([("prep", prep), ("model", est)])


def compare_models(X, y, amount_cols, other_cols, primary: str, kinds, cv, seed):
    """Grid search each candidate with the same CV folds. Returns a results table and the
    best (refit-on-full-train) pipeline per candidate."""
    rows, best = [], {}
    for kind in kinds:
        gs = GridSearchCV(make_pipeline(kind, amount_cols, other_cols, seed), GRIDS[kind],
                          scoring={"roc_auc": "roc_auc", "pr_auc": "average_precision"}, refit=primary,
                          cv=cv, return_train_score=True, n_jobs=1)
        gs.fit(X, y)
        r, i = gs.cv_results_, gs.best_index_
        rows.append({"model": kind, "best_params": json.dumps({k.replace("model__", ""): v for k, v in gs.best_params_.items()}),
                     "cv_roc_auc": r["mean_test_roc_auc"][i], "cv_roc_auc_std": r["std_test_roc_auc"][i],
                     "cv_pr_auc": r["mean_test_pr_auc"][i], "cv_pr_auc_std": r["std_test_pr_auc"][i],
                     "train_roc_auc": r["mean_train_roc_auc"][i], "train_pr_auc": r["mean_train_pr_auc"][i]})
        best[kind] = gs.best_estimator_
        log.info("%s: cv roc_auc=%.4f (sd %.4f) pr_auc=%.4f (sd %.4f) | train roc_auc=%.4f | %s", kind,
                 rows[-1]["cv_roc_auc"], rows[-1]["cv_roc_auc_std"], rows[-1]["cv_pr_auc"], rows[-1]["cv_pr_auc_std"],
                 rows[-1]["train_roc_auc"], rows[-1]["best_params"])
    return pd.DataFrame(rows), best


def choose_calibration(pipeline, X, y, cv):
    """Compare raw vs Platt (sigmoid) vs isotonic calibration on out-of-fold predictions
    within the training data. Only adds a calibration layer if it lowers the Brier score by
    more than 1%; otherwise the extra complexity isn't worth it."""
    rows = []
    for method in ["none", "sigmoid", "isotonic"]:
        est = wrap_calibration(pipeline, method)
        oof = cross_val_predict(est, X, y, cv=cv, method="predict_proba")[:, 1]
        rows.append({"method": method, "brier": brier_score_loss(y, oof),
                     "ece": M.expected_calibration_error(y, oof), "mean_predicted": oof.mean(), "base_rate": y.mean()})
    table = pd.DataFrame(rows)
    raw_brier = table.loc[table.method == "none", "brier"].iloc[0]
    best = table.sort_values("brier").iloc[0]
    chosen = best["method"] if best["brier"] < 0.99 * raw_brier else "none"
    log.info("calibration comparison:\n%s\nchosen: %s", table.round(5).to_string(index=False), chosen)
    return chosen, table


def wrap_calibration(pipeline, method: str):
    if method == "none":
        return clone(pipeline)
    # ensemble=False: one model fit on all training rows; calibrator fit on its CV predictions
    return CalibratedClassifierCV(clone(pipeline), method=method, cv=5, ensemble=False)


def test_comparison(best: dict, X_te, y_te) -> pd.DataFrame:
    """Every candidate on the held-out test set (reported only, NOT used to choose the model)."""
    rows = []
    for kind, est in best.items():
        p = est.predict_proba(X_te)[:, 1]
        m = M.summary_metrics(y_te, p, 0.5)
        rows.append({"model": kind, "test_roc_auc": m["roc_auc"], "test_pr_auc": m["pr_auc"], "test_brier": m["brier"]})
    return pd.DataFrame(rows)


def assign_band(p: np.ndarray) -> list[str]:
    out = []
    for v in p:
        out.append(next(label for lo, hi, label in PD_RISK_BANDS if lo <= v < hi))
    return out


# --------------------------------------------------------------------------- PD model
def run_pd(cfg) -> dict:
    seed, mcfg, prof = cfg["random_seed"], cfg["modeling"], cfg["profitability"]
    df = load_pd_data()
    X, y = df[PD_FEATURES], df["target"]
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=mcfg["test_size"], stratify=y, random_state=seed)
    cv = StratifiedKFold(mcfg["cv_folds"], shuffle=True, random_state=seed)

    cv_results, best = compare_models(X_tr, y_tr, PD_AMOUNTS, PD_OTHER, "roc_auc",
                                      ["logistic_regression", "random_forest", "lightgbm"], cv, seed)
    chosen = cv_results.sort_values("cv_roc_auc").iloc[-1]["model"]
    cal_method, cal_table = choose_calibration(best[chosen], X_tr, y_tr, cv)
    final = wrap_calibration(best[chosen], cal_method).fit(X_tr, y_tr)
    p_te = final.predict_proba(X_te)[:, 1]

    test_metrics = {f"at_{t}": M.summary_metrics(y_te, p_te, t) for t in (0.3, 0.5)}
    lift = M.lift_table(y_te, p_te)
    calib = M.calibration_table(y_te, p_te)
    thresholds = M.threshold_table(y_te, p_te)
    test_cmp = test_comparison(best, X_te, y_te)

    # Out-of-fold scores + SHAP for every customer (each fold model explains its own held-out rows)
    oof = pd.Series(np.nan, index=X.index)
    shap_parts = []
    folds = StratifiedKFold(mcfg["cv_folds"], shuffle=True, random_state=seed + 1)
    for k, (tr_idx, va_idx) in enumerate(folds.split(X, y)):
        m = wrap_calibration(best[chosen], cal_method).fit(X.iloc[tr_idx], y.iloc[tr_idx])
        oof.iloc[va_idx] = m.predict_proba(X.iloc[va_idx])[:, 1]
        shap_parts.append(explain.shap_frame(m, X.iloc[va_idx], background=X.iloc[tr_idx].sample(500, random_state=seed)))
        log.info("PD oof fold %d done", k + 1)
    shap_all = pd.concat(shap_parts).loc[X.index]
    reasons = explain.reason_codes(shap_all, X)
    importance = explain.global_importance(shap_all)

    # Expected-cost threshold: flag if p * C_FN > (1 - p) * C_FP  ->  t = C_FP / (C_FP + C_FN).
    # C_FN = average expected loss on a defaulter = EAD x LGD x charge-off share, where the
    # charge-off share is calibrated to the target annual loss rate (same formula as the
    # profitability mart, using the out-of-fold PDs).
    bal = df.bill_latest.clip(lower=0)
    ead = bal + prof["ccf"] * (df.credit_limit - bal).clip(lower=0)
    chargeoff_share = prof["target_annual_loss_rate"] * bal.sum() / (12 * (oof * ead * prof["lgd"]).sum())
    loss_if_default = ead * prof["lgd"] * chargeoff_share
    c_fn = float(loss_if_default.loc[X_tr.index][y_tr == 1].mean())
    cost_rows, yv = [], y_te.to_numpy()
    for c_fp in mcfg["pd_false_positive_costs"]:
        t = c_fp / (c_fp + c_fn)
        pred = p_te >= t
        cost = c_fn * ((~pred) & (yv == 1)).sum() + c_fp * (pred & (yv == 0)).sum()
        cost_rows.append({"c_fp": c_fp, "c_fn": c_fn, "threshold": t, "share_flagged": pred.mean(),
                          "precision": float((yv[pred] == 1).mean()) if pred.any() else np.nan,
                          "recall": float(pred[yv == 1].mean()),
                          "expected_cost_per_customer": cost / len(y_te)})
    cost_table = pd.DataFrame(cost_rows)
    log.info("calibrated charge-off share %.4f; cost thresholds:\n%s", chargeoff_share, cost_table.round(3).to_string())

    # fairness check: average predicted PD vs actual default rate by sex (sex is NOT a model input)
    fairness = (pd.DataFrame({"sex": df.sex, "pd": oof, "y": y}).groupby("sex")
                .agg(customers=("y", "size"), actual_default_rate=("y", "mean"), mean_pd=("pd", "mean")).reset_index())

    # model for future scoring, trained on all customers
    production = wrap_calibration(best[chosen], cal_method).fit(X, y)
    joblib.dump(production, path("models/pd_model.joblib"))

    scores = pd.DataFrame({
        "customer_id": df.customer_id.astype("int64"),
        "pd_score": oof.values,
        "pd_decile": pd.qcut(oof.rank(method="first", ascending=False), 10, labels=range(1, 11)).astype("int64").values,
        "pd_risk_band": assign_band(oof.values),
        "pd_drivers_up": reasons.drivers_up.values,
        "pd_drivers_down": reasons.drivers_down.values,
    })
    oof_metrics = M.summary_metrics(y, oof.values, 0.5)

    plots.model_figures("pd", y_te, p_te, calib, lift, shap_all, X, importance,
                        title="PD model (test set)", candidates={k: v.predict_proba(X_te)[:, 1] for k, v in best.items()})
    return {
        "chosen_model": chosen, "calibration": cal_method, "cv_results": cv_results, "calibration_table": cal_table,
        "test_comparison": test_cmp, "test_metrics": test_metrics, "oof_metrics": oof_metrics, "lift": lift,
        "calibration_curve": calib, "thresholds": thresholds, "cost_table": cost_table, "importance": importance,
        "fairness": fairness, "chargeoff_share": float(chargeoff_share), "scores": scores, "shap": shap_all, "X": X,
        "n_train": len(X_tr), "n_test": len(X_te),
    }


# --------------------------------------------------------------------------- dormancy model
def run_churn(cfg) -> dict:
    seed, ccfg, mcfg = cfg["random_seed"], cfg["churn"], cfg["modeling"]
    df = load_churn_data()
    labeled = df[df.churn_label.notna()].copy()
    labeled["churn_label"] = labeled.churn_label.astype(int)
    tr = labeled[labeled.snapshot_month_index == ccfg["train_snapshot"]]
    te = labeled[labeled.snapshot_month_index == ccfg["test_snapshot"]]
    assert tr.snapshot_month_index.max() + ccfg["prediction_months"] <= te.snapshot_month_index.min(), \
        "training labels must end before the test snapshot"
    X_tr, y_tr = tr[CHURN_FEATURES], tr.churn_label
    X_te, y_te = te[CHURN_FEATURES], te.churn_label
    cv = StratifiedKFold(mcfg["cv_folds"], shuffle=True, random_state=seed)

    cv_results, best = compare_models(X_tr, y_tr, CHURN_AMOUNTS, CHURN_OTHER, "pr_auc",
                                      ["logistic_regression", "random_forest", "lightgbm", "lightgbm_weighted"], cv, seed)
    chosen = cv_results.sort_values("cv_pr_auc").iloc[-1]["model"]
    cal_method, cal_table = choose_calibration(best[chosen], X_tr, y_tr, cv)
    final = wrap_calibration(best[chosen], cal_method).fit(X_tr, y_tr)
    p_te = final.predict_proba(X_te)[:, 1]

    # capacity-based threshold: the retention team can contact the top 10% of active customers
    budget = cfg["retention"]["budget_share"]
    t_budget = float(np.quantile(p_te, 1 - budget))
    test_metrics = {"at_top_10pct": M.summary_metrics(y_te, p_te, t_budget), "at_0.5": M.summary_metrics(y_te, p_te, 0.5)}
    lift = M.lift_table(y_te, p_te)
    calib = M.calibration_table(y_te, p_te, n_bins=5)  # few positives -> fewer bins
    test_cmp = test_comparison(best, X_te, y_te)
    train_metrics = M.summary_metrics(y_tr, final.predict_proba(X_tr)[:, 1], t_budget)

    # production model: all labeled snapshots (May, Jun, Jul), scored on the Sep snapshot
    production = wrap_calibration(best[chosen], cal_method).fit(labeled[CHURN_FEATURES], labeled.churn_label)
    joblib.dump(production, path("models/churn_model.joblib"))
    score = df[df.snapshot_month_index == ccfg["score_snapshot"]].reset_index(drop=True)
    X_sc = score[CHURN_FEATURES]
    p_sc = production.predict_proba(X_sc)[:, 1]
    shap_sc = explain.shap_frame(production, X_sc, background=labeled[CHURN_FEATURES].sample(500, random_state=seed))
    reasons = explain.reason_codes(shap_sc, X_sc)
    importance = explain.global_importance(shap_sc)

    scores = pd.DataFrame({
        "customer_id": score.customer_id.astype("int64"),
        "snapshot_month_index": score.snapshot_month_index.astype("int64"),
        "churn_score": p_sc,
        "churn_decile": pd.qcut(pd.Series(p_sc).rank(method="first", ascending=False), 10,
                                labels=range(1, 11)).astype("int64").values,
        "churn_drivers_up": reasons.drivers_up.values,
        "churn_drivers_down": reasons.drivers_down.values,
    })
    plots.model_figures("churn", y_te, p_te, calib, lift, shap_sc, X_sc, importance,
                        title="Dormancy model (out-of-time test, Jul 2005 snapshot)",
                        candidates={k: v.predict_proba(X_te)[:, 1] for k, v in best.items()})
    return {
        "chosen_model": chosen, "calibration": cal_method, "cv_results": cv_results, "calibration_table": cal_table,
        "test_comparison": test_cmp, "test_metrics": test_metrics, "train_metrics": train_metrics, "lift": lift,
        "calibration_curve": calib, "importance": importance, "scores": scores, "shap": shap_sc, "X": X_sc,
        "threshold_top10": t_budget, "n_train": len(X_tr), "n_test": len(X_te),
        "train_rate": float(y_tr.mean()), "test_rate": float(y_te.mean()), "score_rows": len(score),
        "labeled_rows_production": len(labeled),
    }


# --------------------------------------------------------------------------- persistence
def _to_jsonable(obj):
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    return obj


def save_results(pd_res: dict, churn_res: dict, seg) -> None:
    from src.analytics.assumptions import write_assumptions

    out_dir = path("reports/tables")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for task, res in (("pd", pd_res), ("churn", churn_res)):
        keep = {k: v for k, v in res.items() if k not in ("scores", "shap", "X")}
        summary[task] = _to_jsonable(keep)
        for name in ("cv_results", "test_comparison", "lift", "calibration_table", "importance"):
            res[name].to_csv(out_dir / f"{task}_{name}.csv", index=False)
    pd_res["thresholds"].to_csv(out_dir / "pd_thresholds.csv", index=False)
    pd_res["cost_table"].to_csv(out_dir / "pd_cost_thresholds.csv", index=False)
    pd_res["fairness"].to_csv(out_dir / "pd_fairness_by_sex.csv", index=False)
    summary["segmentation"] = _to_jsonable(seg["summary"])
    with open(path("reports/model_results.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)

    long_rows = []
    for task, res in (("pd", pd_res), ("churn", churn_res)):
        for _, r in res["cv_results"].iterrows():
            for metric in ("cv_roc_auc", "cv_pr_auc", "train_roc_auc", "train_pr_auc"):
                long_rows.append({"task": task, "model": r.model, "metric": metric, "value": float(r[metric])})
        for _, r in res["test_comparison"].iterrows():
            for metric in ("test_roc_auc", "test_pr_auc", "test_brier"):
                long_rows.append({"task": task, "model": r.model, "metric": metric, "value": float(r[metric])})
    lifts = pd.concat([pd_res["lift"].assign(task="pd"), churn_res["lift"].assign(task="churn")])
    lifts["decile"] = lifts.decile.astype("int64")
    lifts["n"] = lifts.n.astype("int64")
    lifts["positives"] = lifts.positives.astype("int64")
    imps = pd.concat([pd_res["importance"].assign(task="pd"), churn_res["importance"].assign(task="churn")])
    imps["rank"] = imps["rank"].astype("int64")
    cal = pd.concat([pd_res["calibration_curve"].assign(task="pd"), churn_res["calibration_curve"].assign(task="churn")])

    with connect() as conn:
        replace_table(conn, pd_res["scores"], "ml.pd_scores", ["customer_id"])
        replace_table(conn, churn_res["scores"], "ml.churn_scores", ["customer_id"])
        replace_table(conn, pd.DataFrame(long_rows), "ml.model_comparison")
        replace_table(conn, lifts.reset_index(drop=True), "ml.model_lift", ["task", "decile"])
        replace_table(conn, imps.reset_index(drop=True), "ml.feature_importance", ["task", "feature"])
        replace_table(conn, cal.reset_index(drop=True), "ml.model_calibration")
        replace_table(conn, seg["assignments"], "ml.customer_segments", ["customer_id"])
        replace_table(conn, seg["profile"], "ml.segment_profile", ["segment"])
        write_assumptions(conn)
    log.info("model outputs written to ml.* tables and reports/")


def run() -> None:
    from src.analytics.segmentation import run as run_segmentation

    cfg = load_config()
    path("models").mkdir(exist_ok=True)
    pd_res = run_pd(cfg)
    churn_res = run_churn(cfg)
    seg = run_segmentation(cfg)
    save_results(pd_res, churn_res, seg)
