"""Generates the Power BI project (PBIP) in dashboard/powerbi/ from the database.

    python scripts/build_powerbi.py

What it writes:
  CreditCardIntelligence.SemanticModel/definition/   TMDL: tables (columns typed from Postgres
                                                     information_schema), relationships, parameters,
                                                     and a _Measures table with all DAX measures
  CreditCardIntelligence.Report/definition/pages/    PBIR: four report pages and their visuals
  CreditCardIntelligence.Report/StaticResources/     custom theme

The empty project was created once in Power BI Desktop (File > Save as > .pbip) so the files
use the exact format versions of the installed Desktop; this script fills it in. After opening
it in Power BI Desktop, edits made there are saved back into the same text files.
"""
from __future__ import annotations

import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.db import read_sql  # noqa: E402

PBI = ROOT / "dashboard" / "powerbi"
NAME = "CreditCardIntelligence"
SM = PBI / f"{NAME}.SemanticModel"
RPT = PBI / f"{NAME}.Report"
NS = uuid.UUID("6f1d8a52-2c1e-4c47-9d2b-6a0e3c1b9f10")  # stable lineage tags -> clean git diffs

# ----------------------------------------------------------------------------- semantic model
# model table -> (schema, source table, columns to keep or None for all)
TABLES = {
    "customer_360": ("mart", "customer_360", None),
    "customer_monthly": ("mart", "customer_monthly_metrics",
                         ["customer_id", "month_index", "bill_amount", "payment_amount", "est_new_charges",
                          "utilization", "payment_ratio", "delinquency_bucket", "is_delinquent", "is_inactive"]),
    "dim_month": ("core", "dim_month", None),
    "retention_priority": ("mart", "retention_priority", None),
    "segment_profile": ("ml", "segment_profile", None),
    "roll_rates": ("mart", "roll_rates", None),
    "model_lift": ("ml", "model_lift", None),
    "model_comparison": ("ml", "model_comparison", None),
    "assumptions": ("mart", "assumptions", None),
    "retention_budget_curve": ("mart", "retention_budget_curve", None),
    "value_concentration": ("mart", "value_concentration", None),
}

RELATIONSHIPS = [  # (from many-side, to one-side)
    ("customer_monthly.customer_id", "customer_360.customer_id"),
    ("customer_monthly.month_index", "dim_month.month_index"),
    ("retention_priority.customer_id", "customer_360.customer_id"),
    ("customer_360.segment", "segment_profile.segment"),
]

MONEY = r"\N\T\$#,0"   # letters escaped: unescaped "NT$#,0" rendered as "%mt$#,0" in Power BI
PCT1 = "0.0%"
COUNT = "#,0"

# (folder, name, DAX, format)
MEASURES = [
    ("Portfolio", "Customers", "DISTINCTCOUNT(customer_360[customer_id])", COUNT),
    ("Portfolio", "Active Customers (Sep)", "CALCULATE([Customers], NOT ISBLANK(customer_360[churn_score]))", COUNT),
    ("Portfolio", "Balance Outstanding (Sep)", "SUMX(customer_360, MAX(customer_360[balance_sep], 0))", MONEY),
    ("Portfolio", "Total Credit Limit", "SUM(customer_360[credit_limit])", MONEY),
    ("Portfolio", "Portfolio Utilization", "DIVIDE([Balance Outstanding (Sep)], [Total Credit Limit])", PCT1),
    ("Portfolio", "Balance by Month", "SUMX(customer_monthly, MAX(customer_monthly[bill_amount], 0))", MONEY),
    ("Portfolio", "60+ DPD Rate",
     'DIVIDE(CALCULATE(COUNTROWS(customer_monthly), customer_monthly[delinquency_bucket] IN {"2. 60 DPD", "3. 90+ DPD"}), '
     "COUNTROWS(customer_monthly))", PCT1),
    ("Economics (est.)", "Est Monthly Revenue", "SUM(customer_360[est_monthly_revenue])", MONEY),
    ("Economics (est.)", "Est Monthly Operating Cost", "SUM(customer_360[est_monthly_operating_cost])", MONEY),
    ("Economics (est.)", "Est Contribution Before Losses", "SUM(customer_360[est_contribution_before_losses])", MONEY),
    ("Economics (est.)", "Expected Credit Loss (Next Month)", "SUM(customer_360[ecl_next_month])", MONEY),
    ("Economics (est.)", "Est Risk-Adj Contribution", "SUM(customer_360[est_risk_adjusted_contribution])", MONEY),
    ("Economics (est.)", "Est Risk-Adj Contribution (Annualized)", "[Est Risk-Adj Contribution] * 12", MONEY),
    ("Economics (est.)", "Avg Risk-Adj Contribution per Customer", "DIVIDE([Est Risk-Adj Contribution], [Customers])", MONEY),
    ("Economics (est.)", "Loss-Making Share",
     "DIVIDE(CALCULATE([Customers], customer_360[est_risk_adjusted_contribution] < 0), [Customers])", PCT1),
    ("Economics (est.)", "ECL as % of Revenue", "DIVIDE([Expected Credit Loss (Next Month)], [Est Monthly Revenue])", PCT1),
    ("Economics (est.)", "Avg Est Monthly Purchases", "AVERAGE(customer_360[est_monthly_purchases])", MONEY),
    ("Customers", "Avg Utilization (6m)", "AVERAGE(customer_360[utilization_avg_6m])", PCT1),
    ("Customers", "Avg Share of Bill Paid", "AVERAGE(customer_360[payment_ratio_avg])", PCT1),
    ("Customers", "Segment Avg Utilization", "AVERAGE(segment_profile[avg_utilization])", PCT1),
    ("Customers", "Segment Avg Share of Bill Paid", "AVERAGE(segment_profile[avg_payment_ratio])", PCT1),
    ("Customers", "Segment Customers", "SUM(segment_profile[customers])", COUNT),
    ("Risk", "Actual Default Rate (Oct)",
     "DIVIDE(CALCULATE([Customers], customer_360[actual_default_oct] = TRUE()), [Customers])", PCT1),
    ("Risk", "Avg Predicted PD", "AVERAGE(customer_360[pd_score])", PCT1),
    ("Risk", "PD Calibration Gap", "[Avg Predicted PD] - [Actual Default Rate (Oct)]", "0.0%;-0.0%"),
    ("Risk", "EAD", "SUM(customer_360[ead_estimate])", MONEY),
    ("Risk", "ECL Rate on EAD", "DIVIDE([Expected Credit Loss (Next Month)], [EAD])", "0.00%"),
    ("Risk", "Very High Risk Customers", 'CALCULATE([Customers], customer_360[pd_risk_band] = "4. Very high (50%+)")', COUNT),
    ("Risk", "PD Test ROC-AUC",
     'CALCULATE(MAX(model_comparison[value]), model_comparison[task] = "pd", model_comparison[model] = "lightgbm", '
     'model_comparison[metric] = "test_roc_auc")', "0.000"),
    ("Risk", "PD Decile Actual Default Rate", 'CALCULATE(AVERAGE(model_lift[observed_rate]), model_lift[task] = "pd")', PCT1),
    ("Risk", "PD Decile Mean Predicted PD", 'CALCULATE(AVERAGE(model_lift[mean_predicted]), model_lift[task] = "pd")', PCT1),
    # KEEPFILTERS so the matrix's own row/column filters are intersected, not replaced
    # (without it every cell showed 100%). The tiny "30 DPD" bucket is a coding artefact and is left out.
    ("Risk", "Roll Rate Share",
     'VAR n = CALCULATE(SUM(roll_rates[accounts]), roll_rates[from_month_index] <= 4, '
     'KEEPFILTERS(roll_rates[from_bucket] <> "1. 30 DPD"), KEEPFILTERS(roll_rates[to_bucket] <> "1. 30 DPD")) '
     'VAR tot = CALCULATE(SUM(roll_rates[accounts]), roll_rates[from_month_index] <= 4, '
     'KEEPFILTERS(roll_rates[from_bucket] <> "1. 30 DPD"), REMOVEFILTERS(roll_rates[to_bucket])) '
     "RETURN DIVIDE(n, tot)", "0%"),
    ("Retention", "Avg Dormancy Probability", "AVERAGE(customer_360[churn_score])", "0.0%"),
    ("Retention", "Active Customers Scored", "COUNTROWS(retention_priority)", COUNT),
    ("Retention", "Expected Value at Risk (12m)", "SUM(retention_priority[expected_value_at_risk])", MONEY),
    ("Retention", "Contact Now Customers",
     'CALCULATE(COUNTROWS(retention_priority), retention_priority[priority_tier] = "1. Contact now")', COUNT),
    ("Retention", "Contact Now Expected Net Benefit",
     'CALCULATE(SUM(retention_priority[expected_net_benefit]), retention_priority[priority_tier] = "1. Contact now")', MONEY),
    ("Retention", "Excluded for Credit Risk",
     'CALCULATE(COUNTROWS(retention_priority), retention_priority[priority_tier] = "5. Exclude - high credit risk")', COUNT),
    ("Retention", "Avg P(Dormant)", "AVERAGE(retention_priority[churn_score])", "0.00%"),
    ("Retention", "Avg Risk-Adj Value per Month", "AVERAGE(retention_priority[est_risk_adjusted_contribution])", MONEY),
    ("Retention", "Cumulative Net Benefit", "SUM(retention_budget_curve[cum_benefit_model])", MONEY),
    ("Customers", "Cumulative Share of Contribution", "MAX(value_concentration[cum_share_of_total])", "0%"),
]

PG_TYPES = {"bigint": "int64", "integer": "int64", "smallint": "int64", "numeric": "double",
            "double precision": "double", "real": "double", "boolean": "boolean", "text": "string",
            "character varying": "string", "date": "dateTime"}
PCT_HINTS = ("score", "rate", "utilization", "payment_ratio", "share", "observed", "mean_predicted", "lift")
MONEY_HINTS = ("balance", "credit_limit", "est_", "ecl", "ead", "value_if", "value_at_risk", "net_benefit",
               "payment_total", "bill_amount", "payment_amount", "cum_benefit", "contribution", "avg_credit_limit")


def tag(*parts) -> str:
    return str(uuid.uuid5(NS, "/".join(parts)))


def q(name: str) -> str:
    """Quote a TMDL object name if it contains anything besides letters, digits and underscores."""
    return name if name.replace("_", "").isalnum() else "'" + name.replace("'", "''") + "'"


def column_format(col: str, dtype: str) -> str | None:
    if dtype == "dateTime":
        return "yyyy-mm-dd"
    if dtype not in ("int64", "double"):
        return None
    if col.endswith("_id") or col.endswith("_index") or col in ("decile", "rank", "cluster_id", "month_key",
                                                                  "calendar_quarter", "top_percent_of_customers",
                                                                  "contact_cost", "customers_contacted"):
        return "0"
    if col == "value" or col == "chi2":
        return "0.####"
    if any(h in col for h in PCT_HINTS):
        return PCT1
    if any(h in col for h in MONEY_HINTS):
        return MONEY
    return "#,0.##" if dtype == "double" else COUNT


def table_tmdl(table: str, schema: str, source: str, keep: list[str] | None) -> str:
    cols = read_sql("""SELECT column_name, data_type FROM information_schema.columns
                       WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position""", params=(schema, source))
    if cols.empty:
        raise SystemExit(f"{schema}.{source} not found - run the pipeline first")
    if keep:
        cols = cols.set_index("column_name").loc[keep].reset_index()
    out = [f"table {table}", f"\tlineageTag: {tag(table)}", ""]
    for c, pgt in zip(cols.column_name, cols.data_type):
        dtype = PG_TYPES[pgt]
        out.append(f"\tcolumn {q(c)}")
        out.append(f"\t\tdataType: {dtype}")
        fmt = column_format(c, dtype)
        if fmt:
            out.append(f"\t\tformatString: {fmt}")
        out.append(f"\t\tlineageTag: {tag(table, c)}")
        is_key = c.endswith("_id") or c.endswith("_index") or c in ("decile", "rank", "month_key", "contact_cost",
                                                                    "customers_contacted", "top_percent_of_customers")
        summarize = "sum" if dtype in ("int64", "double") and not is_key and fmt == MONEY else "none"
        out.append(f"\t\tsummarizeBy: {summarize}")
        out.append(f"\t\tsourceColumn: {c}")
        if table == "dim_month" and c == "month_label":
            out.append("\t\tsortByColumn: month_index")
        out.append("")
        if dtype == "dateTime":
            out.append("\t\tannotation UnderlyingDateTimeDataType = Date")
            out.append("")
        out.append("\t\tannotation SummarizationSetBy = Automatic")
        out.append("")
    select = ""
    if keep:
        select = (",\n\t\t\t\t    Kept = Table.SelectColumns(Data, {" + ", ".join(f'"{k}"' for k in keep) + "})")
    result = "Kept" if keep else "Data"
    out += [
        f"\tpartition {table} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        "\t\t\t\t    Source = PostgreSQL.Database(PgServer, PgDatabase),",
        f'\t\t\t\t    Data = Source{{[Schema = "{schema}", Item = "{source}"]}}[Data]{select}',
        "\t\t\t\tin",
        f"\t\t\t\t    {result}",
        "",
        "\tannotation PBI_ResultType = Table",
        "",
    ]
    return "\n".join(out)


def measures_tmdl() -> str:
    out = ["table _Measures", f"\tlineageTag: {tag('_Measures')}", ""]
    for folder, name, dax, fmt in MEASURES:
        out += [f"\tmeasure {q(name)} = {dax}", f"\t\tformatString: {fmt}", f"\t\tdisplayFolder: {folder}",
                f"\t\tlineageTag: {tag('_Measures', name)}", ""]
    out += [
        "\tcolumn Value",
        "\t\tdataType: int64",
        "\t\tisHidden",
        "\t\tformatString: 0",
        f"\t\tlineageTag: {tag('_Measures', 'Value')}",
        "\t\tsummarizeBy: none",
        "\t\tisNameInferred",
        "\t\tsourceColumn: [Value]",
        "",
        "\t\tannotation SummarizationSetBy = Automatic",
        "",
        "\tpartition _Measures = calculated",
        "\t\tmode: import",
        '\t\tsource = ROW("Value", 0)',
        "",
    ]
    return "\n".join(out)


def write_semantic_model() -> None:
    d = SM / "definition"
    tables_dir = d / "tables"
    if tables_dir.exists():
        shutil.rmtree(tables_dir)
    tables_dir.mkdir(parents=True)
    for t, (schema, src, keep) in TABLES.items():
        (tables_dir / f"{t}.tmdl").write_text(table_tmdl(t, schema, src, keep), encoding="utf-8")
    (tables_dir / "_Measures.tmdl").write_text(measures_tmdl(), encoding="utf-8")

    rel = []
    for f, t in RELATIONSHIPS:
        rel += [f"relationship {tag('rel', f, t)}", f"\tfromColumn: {f}", f"\ttoColumn: {t}", ""]
    (d / "relationships.tmdl").write_text("\n".join(rel), encoding="utf-8")

    (d / "expressions.tmdl").write_text("\n".join([
        'expression PgServer = "localhost:5432" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        f"\tlineageTag: {tag('param', 'PgServer')}",
        "",
        "\tannotation PBI_ResultType = Text",
        "",
        'expression PgDatabase = "cc_intel" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]',
        f"\tlineageTag: {tag('param', 'PgDatabase')}",
        "",
        "\tannotation PBI_ResultType = Text",
        "",
    ]), encoding="utf-8")

    order = ["PgServer", "PgDatabase", *TABLES]
    (d / "model.tmdl").write_text("\n".join([
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tsourceQueryCulture: en-US",
        "\tvalueFilterBehavior: independent",
        "\tdataAccessOptions",
        "\t\tlegacyRedirects",
        "\t\treturnErrorValuesAsNull",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        "annotation PBI_QueryOrder = " + json.dumps(order),
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
        *[f"ref table {t}" for t in [*TABLES, "_Measures"]],
        "",
        "ref cultureInfo en-US",
        "",
    ]), encoding="utf-8")

    # start from a clean data cache and don't let Desktop invent extra relationships
    (SM / ".pbi" / "cache.abf").unlink(missing_ok=True)
    es = SM / ".pbi" / "editorSettings.json"
    if es.exists():
        s = json.loads(es.read_text(encoding="utf-8"))
        s["autodetectRelationships"] = False
        es.write_text(json.dumps(s, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------------- report helpers
VC_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/visualContainer/2.12.0/schema.json"
PAGE_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/2.1.0/schema.json"
PAGES_SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/pagesMetadata/1.1.0/schema.json"
W, H = 1280, 720


def lit(v):
    if isinstance(v, bool):
        return {"expr": {"Literal": {"Value": "true" if v else "false"}}}
    if isinstance(v, (int, float)):
        return {"expr": {"Literal": {"Value": f"{v}D"}}}
    return {"expr": {"Literal": {"Value": "'" + str(v).replace("'", "''") + "'"}}}


def col(table, name):
    return ("col", table, name)


def m(name):
    return ("m", "_Measures", name)


def field(f):
    kind, table, name = f
    key = "Column" if kind == "col" else "Measure"
    return {key: {"Expression": {"SourceRef": {"Entity": table}}, "Property": name}}


def projection(f, display=None):
    p = {"field": field(f), "queryRef": f"{f[1]}.{f[2]}", "nativeQueryRef": f[2]}
    if display:
        p["displayName"] = display
    return p


class Page:
    def __init__(self, key: str, display: str):
        self.key, self.display, self.visuals = key, display, []

    def add(self, vtype, x, y, w, h, roles=None, title=None, objects=None, sort=None, displays=None):
        displays = displays or {}
        visual = {"visualType": vtype}
        if roles:
            visual["query"] = {"queryState": {role: {"projections": [projection(f, displays.get(f[2])) for f in fs]}
                                              for role, fs in roles.items()}}
            if sort:
                f, direction = sort
                visual["query"]["sortDefinition"] = {"sort": [{"field": field(f), "direction": direction}],
                                                     "isDefaultSort": False}
        if objects:
            visual["objects"] = objects
        if title:
            # explicit title; switch off the auto-generated "Measure by Column" subtitle line
            visual["visualContainerObjects"] = {
                "title": [{"properties": {"show": lit(True), "text": lit(title), "fontSize": lit(11)}}],
                "subTitle": [{"properties": {"show": lit(False)}}]}
        n = len(self.visuals)
        self.visuals.append({
            "$schema": VC_SCHEMA,
            "name": f"{self.key}v{n:02d}",
            "position": {"x": x, "y": y, "z": n, "height": h, "width": w, "tabOrder": n},
            "visual": visual,
        })

    # convenience builders -------------------------------------------------
    def text(self, x, y, w, h, paragraphs):
        """paragraphs: list of (text, size_pt, bold)."""
        paras = [{"textRuns": [{"value": t, "textStyle": {"fontSize": f"{s}pt", **({"fontWeight": "bold"} if b else {}),
                                                            "color": "#0b0b0b" if b else "#52514e"}}]}
                 for t, s, b in paragraphs]
        self.add("textbox", x, y, w, h, objects={"general": [{"properties": {"paragraphs": paras}}]})

    def header(self, title, subtitle, slicers=()):
        self.text(16, 4, 860, 66, [(title, 16, True), (subtitle, 9, False)])
        for i, (f, label) in enumerate(slicers):
            # the slicer's own header shows the label (projection displayName); a container title on
            # top of it pushed the dropdown out of the 56px box
            self.add("slicer", W - 16 - (len(slicers) - i) * 196 + 8, 8, 188, 56, roles={"Values": [f]},
                     displays={f[2]: label}, objects={"data": [{"properties": {"mode": lit("Dropdown")}}]})

    def cards(self, y, h, measures):
        """KPI cards: value on top, label underneath (no container title, which would crowd out the value).
        Money values get one decimal so NT$1.54bn doesn't display as NT$2bn."""
        gap = 8
        w = (W - 32 - gap * (len(measures) - 1)) / len(measures)
        money = {name for _, name, _, fmt in MEASURES if fmt == MONEY}
        for i, (name, label) in enumerate(measures):
            labels = {"fontSize": lit(17)}
            if name in money:
                labels["labelPrecision"] = lit(1)
            self.add("card", round(16 + i * (w + gap)), y, round(w), h, roles={"Values": [m(name)]},
                     displays={name: label},
                     objects={"categoryLabels": [{"properties": {"show": lit(True), "fontSize": lit(9)}}],
                              "labels": [{"properties": labels}]})


LABELS_ON = {"labels": [{"properties": {"show": lit(True)}}]}


def build_pages(cfg) -> list[Page]:
    seg, tier = col("customer_360", "segment"), col("customer_360", "limit_tier")
    month = col("dim_month", "month_label")

    # ---- Page 1
    p1 = Page("exec", "1 Executive overview")
    p1.header("Executive overview",
              "30,000 Taiwanese credit card customers, Apr-Sep 2005. How big is the book, is it getting riskier, "
              "and where does the value come from? NT$ figures marked est. are estimates.",
              [(seg, "Segment"), (tier, "Credit limit tier")])
    p1.cards(72, 90, [("Customers", "Customers"), ("Active Customers (Sep)", "Active in Sep"),
                      ("Balance Outstanding (Sep)", "Balance (Sep)"),
                      ("Est Monthly Revenue", "Revenue / mo (est.)"),
                      ("Expected Credit Loss (Next Month)", "Exp. loss / mo (est.)"),
                      ("Est Risk-Adj Contribution", "Risk-adj. profit / mo (est.)"),
                      ("Actual Default Rate (Oct)", "Default rate (Oct)"),
                      ("Loss-Making Share", "Loss-making (est.)")])
    p1.add("lineChart", 16, 172, 620, 266, roles={"Category": [month], "Y": [m("Balance by Month")]},
           title="Is the book growing? Balance outstanding by month", sort=(month, "Ascending"))
    p1.add("lineChart", 644, 172, 620, 266, roles={"Category": [month], "Y": [m("60+ DPD Rate")]},
           title="Is credit quality getting worse? Accounts 60+ days past due (Sep status codes not comparable)",
           sort=(month, "Ascending"), objects=LABELS_ON)
    p1.add("clusteredBarChart", 16, 446, 620, 266, roles={"Category": [seg], "Y": [m("Est Risk-Adj Contribution")]},
           title="Where does the value come from? Est. monthly risk-adjusted contribution by segment",
           sort=(m("Est Risk-Adj Contribution"), "Descending"), objects=LABELS_ON)
    p1.add("lineChart", 644, 446, 620, 266,
           roles={"Category": [col("value_concentration", "top_percent_of_customers")],
                  "Y": [m("Cumulative Share of Contribution")]},
           title="How concentrated is value? Cumulative share of est. contribution from the top X% of customers "
                 "(whole portfolio)", sort=(col("value_concentration", "top_percent_of_customers"), "Ascending"))

    # ---- Page 2
    p2 = Page("cust", "2 Customer analytics")
    p2.header("Customer analytics",
              "Five behavioral segments from K-means on utilization, payment, activity and delinquency ratios. "
              "Who are our customers and how do they use the card?",
              [(tier, "Credit limit tier"), (col("customer_360", "age_band"), "Age band")])
    p2.add("clusteredBarChart", 16, 72, 306, 310, roles={"Category": [seg], "Y": [m("Customers")]},
           title="How many customers are in each segment?", sort=(m("Customers"), "Descending"), objects=LABELS_ON)
    p2.add("scatterChart", 330, 72, 306, 310,
           roles={"Category": [col("segment_profile", "segment")],
                  "X": [m("Segment Avg Utilization")], "Y": [m("Segment Avg Share of Bill Paid")],
                  "Size": [m("Segment Customers")]},
           title="How do segments use the card? Utilization vs share of bill paid (size = customers)",
           objects={"categoryLabels": [{"properties": {"show": lit(True)}}]})
    p2.add("clusteredBarChart", 644, 72, 306, 310, roles={"Category": [seg], "Y": [m("Avg Est Monthly Purchases")]},
           title="How much do they spend? Est. monthly purchases per customer",
           sort=(m("Avg Est Monthly Purchases"), "Descending"),
           objects={**LABELS_ON, "valueAxis": [{"properties": {"show": lit(False)}}]})
    p2.add("clusteredColumnChart", 958, 72, 306, 310,
           roles={"Category": [col("customer_360", "age_band")], "Y": [m("Customers")]},
           title="Demographics: customers by age band", sort=(col("customer_360", "age_band"), "Ascending"),
           objects=LABELS_ON)
    p2.add("hundredPercentStackedBarChart", 16, 390, 500, 322,
           roles={"Category": [tier], "Series": [seg], "Y": [m("Customers")]},
           title="Product usage: segment mix within each credit-limit tier", sort=(tier, "Ascending"))
    p2.add("tableEx", 524, 390, 740, 322,
           roles={"Values": [seg, m("Customers"), m("Avg Utilization (6m)"),
                             m("Avg Est Monthly Purchases"), m("Actual Default Rate (Oct)"),
                             m("Avg Dormancy Probability"), m("Avg Risk-Adj Contribution per Customer")]},
           title="Segment summary", sort=(m("Avg Risk-Adj Contribution per Customer"), "Descending"),
           displays={"segment": "Segment", "Avg Utilization (6m)": "Utilization",
                     "Avg Est Monthly Purchases": "Purchases / mo", "Actual Default Rate (Oct)": "Default rate",
                     "Avg Dormancy Probability": "P(dormant)",
                     "Avg Risk-Adj Contribution per Customer": "Risk-adj. value / mo"})

    # ---- Page 3
    p3 = Page("risk", "3 Risk analytics")
    p3.header("Risk analytics",
              "PD = out-of-fold LightGBM probability of missing the Oct 2005 payment. Where is the credit risk and "
              "how much could it cost? (The data has no geography or card-product field.)",
              [(seg, "Segment"), (tier, "Credit limit tier")])
    p3.cards(72, 84, [("Avg Predicted PD", "Avg predicted PD"), ("Actual Default Rate (Oct)", "Default rate (Oct, actual)"),
                      ("Expected Credit Loss (Next Month)", "Expected loss / mo (est.)"),
                      ("Very High Risk Customers", "Customers with PD 50%+"), ("ECL Rate on EAD", "Expected loss / EAD"),
                      ("PD Test ROC-AUC", "PD model test ROC-AUC")])
    band = col("customer_360", "pd_risk_band")
    p3.add("clusteredColumnChart", 16, 164, 400, 268, roles={"Category": [band], "Y": [m("Customers")]},
           title="How is risk distributed? Customers by PD band", sort=(band, "Ascending"), objects=LABELS_ON)
    dec = col("model_lift", "decile")
    p3.add("clusteredColumnChart", 424, 164, 420, 268,
           roles={"Category": [dec], "Y": [m("PD Decile Actual Default Rate"), m("PD Decile Mean Predicted PD")]},
           title="Does the model rank and calibrate risk? Test set by PD decile (1 = riskiest)",
           sort=(dec, "Ascending"),
           objects={"categoryAxis": [{"properties": {"axisType": lit("Categorical")}}]},
           displays={"PD Decile Actual Default Rate": "Actual default rate",
                     "PD Decile Mean Predicted PD": "Mean predicted PD"})
    p3.add("pivotTable", 852, 164, 412, 268,
           roles={"Rows": [col("roll_rates", "from_bucket")], "Columns": [col("roll_rates", "to_bucket")],
                  "Values": [m("Roll Rate Share")]},
           title="Roll rates: share moving to each status next month (Apr-Aug average)",
           objects={
               "subTotals": [{"properties": {"rowSubtotals": lit(False), "columnSubtotals": lit(False)}}]})
    p3.add("clusteredColumnChart", 16, 440, 400, 272,
           roles={"Category": [tier], "Y": [m("Actual Default Rate (Oct)"), m("Avg Predicted PD")]},
           title="Risk by product tier: default rate by credit limit", sort=(tier, "Ascending"),
           displays={"Actual Default Rate (Oct)": "Actual default rate", "Avg Predicted PD": "Avg predicted PD"})
    edu = col("customer_360", "education")
    p3.add("clusteredBarChart", 424, 440, 420, 272, roles={"Category": [edu], "Y": [m("Actual Default Rate (Oct)")]},
           title="Risk by customer profile: default rate by education",
           sort=(m("Actual Default Rate (Oct)"), "Descending"), objects=LABELS_ON)
    p3.add("clusteredBarChart", 852, 440, 412, 272, roles={"Category": [seg], "Y": [m("Expected Credit Loss (Next Month)")]},
           title="Where is the expected loss? Next-month ECL by segment (est.)",
           sort=(m("Expected Credit Loss (Next Month)"), "Descending"), objects=LABELS_ON)

    # ---- Page 4
    r = cfg["retention"]
    p4 = Page("ret", "4 Retention decision support")
    p4.header("Retention decision support",
              "Which customers should we prioritize, and why? Expected net benefit = P(dormant) x save rate x "
              "12-month risk-adjusted value - contact cost. High-PD customers go to risk management instead.",
              [(col("retention_priority", "priority_tier"), "Priority tier"), (col("retention_priority", "segment"), "Segment")])
    p4.cards(72, 84, [("Active Customers Scored", "Active customers scored"), ("Contact Now Customers", "Contact now"),
                      ("Contact Now Expected Net Benefit", "Net benefit of 'Contact now' (est.)"),
                      ("Expected Value at Risk (12m)", "Dormancy value at risk, 12m (est.)"),
                      ("Excluded for Credit Risk", "Excluded: PD 50%+")])
    pt = col("retention_priority", "priority_tier")
    p4.add("clusteredBarChart", 16, 164, 400, 246, roles={"Category": [pt], "Y": [m("Active Customers Scored")]},
           title="What happens to each active customer? Customers by priority tier", sort=(pt, "Ascending"),
           objects={"labels": [{"properties": {"show": lit(True), "labelDisplayUnits": lit(1)}}]})
    share = col("retention_budget_curve", "share_of_active_contacted")
    p4.add("lineChart", 424, 164, 420, 246,
           roles={"Category": [share], "Series": [col("retention_budget_curve", "contact_cost")],
                  "Y": [m("Cumulative Net Benefit")]},
           title="What if only 10% can be contacted? Cumulative expected net benefit by contact cost (NT$)",
           sort=(share, "Ascending"), displays={"contact_cost": "Contact cost (NT$)"},
           objects={"legend": [{"properties": {"showTitle": lit(True), "titleText": lit("Contact cost (NT$)")}}]})
    rseg = col("retention_priority", "segment")
    p4.add("clusteredBarChart", 852, 164, 412, 246, roles={"Category": [rseg], "Y": [m("Expected Value at Risk (12m)")]},
           title="Where is the dormancy value at risk? 12-month value at risk by segment (est.)",
           sort=(m("Expected Value at Risk (12m)"), "Descending"), objects=LABELS_ON)
    p4.add("tableEx", 16, 418, 900, 294,
           roles={"Values": [col("retention_priority", "customer_id"), col("retention_priority", "segment"),
                             col("retention_priority", "est_risk_adjusted_contribution"),
                             col("retention_priority", "churn_score"), col("retention_priority", "pd_score"),
                             col("retention_priority", "expected_net_benefit"), pt,
                             col("retention_priority", "priority_reason")]},
           title="Decision list: who to contact first, and why (sorted by expected net benefit)",
           sort=(col("retention_priority", "expected_net_benefit"), "Descending"),
           displays={"customer_id": "Customer", "segment": "Segment", "est_risk_adjusted_contribution": "Value / month (est.)",
                     "churn_score": "P(dormant)", "pd_score": "PD", "expected_net_benefit": "Expected net benefit",
                     "priority_tier": "Priority", "priority_reason": "Why"})
    p4.text(924, 418, 340, 294, [
        ("Assumptions", 11, True),
        (f"Save rate {r['save_rate']:.0%} (unknown - must be measured with a random holdout group).", 9, False),
        (f"Contact cost NT${r['contact_cost']:.0f}; value horizon {r['horizon_months']} months; "
         f"budget scenario {r['budget_share']:.0%} of active customers.", 9, False),
        (f"Expected loss calibrated to a {cfg['profitability']['target_annual_loss_rate']:.0%} annual loss rate "
         "on balances (sensitivity 4-12% in the docs).", 9, False),
        ("The dormancy model predicts who is likely to go dormant, not who will respond to an offer.", 9, False),
        ("At NT$100 per contact only a few dozen customers are worth contacting; cheap digital nudges "
         "(about NT$10) make sense for roughly 1,000.", 9, False),
    ])
    return [p1, p2, p3, p4]


def write_report(cfg) -> None:
    pages_dir = RPT / "definition" / "pages"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages = build_pages(cfg)
    for p in pages:
        pd_ = pages_dir / p.key
        (pd_ / "visuals").mkdir(parents=True)
        (pd_ / "page.json").write_text(json.dumps({
            "$schema": PAGE_SCHEMA, "name": p.key, "displayName": p.display,
            "displayOption": "FitToPage", "height": H, "width": W}, indent=2), encoding="utf-8")
        for v in p.visuals:
            vd = pd_ / "visuals" / v["name"]
            vd.mkdir()
            (vd / "visual.json").write_text(json.dumps(v, indent=2), encoding="utf-8")
    (pages_dir / "pages.json").write_text(json.dumps({
        "$schema": PAGES_SCHEMA, "pageOrder": [p.key for p in pages], "activePageName": pages[0].key}, indent=2),
        encoding="utf-8")

    # custom theme (validated color-blind-safe palette) on top of the built-in base theme
    reg = RPT / "StaticResources" / "RegisteredResources"
    reg.mkdir(parents=True, exist_ok=True)
    shutil.copy(PBI / "theme.json", reg / "CreditCardTheme.json")
    rj_path = RPT / "definition" / "report.json"
    rj = json.loads(rj_path.read_text(encoding="utf-8"))
    version = rj["themeCollection"]["baseTheme"]["reportVersionAtImport"]
    rj["themeCollection"]["customTheme"] = {"name": "CreditCardTheme.json", "reportVersionAtImport": version,
                                           "type": "RegisteredResources"}
    rj["resourcePackages"] = [p for p in rj["resourcePackages"] if p["name"] != "RegisteredResources"] + [{
        "name": "RegisteredResources", "type": "RegisteredResources",
        "items": [{"name": "CreditCardTheme.json", "path": "CreditCardTheme.json", "type": "CustomTheme"}]}]
    rj_path.write_text(json.dumps(rj, indent=2), encoding="utf-8")


def main() -> None:
    if not (SM.exists() and RPT.exists()):
        raise SystemExit(f"Create the empty project first: Power BI Desktop > File > Save as > {NAME}.pbip in {PBI}")
    cfg = load_config()
    write_semantic_model()
    write_report(cfg)
    n_vis = sum(1 for _ in (RPT / "definition" / "pages").rglob("visual.json"))
    print(f"wrote {len(TABLES) + 1} tables, {len(MEASURES)} measures, {len(RELATIONSHIPS)} relationships, "
          f"4 pages, {n_vis} visuals")


if __name__ == "__main__":
    main()
