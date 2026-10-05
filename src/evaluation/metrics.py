"""Evaluation helpers: classification metrics, calibration, threshold and lift tables."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.metrics import (average_precision_score, brier_score_loss, confusion_matrix, f1_score,
                             log_loss, precision_score, recall_score, roc_auc_score)


def summary_metrics(y_true, p, threshold: float) -> dict:
    y_true = np.asarray(y_true)
    pred = (p >= threshold).astype(int)
    tn, fp, fn, tp = confusion_matrix(y_true, pred, labels=[0, 1]).ravel()
    return {
        "n": int(len(y_true)),
        "base_rate": float(y_true.mean()),
        "roc_auc": float(roc_auc_score(y_true, p)),
        "gini": float(2 * roc_auc_score(y_true, p) - 1),
        "pr_auc": float(average_precision_score(y_true, p)),
        "brier": float(brier_score_loss(y_true, p)),
        "log_loss": float(log_loss(y_true, np.clip(p, 1e-6, 1 - 1e-6))),
        "mean_predicted": float(np.mean(p)),
        "threshold": float(threshold),
        "precision": float(precision_score(y_true, pred, zero_division=0)),
        "recall": float(recall_score(y_true, pred, zero_division=0)),
        "f1": float(f1_score(y_true, pred, zero_division=0)),
        "tp": int(tp), "fp": int(fp), "tn": int(tn), "fn": int(fn),
    }


def calibration_table(y_true, p, n_bins: int = 10) -> pd.DataFrame:
    """Observed vs predicted rate per bin of predicted probability (quantile bins)."""
    frac_pos, mean_pred = calibration_curve(y_true, p, n_bins=n_bins, strategy="quantile")
    return pd.DataFrame({"mean_predicted": mean_pred, "observed_rate": frac_pos})


def expected_calibration_error(y_true, p, n_bins: int = 10) -> float:
    df = pd.DataFrame({"y": np.asarray(y_true), "p": p})
    df["bin"] = pd.qcut(df["p"].rank(method="first"), n_bins, labels=False)
    g = df.groupby("bin").agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size"))
    return float((g.n * (g.p - g.y).abs()).sum() / g.n.sum())


def threshold_table(y_true, p, thresholds=None) -> pd.DataFrame:
    y_true = np.asarray(y_true)
    thresholds = thresholds if thresholds is not None else np.round(np.arange(0.1, 0.91, 0.1), 2)
    rows = []
    for t in thresholds:
        pred = p >= t
        tp = int((pred & (y_true == 1)).sum())
        fp = int((pred & (y_true == 0)).sum())
        fn = int((~pred & (y_true == 1)).sum())
        rows.append({"threshold": t, "share_flagged": pred.mean(),
                     "precision": tp / (tp + fp) if tp + fp else np.nan,
                     "recall": tp / (tp + fn) if tp + fn else np.nan, "tp": tp, "fp": fp, "fn": fn})
    return pd.DataFrame(rows)


def lift_table(y_true, p, n_bins: int = 10) -> pd.DataFrame:
    """Deciles of predicted risk (1 = highest). Cumulative capture = share of all positives
    found by targeting the top k deciles."""
    df = pd.DataFrame({"y": np.asarray(y_true), "p": p})
    df["decile"] = pd.qcut(df["p"].rank(method="first", ascending=False), n_bins, labels=range(1, n_bins + 1))
    g = df.groupby("decile", observed=True).agg(n=("y", "size"), positives=("y", "sum"),
                                                 mean_predicted=("p", "mean"))
    g["observed_rate"] = g.positives / g.n
    g["lift"] = g.observed_rate / df.y.mean()
    g["cum_capture"] = g.positives.cumsum() / df.y.sum()
    return g.reset_index()
