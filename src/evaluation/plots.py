"""Figures for model evaluation (saved to reports/figures/).

One shared style: thin marks, recessive grid, fixed categorical colour order.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve, roc_curve

from src.config import path

# Validated categorical order (blue, orange, aqua, yellow, ...) and chart chrome
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SEQ = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"
NICE_NAMES = {"logistic_regression": "Logistic regression", "random_forest": "Random forest",
              "lightgbm": "LightGBM", "lightgbm_weighted": "LightGBM (class-weighted)"}


def apply_style():
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "axes.titlecolor": INK, "axes.titlesize": 11,
        "axes.titleweight": "bold", "axes.titlelocation": "left", "axes.labelsize": 9,
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True,
        "grid.color": GRID, "grid.linewidth": 0.6, "xtick.color": MUTED, "ytick.color": MUTED,
        "xtick.labelsize": 8, "ytick.labelsize": 8, "legend.fontsize": 8, "legend.frameon": False,
        "lines.linewidth": 2, "axes.axisbelow": True, "text.parse_math": False, "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
        "axes.prop_cycle": matplotlib.cycler(color=SERIES),
    })


apply_style()


def fig_dir():
    d = path("reports/figures")
    d.mkdir(parents=True, exist_ok=True)
    return d


def model_figures(task, y_te, p_te, calib: pd.DataFrame, lift: pd.DataFrame, shap_df: pd.DataFrame,
                  X: pd.DataFrame, importance: pd.DataFrame, title: str, candidates: dict) -> None:
    out = fig_dir()
    y_te = np.asarray(y_te)

    # ROC + PR for every candidate (same test set)
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    for i, (name, p) in enumerate(candidates.items()):
        fpr, tpr, _ = roc_curve(y_te, p)
        prec, rec, _ = precision_recall_curve(y_te, p)
        axes[0].plot(fpr, tpr, color=SERIES[i], label=NICE_NAMES.get(name, name))
        axes[1].plot(rec, prec, color=SERIES[i], label=NICE_NAMES.get(name, name))
    axes[0].plot([0, 1], [0, 1], color=AXIS, lw=1, ls="--")
    axes[1].axhline(y_te.mean(), color=AXIS, lw=1, ls="--")
    axes[1].text(0.99, y_te.mean(), f"base rate {y_te.mean():.1%}", ha="right", va="bottom", color=MUTED, fontsize=8)
    axes[0].set(title="ROC curve", xlabel="False positive rate", ylabel="True positive rate")
    axes[1].set(title="Precision-recall curve", xlabel="Recall", ylabel="Precision")
    axes[0].legend(loc="lower right")
    fig.suptitle(title, x=0.01, ha="left", fontsize=12, fontweight="bold", color=INK)
    fig.tight_layout()
    fig.savefig(out / f"{task}_roc_pr.png", dpi=150)
    plt.close(fig)

    # calibration + lift
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.2))
    lim = max(calib.mean_predicted.max(), calib.observed_rate.max()) * 1.05
    axes[0].plot([0, lim], [0, lim], color=AXIS, lw=1, ls="--")
    axes[0].plot(calib.mean_predicted, calib.observed_rate, marker="o", ms=6, color=SERIES[0])
    axes[0].set(title="Calibration (chosen model)", xlabel="Mean predicted probability", ylabel="Observed rate",
                xlim=(0, lim), ylim=(0, lim))
    axes[1].bar(lift.decile.astype(int), lift.observed_rate, color=SERIES[0], width=0.7)
    axes[1].axhline(y_te.mean(), color=INK2, lw=1, ls="--")
    axes[1].text(10.4, y_te.mean(), "average", va="bottom", ha="right", color=INK2, fontsize=8)
    axes[1].set(title="Observed rate by predicted-risk decile", xlabel="Decile (1 = highest predicted risk)",
                ylabel="Observed rate", xticks=range(1, 11))
    fig.tight_layout()
    fig.savefig(out / f"{task}_calibration_lift.png", dpi=150)
    plt.close(fig)

    # global SHAP importance (top 15)
    top = importance.head(15).iloc[::-1]
    fig, ax = plt.subplots(figsize=(7, 5))
    ax.barh(top.feature_label, top.mean_abs_shap, color=SERIES[0], height=0.6)
    ax.set(title="Global feature importance (mean |SHAP|)", xlabel="Mean absolute SHAP value (log-odds for LightGBM/LR, probability for random forest)")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    fig.savefig(out / f"{task}_shap_importance.png", dpi=150)
    plt.close(fig)

    # SHAP beeswarm-style plot: direction of effect for the top numeric features
    import shap
    num_cols = [f for f in importance.feature if f in X.columns and pd.api.types.is_numeric_dtype(X[f])][:12]
    sample = shap_df.sample(min(4000, len(shap_df)), random_state=0).index
    plt.figure()
    shap.summary_plot(shap_df.loc[sample, num_cols].to_numpy(), X.loc[sample, num_cols].astype(float),
                      feature_names=[importance.set_index("feature").feature_label[f] for f in num_cols],
                      show=False, plot_size=(8, 5.5))
    plt.title("SHAP values: how each feature moves the prediction", loc="left", fontsize=11, fontweight="bold")
    plt.tight_layout()
    plt.savefig(out / f"{task}_shap_beeswarm.png", dpi=150)
    plt.close("all")
