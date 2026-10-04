"""Parsing of what staff scan.

Two codes are supported:

1. Pharmacy label QR (MedVerify format, printed by the pharmacy or our label tool):
       MV1|<label code>|<medicine>|<strength>|<resident ref>|<dose instructions>|<times>|<expiry>|<gtin>
   times are HH:MM separated by ';', expiry is YYYY-MM-DD, gtin optional.

2. GS1 DataMatrix on the manufacturer's pack (UK packs carry these): GTIN (01),
   expiry (17), batch (10) and serial (21). Accepts raw scans with FNC1/GS
   separators and the human-readable "(01)...(17)..." form.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date

GS = "\x1d"
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class LabelError(ValueError):
    pass


@dataclass
class PharmacyLabel:
    label_code: str
    medicine_name: str
    strength: str | None
    resident_ref: str
    dose_instructions: str | None
    dose_times: list[str] = field(default_factory=list)
    expiry: date | None = None
    gtin: str | None = None


@dataclass
class GS1Pack:
    gtin: str | None = None
    expiry: date | None = None
    batch: str | None = None
    serial: str | None = None


def parse_pharmacy_label(text: str) -> PharmacyLabel:
    text = text.strip()
    if not text.startswith("MV1|"):
        raise LabelError("Not a MedVerify pharmacy label (expected it to start with MV1|)")
    parts = text.split("|")
    if len(parts) < 8:
        raise LabelError("Label is missing fields")
    parts += [""] * (9 - len(parts))
    _, code, name, strength, resident_ref, dose, times, expiry, gtin = parts[:9]
    if not code or not name or not resident_ref:
        raise LabelError("Label code, medicine name and resident reference are required")
    dose_times = [t.strip() for t in times.split(";") if t.strip()]
    for t in dose_times:
        if not _TIME.match(t):
            raise LabelError(f"Invalid dose time '{t}' (use HH:MM)")
    expiry_date = None
    if expiry:
        try:
            expiry_date = date.fromisoformat(expiry)
        except ValueError as exc:
            raise LabelError(f"Invalid expiry '{expiry}' (use YYYY-MM-DD)") from exc
    if gtin and not validate_gtin(gtin):
        raise LabelError(f"Invalid GTIN check digit: {gtin}")
    return PharmacyLabel(
        label_code=code.strip(),
        medicine_name=name.strip(),
        strength=strength.strip() or None,
        resident_ref=resident_ref.strip(),
        dose_instructions=dose.strip() or None,
        dose_times=dose_times,
        expiry=expiry_date,
        gtin=gtin.strip() or None,
    )


def build_pharmacy_label(label: PharmacyLabel) -> str:
    fields = [label.label_code, label.medicine_name, label.strength or "", label.resident_ref,
              label.dose_instructions or "", ";".join(label.dose_times),
              label.expiry.isoformat() if label.expiry else "", label.gtin or ""]
    for f in fields:
        if "|" in f:
            raise LabelError("Fields may not contain '|'")
    return "MV1|" + "|".join(fields)


def validate_gtin(gtin: str) -> bool:
    if not gtin.isdigit() or len(gtin) not in (8, 12, 13, 14):
        return False
    digits = [int(d) for d in gtin]
    check = digits.pop()
    total = sum(d * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return (10 - total % 10) % 10 == check


def _gs1_date(yymmdd: str) -> date:
    yy, mm, dd = int(yymmdd[:2]), int(yymmdd[2:4]), int(yymmdd[4:6])
    year = 2000 + yy
    if dd == 0:  # GS1: day 00 means the last day of the month
        dd = calendar.monthrange(year, mm)[1]
    return date(year, mm, dd)


_FIXED = {"01": 14, "17": 6, "11": 6, "15": 6}
_VARIABLE = {"10", "21"}


def parse_gs1(text: str) -> GS1Pack:
    text = text.strip()
    for prefix in ("]d2", "]Q3", "]C1", "]e0"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    if text.startswith("("):
        # Human readable: (01)05012345678900(17)271231(10)AB12
        pairs = re.findall(r"\((\d{2,4})\)([^(]*)", text)
        values = {ai: val.strip() for ai, val in pairs}
    else:
        values = {}
        i = 0
        while i < len(text):
            if text[i] == GS:
                i += 1
                continue
            ai = text[i:i + 2]
            i += 2
            if ai in _FIXED:
                values[ai] = text[i:i + _FIXED[ai]]
                i += _FIXED[ai]
            elif ai in _VARIABLE:
                end = text.find(GS, i)
                end = len(text) if end == -1 else end
                values[ai] = text[i:end][:20]
                i = end
            else:
                raise LabelError(f"Unsupported GS1 application identifier '{ai}'")
    if not values:
        raise LabelError("No GS1 data found")
    pack = GS1Pack()
    if "01" in values:
        if not validate_gtin(values["01"]):
            raise LabelError("GTIN check digit is wrong; rescan the pack")
        pack.gtin = values["01"]
    if "17" in values:
        try:
            pack.expiry = _gs1_date(values["17"])
        except ValueError as exc:
            raise LabelError("Invalid expiry date in pack code") from exc
    pack.batch = values.get("10")
    pack.serial = values.get("21")
    return pack
