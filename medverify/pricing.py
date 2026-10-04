"""Plans and monthly pricing from the business plan (starting assumptions to test)."""

from __future__ import annotations

from dataclasses import dataclass

PLANS = {
    "fridge_safe": {"name": "Fridge Safe", "buyer": "Care home", "unit": "fridge", "price_gbp": 20.0,
                    "includes": "Sensor per fridge, medicine-aware alerts, decision engine, CQC log"},
    "stock_verify": {"name": "Stock Verify", "buyer": "Care home", "unit": "resident tray", "price_gbp": 3.0,
                     "includes": "Weight-sensing trays for fridge items, discrepancy alerts, eMAR link"},
    "delivery_proof": {"name": "Delivery Proof", "buyer": "Community pharmacy", "unit": "box", "price_gbp": 15.0,
                       "includes": "Reusable tag per delivery box, handover scan, journey record"},
    "paper_to_digital": {"name": "Paper to Digital", "buyer": "Small homes on paper", "unit": "home",
                         "price_gbp": 49.0, "includes": "Simple digital MAR chart plus Fridge Safe"},
    "group": {"name": "Group", "buyer": "Care groups and NHS teams", "unit": "contract", "price_gbp": None,
              "includes": "All of the above plus a dashboard across homes (quoted per contract)"},
}
MIN_CONTRACT_MONTHS = 12
HARDWARE_PAYBACK_MONTHS = 6


@dataclass
class Quote:
    lines: list[dict]
    monthly_gbp: float
    annual_gbp: float
    hardware_cost_gbp: float | None
    hardware_payback_ok: bool | None


def home_quote(fridges: int, trays: int, paper_to_digital: bool = False,
               hardware_cost_gbp: float | None = None) -> Quote:
    lines = []
    if paper_to_digital:
        # £49 covers the digital MAR and Fridge Safe for the home.
        lines.append({"plan": "paper_to_digital", "qty": 1, "unit_gbp": 49.0, "total_gbp": 49.0})
    elif fridges:
        lines.append({"plan": "fridge_safe", "qty": fridges, "unit_gbp": 20.0, "total_gbp": 20.0 * fridges})
    if trays:
        lines.append({"plan": "stock_verify", "qty": trays, "unit_gbp": 3.0, "total_gbp": 3.0 * trays})
    monthly = round(sum(l["total_gbp"] for l in lines), 2)
    ok = None if hardware_cost_gbp is None else hardware_cost_gbp <= HARDWARE_PAYBACK_MONTHS * monthly
    return Quote(lines, monthly, round(monthly * 12, 2), hardware_cost_gbp, ok)


def pharmacy_quote(boxes: int) -> Quote:
    lines = [{"plan": "delivery_proof", "qty": boxes, "unit_gbp": 15.0, "total_gbp": 15.0 * boxes}] if boxes else []
    monthly = round(sum(l["total_gbp"] for l in lines), 2)
    return Quote(lines, monthly, round(monthly * 12, 2), None, None)


def homes_needed_for(target_annual_gbp: float, monthly_per_home: float) -> int:
    import math

    return math.ceil(target_annual_gbp / (monthly_per_home * 12))
