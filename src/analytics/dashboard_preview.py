"""Static previews of the four planned Power BI pages, drawn with matplotlib from the same
tables that are exported for Power BI. These are previews of the layout and numbers, NOT
Power BI screenshots (Power BI Desktop was not available in the build environment)."""
from __future__ import annotations

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.gridspec import GridSpec

from src.config import path
from src.db import connect, read_sql
from src.evaluation.plots import AXIS, GRID, INK, INK2, MUTED, SEQ, SERIES, STATUS, SURFACE

SEGMENT_ORDER = ["Transactors", "Heavy-spend revolvers", "Revolvers", "Dormant / low use", "Delinquent revolvers"]
SEG_COLOR = dict(zip(SEGMENT_ORDER, SERIES))
TIER_COLOR = {"1. Contact now": SERIES[0], "2. Next wave": SEQ[2], "3. Monitor - offer costs more than expected gain": "#c3c2b7",
              "4. Exclude - not profitable": STATUS["serious"], "5. Exclude - high credit risk": STATUS["critical"]}


def ntd(v, short=True):
    a = abs(v)
    if short and a >= 1e9:
        return f"NT${v/1e9:,.2f}B"
    if short and a >= 1e6:
        return f"NT${v/1e6:,.1f}M"
    if short and a >= 1e3:
        return f"NT${v/1e3:,.0f}k"
    return f"NT${v:,.0f}"


def page(title, subtitle):
    fig = plt.figure(figsize=(16, 9.4))
    fig.patch.set_facecolor(SURFACE)
    fig.text(0.015, 0.975, title, fontsize=17, fontweight="bold", color=INK, va="top")
    fig.text(0.015, 0.94, subtitle, fontsize=9.5, color=INK2, va="top")
    fig.text(0.985, 0.012, "Python preview of the planned Power BI page, built from the exported tables. "
             "NTD figures marked est. are estimates based on documented assumptions.",
             fontsize=7.5, color=MUTED, ha="right")
    return fig


def kpi_row(fig, tiles, top=0.90, height=0.085):
    n = len(tiles)
    w = 0.97 / n
    for i, (label, value, note) in enumerate(tiles):
        ax = fig.add_axes([0.015 + i * w, top - height, w - 0.008, height])
        ax.set_facecolor("#f3f2ee")
        ax.set_xticks([]), ax.set_yticks([])
        for s in ax.spines.values():
            s.set_visible(False)
        ax.text(0.06, 0.78, label, fontsize=8.5, color=INK2, transform=ax.transAxes, va="center")
        ax.text(0.06, 0.40, value, fontsize=15, fontweight="bold", color=INK, transform=ax.transAxes, va="center")
        ax.text(0.06, 0.10, note, fontsize=7, color=MUTED, transform=ax.transAxes, va="center")


def tidy(ax, title, xlabel=None, ylabel=None):
    ax.set_title(title, fontsize=10.5, fontweight="bold", color=INK, loc="left", pad=8)
    if xlabel is not None:
        ax.set_xlabel(xlabel)
    if ylabel is not None:
        ax.set_ylabel(ylabel)


def hbar(ax, labels, values, fmt, colors=None, title=""):
    y = np.arange(len(labels))[::-1]
    ax.barh(y, values, color=colors or SERIES[0], height=0.62)
    ax.set_yticks(y, labels)
    ax.grid(axis="y", visible=False)
    span = (max(values) - min(min(values), 0)) or 1
    for yi, v in zip(y, values):
        ax.text(v + span * 0.01 * (1 if v >= 0 else -1), yi, fmt(v), va="center",
                ha="left" if v >= 0 else "right", fontsize=8, color=INK2)
    ax.axvline(0, color=AXIS, lw=1)
    lo = min(min(values), 0)
    ax.set_xlim(lo - span * (0.18 if lo < 0 else 0), max(values) + span * 0.18)
    tidy(ax, title)


def load(conn):
    q = lambda s: read_sql(s, conn)
    sql = lambda name: read_sql(path(f"sql/analysis/{name}.sql").read_text(encoding="utf-8"), conn)
    return {
        "kpi": sql("executive_kpis").iloc[0],
        "seg": sql("segment_summary").set_index("segment").reindex(SEGMENT_ORDER),
        "risk": sql("risk_by_group"),
        "budget": sql("retention_budget_curve"),
        "conc": sql("value_concentration"),
        "port": q("SELECT * FROM mart.portfolio_monthly ORDER BY month_index"),
        "roll": q("SELECT from_bucket, to_bucket, sum(accounts) n FROM mart.roll_rates WHERE from_month_index <= 4 "
                  "AND from_bucket <> '1. 30 DPD' GROUP BY 1, 2"),
        "c360": q("SELECT segment, limit_tier, pd_score, pd_risk_band, pd_decile, actual_default_oct::INT y, "
                  "est_monthly_purchases FROM mart.customer_360"),
        "ret": q("SELECT * FROM mart.retention_priority"),
        "lift": q("SELECT * FROM ml.model_lift WHERE task = 'pd' ORDER BY decile"),
    }


def page_executive(d):
    k, port, seg = d["kpi"], d["port"], d["seg"]
    fig = page("1  Executive overview", "Portfolio of 30,000 Taiwanese credit card customers, Apr-Sep 2005. "
               "Question: how big is the book, is it getting riskier, and where does the value come from?")
    kpi_row(fig, [
        ("Customers", f"{int(k.customers):,}", f"{int(k.active_customers_sep):,} active in Sep"),
        ("Balance outstanding (Sep)", ntd(k.total_balance_sep), f"{k.portfolio_utilization:.0%} of total limit"),
        ("Est. monthly revenue", ntd(k.est_monthly_revenue), "interest + interchange + fees (est.)"),
        ("Next-month expected loss", ntd(k.ecl_next_month), "PD x EAD x LGD x charge-off share"),
        ("Est. risk-adj. contribution", ntd(k.est_monthly_risk_adj_contribution), "per month, after expected loss"),
        ("Default rate (Oct, actual)", f"{k.actual_default_rate_oct:.1%}", f"mean predicted PD {k.avg_predicted_pd:.1%}"),
        ("Loss-making customers", f"{k.share_loss_making:.0%}", "risk-adj. contribution < 0 (est.)"),
        ("Avg dormancy probability", f"{k.avg_predicted_dormancy:.1%}", "active customers, next 2 months"),
    ])
    gs = GridSpec(2, 2, figure=fig, left=0.11, right=0.98, top=0.76, bottom=0.07, hspace=0.45, wspace=0.22)
    months = port.month_label.str[:3]

    ax = fig.add_subplot(gs[0, 0])
    ax.plot(months, port.total_balance / 1e9, marker="o", ms=6, color=SERIES[0])
    for x, v in zip(months, port.total_balance / 1e9):
        ax.text(x, v + 0.012, f"{v:.2f}", ha="center", fontsize=8, color=INK2)
    ax.set_ylim(1.0, port.total_balance.max() / 1e9 * 1.08)
    tidy(ax, "Is the book growing? Total balance outstanding (NT$ billions)")

    ax = fig.add_subplot(gs[0, 1])
    ax.plot(months, port.delinquency_rate_60plus * 100, marker="o", ms=6, color=SERIES[0])
    for x, v in zip(months, port.delinquency_rate_60plus * 100):
        ax.text(x, v + 0.3, f"{v:.1f}%", ha="center", fontsize=8, color=INK2)
    ax.annotate("Sep status codes differ\n(not comparable)", xy=(5, port.delinquency_rate_60plus.iloc[-1] * 100),
                xytext=(3.6, 6.5), fontsize=7.5, color=MUTED, arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.8))
    ax.set_ylim(0, port.delinquency_rate_60plus.max() * 100 * 1.25)
    tidy(ax, "Is credit quality getting worse? Accounts 60+ days past due (%)")

    ax = fig.add_subplot(gs[1, 0])
    hbar(ax, SEGMENT_ORDER, list(seg.total_risk_adj_contribution / 1e6), lambda v: f"NT${v:,.1f}M",
         colors=[SEG_COLOR[s] for s in SEGMENT_ORDER],
         title="Where does value come from? Est. monthly risk-adjusted contribution by segment")

    ax = fig.add_subplot(gs[1, 1])
    c = d["conc"]
    ax.plot(c.top_percent_of_customers, c.cum_share_of_total * 100, color=SERIES[0])
    for p in (10, 20):
        v = c.loc[c.top_percent_of_customers == p, "cum_share_of_total"].iloc[0] * 100
        ax.plot(p, v, "o", ms=7, color=SERIES[0])
        ax.text(p + 2, v - 6, f"top {p}% -> {v:.0f}% of total", fontsize=8, color=INK2)
    ax.axhline(100, color=AXIS, lw=1, ls="--")
    tidy(ax, "How concentrated is value? Cumulative share of est. contribution", "Top % of customers", "% of total")
    return fig


def page_customers(d):
    seg = d["seg"]
    fig = page("2  Customer analytics", "Five behavioural segments from K-means on utilization, payment, activity and "
               "delinquency ratios. Question: who are our customers and how do they use the card?")
    gs = GridSpec(2, 2, figure=fig, left=0.12, right=0.98, top=0.88, bottom=0.12, hspace=0.42, wspace=0.35)

    ax = fig.add_subplot(gs[0, 0])
    hbar(ax, SEGMENT_ORDER, list(seg.customers), lambda v: f"{v:,.0f}", colors=[SEG_COLOR[s] for s in SEGMENT_ORDER],
         title="How many customers are in each segment?")

    ax = fig.add_subplot(gs[0, 1])
    for s in SEGMENT_ORDER:
        r = seg.loc[s]
        ax.scatter(r.avg_utilization, r.avg_payment_ratio, s=r.customers / 25, color=SEG_COLOR[s],
                   edgecolor=SURFACE, linewidth=2, zorder=3)
        ax.annotate(s, (r.avg_utilization, r.avg_payment_ratio), xytext=(8, 6), textcoords="offset points",
                    fontsize=8, color=INK2)
    ax.set_xlim(-0.05, 0.85), ax.set_ylim(0, 1.1)
    tidy(ax, "How do segments use the card? (bubble size = customers)", "Average utilization",
         "Average share of bill paid")

    ax = fig.add_subplot(gs[1, 0])
    hbar(ax, SEGMENT_ORDER, list(seg.avg_monthly_purchases), lambda v: ntd(v, short=False),
         colors=[SEG_COLOR[s] for s in SEGMENT_ORDER], title="How much do they spend? Est. monthly purchases per customer")

    ax = fig.add_subplot(gs[1, 1])
    ct = pd.crosstab(d["c360"].limit_tier, d["c360"].segment, normalize="index").reindex(columns=SEGMENT_ORDER) * 100
    left = np.zeros(len(ct))
    y = np.arange(len(ct))[::-1]
    for s in SEGMENT_ORDER:
        ax.barh(y, ct[s], left=left, color=SEG_COLOR[s], height=0.6, edgecolor=SURFACE, linewidth=2, label=s)
        left += ct[s].values
    ax.set_yticks(y, [t[3:] for t in ct.index])
    ax.set_xlim(0, 100)
    ax.grid(axis="y", visible=False)
    ax.legend(ncol=5, loc="upper center", bbox_to_anchor=(0.5, -0.06), fontsize=7.5)
    tidy(ax, "Which segments hold each credit-limit tier? (% of tier)")
    return fig


def page_risk(d):
    c, risk, lift = d["c360"], d["risk"], d["lift"]
    fig = page("3  Risk analytics", "PD = out-of-fold LightGBM probability of missing the Oct 2005 payment. "
               "Question: where is the credit risk and how much could it cost?")
    gs = GridSpec(2, 3, figure=fig, left=0.07, right=0.98, top=0.88, bottom=0.08, hspace=0.45, wspace=0.42)

    ax = fig.add_subplot(gs[0, 0])
    bands = c.pd_risk_band.value_counts().sort_index()
    cols = [STATUS["good"], STATUS["warning"], STATUS["serious"], STATUS["critical"]]
    ax.bar([b[3:].replace(" (", "\n(") for b in bands.index], bands.values, color=cols, width=0.65)
    for i, v in enumerate(bands.values):
        ax.text(i, v + 150, f"{v:,}\n({v/len(c):.0%})", ha="center", fontsize=8, color=INK2)
    ax.set_ylim(0, bands.max() * 1.25)
    ax.grid(axis="x", visible=False)
    tidy(ax, "How is risk distributed? Customers by PD band")

    ax = fig.add_subplot(gs[0, 1])
    ax.bar(lift.decile, lift.observed_rate * 100, color=SEQ[2], width=0.7, label="Actual default rate")
    ax.plot(lift.decile, lift.mean_predicted * 100, "o", ms=7, color=SERIES[1], label="Mean predicted PD")
    ax.set_xticks(range(1, 11))
    ax.legend(loc="upper right")
    ax.grid(axis="x", visible=False)
    tidy(ax, "Does the model rank risk? Test set by PD decile (%)", "Decile (1 = riskiest)")

    ax = fig.add_subplot(gs[0, 2])
    rr = d["roll"].pivot(index="from_bucket", columns="to_bucket", values="n").fillna(0)
    rr = rr.drop(columns=[c_ for c_ in rr.columns if c_ == "1. 30 DPD"], errors="ignore")
    share = rr.div(rr.sum(axis=1), axis=0) * 100
    ax.imshow(share.values, cmap=matplotlib_cmap(), vmin=0, vmax=100, aspect="auto")
    for i in range(share.shape[0]):
        for j in range(share.shape[1]):
            v = share.values[i, j]
            ax.text(j, i, f"{v:.0f}%", ha="center", va="center", fontsize=9, color="white" if v > 55 else INK)
    ax.set_xticks(range(share.shape[1]), [s[3:] for s in share.columns])
    ax.set_yticks(range(share.shape[0]), [s[3:] for s in share.index])
    ax.grid(False)
    tidy(ax, "Roll rates: next-month status (Apr-Aug avg.)", "To", "From")

    ax = fig.add_subplot(gs[1, 0])
    lt = risk[risk.dimension == "Credit limit tier"].sort_values("grp")
    ax.bar([g[3:] for g in lt.grp], lt.actual_default_rate * 100, color=SERIES[0], width=0.6)
    for i, v in enumerate(lt.actual_default_rate * 100):
        ax.text(i, v + 0.5, f"{v:.1f}%", ha="center", fontsize=8, color=INK2)
    ax.grid(axis="x", visible=False)
    tidy(ax, "Risk by product tier: default rate by credit limit")

    ax = fig.add_subplot(gs[1, 1])
    ed = risk[risk.dimension == "Education"].sort_values("actual_default_rate", ascending=False)
    hbar(ax, list(ed.grp), list(ed.actual_default_rate * 100), lambda v: f"{v:.1f}%",
         title="Risk by customer profile: default rate by education")

    ax = fig.add_subplot(gs[1, 2])
    sg = risk[risk.dimension == "Segment"].set_index("grp").reindex(SEGMENT_ORDER)
    hbar(ax, SEGMENT_ORDER, list(sg.total_ecl / 1e6), lambda v: f"NT${v:,.1f}M",
         colors=[SEG_COLOR[s] for s in SEGMENT_ORDER], title="Where is the expected loss? Next-month ECL by segment")
    return fig


def matplotlib_cmap():
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list("seq", ["#f0efec"] + SEQ)


def page_retention(d):
    ret, budget = d["ret"], d["budget"]
    fig = page("4  Retention decision support", "Active customers in Sep 2005 ranked by expected net benefit of a "
               "retention contact = P(dormant) x save rate x 12-month risk-adjusted value - contact cost. "
               "Question: who should we contact first, and why?")
    gs = GridSpec(2, 2, figure=fig, left=0.10, right=0.98, top=0.88, bottom=0.05, hspace=0.42, wspace=0.22,
                  height_ratios=[1, 1.1])

    ax = fig.add_subplot(gs[0, 0])
    tiers = ret.priority_tier.value_counts().reindex(list(TIER_COLOR)).fillna(0)
    labels = ["Contact now", "Next wave", "Monitor (offer > gain)", "Exclude: not profitable", "Exclude: credit risk"]
    hbar(ax, labels, list(tiers.values), lambda v: f"{v:,.0f}", colors=list(TIER_COLOR.values()),
         title="What happens to each active customer? Priority tier")

    ax = fig.add_subplot(gs[0, 1])
    for i, (cost, b) in enumerate(budget.groupby("contact_cost")):
        b = b[b.share_of_active_contacted <= 0.10]
        pk = b.loc[b.cum_benefit_model.idxmax()]
        ax.plot(b.share_of_active_contacted * 100, b.cum_benefit_model / 1e3, color=SERIES[i],
                label=f"NT${cost:.0f}: best = top {int(pk.customers_contacted):,} customers ({ntd(pk.cum_benefit_model)})")
        ax.plot(pk.share_of_active_contacted * 100, pk.cum_benefit_model / 1e3, "o", ms=7, color=SERIES[i])
    ax.axhline(0, color=AXIS, lw=1)
    ax.axvline(10, color=MUTED, lw=1, ls=":")
    ax.text(9.9, ax.get_ylim()[0] * 0.9, "10% budget", ha="right", fontsize=7.5, color=MUTED)
    ax.legend(loc="lower left", title="Contact cost", title_fontsize=8)
    tidy(ax, "What if only 10% can be contacted? Cumulative expected net benefit (NT$ thousands)",
         "% of active customers contacted (in model-priority order)")

    ax = fig.add_subplot(gs[1, 0])
    s = ret.sample(min(6000, len(ret)), random_state=0)
    for t, col in TIER_COLOR.items():
        m = s.priority_tier == t
        ax.scatter(s.loc[m, "churn_score"] * 100, s.loc[m, "est_risk_adjusted_contribution"], s=9, color=col,
                   alpha=0.7, label=t[3:], linewidths=0)
    ax.set_xscale("log")
    ax.set_ylim(np.percentile(ret.est_risk_adjusted_contribution, 0.5), np.percentile(ret.est_risk_adjusted_contribution, 99.7))
    ax.axhline(0, color=AXIS, lw=1)
    ax.legend(fontsize=7, loc="upper left", markerscale=2)
    tidy(ax, "Value vs dormancy risk (sample of 6,000)", "P(dormant in next 2 months), % (log scale)",
         "Est. risk-adj. contribution, NT$/month")

    ax = fig.add_subplot(gs[1, 1])
    ax.axis("off")
    top = ret[ret.priority_tier == "1. Contact now"].sort_values("priority_rank").head(12)
    cols = ["Customer", "Segment", "P(dormant)", "PD", "Value/mo", "Net benefit"]
    cell = [[f"{r.customer_id}", r.segment, f"{r.churn_score:.0%}", f"{r.pd_score:.0%}",
             ntd(r.est_risk_adjusted_contribution, short=False), ntd(r.expected_net_benefit, short=False)]
            for r in top.itertuples()]
    if cell:
        tbl = ax.table(cellText=cell, colLabels=cols, loc="upper center", cellLoc="left", colLoc="left",
                       colWidths=[0.11, 0.27, 0.13, 0.08, 0.16, 0.17])
        tbl.auto_set_font_size(False)
        tbl.set_fontsize(8)
        tbl.scale(1, 1.35)
        for (i, j), c_ in tbl.get_celld().items():
            c_.set_edgecolor(GRID)
            c_.set_facecolor("#f3f2ee" if i == 0 else SURFACE)
            if i == 0:
                c_.set_text_props(fontweight="bold", color=INK)
    ax.set_title("Who should we contact first? Top 'Contact now' customers", fontsize=10.5, fontweight="bold",
                 color=INK, loc="left")
    return fig


def render_all() -> None:
    out = path("dashboard/powerbi/preview")
    out.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        d = load(conn)
    for name, fn in [("page1_executive", page_executive), ("page2_customers", page_customers),
                     ("page3_risk", page_risk), ("page4_retention", page_retention)]:
        fig = fn(d)
        fig.savefig(out / f"{name}.png", dpi=110)
        plt.close(fig)
