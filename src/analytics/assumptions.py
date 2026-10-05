"""Writes the profitability/retention assumptions from config.yaml into mart.assumptions.

The profitability SQL reads its parameters from this table, so the numbers in the database,
the dashboard and the config file can't drift apart.
"""
from __future__ import annotations

import pandas as pd

from src.config import load_config
from src.db import replace_table

DESCRIPTIONS = {
    "apr": "Annual interest rate on revolving balances (assumed)",
    "interchange_rate": "Issuer interchange as share of purchase volume (assumed)",
    "late_fee": "Late fee per delinquent month, NTD (assumed)",
    "rewards_rate": "Rewards cost as share of purchase volume (assumed)",
    "cost_of_funds_annual": "Annual funding cost on drawn balance (assumed)",
    "servicing_cost_monthly": "Monthly servicing cost per account, NTD (assumed)",
    "ccf": "Credit conversion factor applied to undrawn limit for EAD (assumed)",
    "lgd": "Loss given default for a charged-off unsecured balance (assumed)",
    "target_annual_loss_rate": "Annual expected loss as share of balances used to calibrate ECL level (assumed; Fed card charge-off benchmark ~3-10.5%)",
    "horizon_months": "Months of contribution lost if a customer goes dormant (assumed)",
    "contact_cost": "Cost of one retention contact/offer, NTD (assumed)",
    "save_rate": "Share of would-be churners retained by an offer (assumed; needs an experiment)",
    "budget_share": "Share of active customers the retention team can contact (scenario)",
}


def write_assumptions(conn) -> None:
    cfg = load_config()
    rows = []
    for group in ("profitability", "retention"):
        for k, v in cfg[group].items():
            rows.append({"parameter": k, "value": float(v), "parameter_group": group,
                         "description": DESCRIPTIONS.get(k, "")})
    replace_table(conn, pd.DataFrame(rows), "mart.assumptions", ["parameter"])
