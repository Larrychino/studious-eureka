"""Customer discovery tracker (Phase 1: 30 interviews in 90 days).

Scores each problem 0-3:
  0 never happens, 1 happens but tolerated, 2 painful and recurring,
  3 painful and they have already spent money or time trying to fix it.

Go signals after 30 interviews (from the plan):
  stock mismatch       >= 10 people score 2 or 3
  fridge excursion     >= 10 people score 2 or 3
  delivery temperature >= 3 of 5 pharmacists score 2 or 3
  paper charts         >= 8 homes still on paper
The problem with the most 3s becomes the headline product.
"""

from __future__ import annotations

from collections import Counter

from .models import Interview

GROUP_TARGETS = {"manager": 12, "carer": 10, "pharmacist": 5, "icb_pharmacist": 3}
PROBLEMS = {
    "stock_mismatch": "Stock not matching records",
    "fridge_excursion": 'Fridge out of range and "is it still safe?"',
    "delivery_temperature": "Unverified delivery temperature",
}


def summarise(interviews: list[Interview]) -> dict:
    by_group = Counter(i.group for i in interviews)
    painful = {p: sum(1 for i in interviews if getattr(i, f"score_{p}") >= 2) for p in PROBLEMS}
    threes = {p: sum(1 for i in interviews if getattr(i, f"score_{p}") == 3) for p in PROBLEMS}
    pharmacists = [i for i in interviews if i.group == "pharmacist"]
    pharm_painful = sum(1 for i in pharmacists if i.score_delivery_temperature >= 2)
    on_paper = sum(1 for i in interviews if i.group == "manager" and i.still_on_paper)
    late = [i.id for i in interviews if i.written_up_at is None or
            (i.written_up_at.date() - i.held_on).days > 1]

    signals = {
        "stock_mismatch": {"label": PROBLEMS["stock_mismatch"], "value": painful["stock_mismatch"], "target": 10,
                           "met": painful["stock_mismatch"] >= 10, "rule": "At least 10 people score 2 or 3"},
        "fridge_excursion": {"label": PROBLEMS["fridge_excursion"], "value": painful["fridge_excursion"], "target": 10,
                             "met": painful["fridge_excursion"] >= 10, "rule": "At least 10 people score 2 or 3"},
        "delivery_temperature": {"label": PROBLEMS["delivery_temperature"], "value": pharm_painful, "target": 3,
                                 "met": pharm_painful >= 3, "rule": "At least 3 of 5 pharmacists score 2 or 3"},
        "paper_charts": {"label": "Paper charts still in use", "value": on_paper, "target": 8,
                         "met": on_paper >= 8, "rule": "At least 8 homes still on paper"},
    }
    headline = None
    if any(threes.values()):
        headline = max(threes, key=lambda p: threes[p])
    # Gate 1 of the 24-month plan: at least 10 of 30 interviews score a problem 2 or 3.
    any_painful = sum(1 for i in interviews if max(i.score_stock_mismatch, i.score_fridge_excursion,
                                                   i.score_delivery_temperature) >= 2)
    return {
        "total": len(interviews),
        "target_total": sum(GROUP_TARGETS.values()),
        "by_group": {g: {"done": by_group.get(g, 0), "target": t} for g, t in GROUP_TARGETS.items()},
        "painful_counts": painful,
        "three_counts": threes,
        "signals": signals,
        "headline_problem": headline,
        "headline_label": PROBLEMS.get(headline) if headline else None,
        "gate_validate_met": any_painful >= 10,
        "gate_validate_value": any_painful,
        "pilot_interest": sum(1 for i in interviews if i.pilot_interest),
        "not_written_up_within_24h": late,
    }
