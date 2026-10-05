"""Behavioral customer segmentation with K-means.

Inputs are six behavior ratios (no demographics, no model scores), so the segments describe
how customers use the card. I checked k = 2..8 with silhouette score and bootstrap stability
(adjusted Rand index between the full-data clustering and clusterings fit on resamples).
k = 4 has the best silhouette (0.44) and k = 5 is close (0.42); both are very stable (ARI ~0.99).
I use k = 5 because the extra cluster separates heavy-spend revolvers, whose economics
(interchange + interest) differ from ordinary revolvers. Cluster names are assigned from the
centroids by rule, so labels don't depend on K-means' arbitrary cluster numbering.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import adjusted_rand_score, silhouette_score
from sklearn.preprocessing import StandardScaler

from src.db import read_sql

log = logging.getLogger(__name__)

SEGMENT_FEATURES = ["util", "pay_ratio", "pif_share", "inactive_share", "spend_to_limit", "dlq_share"]
CHOSEN_K = 5


def segment_inputs(f: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame({
        "util": f.util_avg_6m.clip(0, 1.2),                  # average utilization, capped
        "pay_ratio": f.payment_ratio_avg.fillna(1.0),        # nothing ever owed -> treat as paying in full
        "pif_share": f.months_paid_in_full / 5,              # share of months paid in full
        "inactive_share": f.months_inactive_5m / 5,          # share of months dormant
        "spend_to_limit": f.new_charges_to_limit.clip(0, 0.5),
        "dlq_share": f.months_60plus_6m / 6,                 # share of months 60+ days late
    }, index=f.index)


def name_clusters(centroids: pd.DataFrame) -> dict[int, str]:
    names, remaining = {}, list(centroids.index)
    for col, label in [("dlq_share", "Delinquent revolvers"), ("inactive_share", "Dormant / low use"),
                       ("pif_share", "Transactors")]:
        c = centroids.loc[remaining, col].idxmax()
        names[c] = label
        remaining.remove(c)
    rest = centroids.loc[remaining].sort_values("spend_to_limit", ascending=False).index
    names[rest[0]] = "Heavy-spend revolvers"
    for c in rest[1:]:
        names[c] = "Revolvers"
    return names


def evaluate_k(Z: np.ndarray, ks=range(2, 9), seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    sample = rng.choice(len(Z), min(6000, len(Z)), replace=False)
    rows = []
    for k in ks:
        km = KMeans(k, n_init=10, random_state=seed).fit(Z)
        aris = []
        for s in range(3):
            boot = rng.choice(len(Z), len(Z), replace=True)
            aris.append(adjusted_rand_score(km.labels_, KMeans(k, n_init=10, random_state=seed + s + 1).fit(Z[boot]).predict(Z)))
        rows.append({"k": k, "inertia": km.inertia_, "silhouette": silhouette_score(Z[sample], km.labels_[sample]),
                     "stability_ari": float(np.mean(aris))})
    return pd.DataFrame(rows)


def run(cfg) -> dict:
    seed = cfg["random_seed"]
    f = read_sql("SELECT * FROM features.customer_risk_features ORDER BY customer_id")
    X = segment_inputs(f)
    Z = StandardScaler().fit_transform(X)
    k_table = evaluate_k(Z, seed=seed)
    log.info("segmentation k selection:\n%s", k_table.round(3).to_string(index=False))

    km = KMeans(CHOSEN_K, n_init=20, random_state=seed).fit(Z)
    centroids = X.groupby(km.labels_).mean()
    names = name_clusters(centroids)
    segment = pd.Series(km.labels_).map(names)

    assignments = pd.DataFrame({"customer_id": f.customer_id.astype("int64"), "cluster_id": km.labels_.astype("int64"),
                                "segment": segment.values})
    profile = X.assign(segment=segment.values, credit_limit=f.credit_limit).groupby("segment").agg(
        customers=("util", "size"), avg_utilization=("util", "mean"), avg_payment_ratio=("pay_ratio", "mean"),
        share_months_paid_in_full=("pif_share", "mean"), share_months_inactive=("inactive_share", "mean"),
        new_charges_to_limit=("spend_to_limit", "mean"), share_months_60plus=("dlq_share", "mean"),
        avg_credit_limit=("credit_limit", "mean")).reset_index()
    profile["customers"] = profile.customers.astype("int64")
    log.info("segment profile:\n%s", profile.round(3).to_string(index=False))
    return {"assignments": assignments, "profile": profile,
            "summary": {"chosen_k": CHOSEN_K, "k_selection": k_table, "profile": profile}}
