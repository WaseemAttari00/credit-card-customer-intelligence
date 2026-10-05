"""Renders the Power BI PDF export (File > Export > Export to PDF) to one PNG per page for the README.

    python scripts/export_dashboard_images.py
"""
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parents[1]
PDF = ROOT / "dashboard" / "powerbi" / "CreditCardIntelligence.pdf"
OUT = ROOT / "dashboard" / "powerbi" / "screenshots"
NAMES = ["page1_executive", "page2_customers", "page3_risk", "page4_retention"]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with fitz.open(PDF) as doc:
        for page, name in zip(doc, NAMES):
            pix = page.get_pixmap(dpi=144)
            pix.save(OUT / f"{name}.png")
            print(f"{name}.png  {pix.width}x{pix.height}")


if __name__ == "__main__":
    main()
