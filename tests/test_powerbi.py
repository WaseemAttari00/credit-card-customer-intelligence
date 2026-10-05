"""The Power BI project (PBIP) must stay consistent with itself: every field a visual uses has to
exist in the semantic model, and every column a DAX measure references has to exist too."""
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "dashboard" / "powerbi"
SM = ROOT / "CreditCardIntelligence.SemanticModel" / "definition"
RPT = ROOT / "CreditCardIntelligence.Report" / "definition"

pytestmark = pytest.mark.skipif(not (SM / "tables").exists(), reason="run scripts/build_powerbi.py first")


def model_objects():
    cols, measures = set(), set()
    for f in (SM / "tables").glob("*.tmdl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if m := re.match(r"^\tcolumn (.+)$", line):
                cols.add((f.stem, m.group(1).strip("'")))
            if m := re.match(r"^\tmeasure (.+?) = ", line):
                measures.add(m.group(1).strip("'"))
    return cols, measures


def test_visual_fields_exist_in_model():
    cols, measures = model_objects()
    pattern = r'"(Column|Measure)": \{"Expression": \{"SourceRef": \{"Entity": "([^"]+)"\}\}, "Property": "([^"]+)"'
    for f in RPT.rglob("visual.json"):
        text = json.dumps(json.loads(f.read_text(encoding="utf-8")))
        for kind, entity, prop in re.findall(pattern, text):
            if kind == "Column":
                assert (entity, prop) in cols, f"{f.parent.name}: {entity}.{prop}"
            else:
                assert prop in measures, f"{f.parent.name}: measure {prop}"


def test_dax_references_exist():
    cols, measures = model_objects()
    dax = "\n".join(l for l in (SM / "tables" / "_Measures.tmdl").read_text(encoding="utf-8").splitlines()
                    if l.startswith("\tmeasure "))
    for table, column in re.findall(r"(\w+)\[([^\]]+)\]", dax):
        assert (table, column) in cols, f"{table}[{column}]"
    for ref in re.findall(r"(?<![\w\]])\[([^\]]+)\]", dax):
        assert ref in measures, f"[{ref}]"


def test_relationships_use_model_columns():
    cols, _ = model_objects()
    for table, column in re.findall(r"Column: (\w+)\.(\w+)", (SM / "relationships.tmdl").read_text(encoding="utf-8")):
        assert (table, column) in cols


def test_pages_and_visual_names_unique_and_on_page():
    pages = json.loads((RPT / "pages" / "pages.json").read_text(encoding="utf-8"))["pageOrder"]
    assert len(pages) == len(set(pages)) == 4
    for p in pages:
        page = json.loads((RPT / "pages" / p / "page.json").read_text(encoding="utf-8"))
        names = []
        for f in (RPT / "pages" / p / "visuals").rglob("visual.json"):
            v = json.loads(f.read_text(encoding="utf-8"))
            names.append(v["name"])
            pos = v["position"]
            assert pos["x"] + pos["width"] <= page["width"] and pos["y"] + pos["height"] <= page["height"], v["name"]
        assert len(names) == len(set(names))
