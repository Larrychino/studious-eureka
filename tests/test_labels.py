from datetime import date

import pytest

from medverify.labels import (
    GS,
    LabelError,
    PharmacyLabel,
    build_pharmacy_label,
    parse_gs1,
    parse_pharmacy_label,
    validate_gtin,
)


def test_pharmacy_label_round_trip():
    label = PharmacyLabel("A1", "Insulin glargine", "100u/ml", "R-9", "10 units at night", ["21:00"],
                          date(2027, 5, 1), "09506000134352")
    parsed = parse_pharmacy_label(build_pharmacy_label(label))
    assert parsed == label


def test_pharmacy_label_without_optional_fields():
    parsed = parse_pharmacy_label("MV1|A2|Eye drops||R-1||||")
    assert parsed.strength is None and parsed.dose_times == [] and parsed.expiry is None


@pytest.mark.parametrize("text", ["hello", "MV1|A|B", "MV1||Name||R|||", "MV1|A|B||R||25:00|", "MV1|A|B||R|||2027-13-01"])
def test_bad_labels_rejected(text):
    with pytest.raises(LabelError):
        parse_pharmacy_label(text)


def test_gs1_raw_with_group_separators():
    pack = parse_gs1("]d2" + "01" + "09506000134352" + "17" + "271231" + "10" + "BATCH7" + GS + "21" + "SER123")
    assert pack.gtin == "09506000134352"
    assert pack.expiry == date(2027, 12, 31)
    assert pack.batch == "BATCH7" and pack.serial == "SER123"


def test_gs1_human_readable_and_day_zero():
    pack = parse_gs1("(01)05012345678900(17)270200(10)AB1")
    assert pack.expiry == date(2027, 2, 28)  # day 00 = last day of month
    assert pack.batch == "AB1"


def test_gs1_bad_check_digit():
    with pytest.raises(LabelError):
        parse_gs1("(01)05012345678901")


def test_validate_gtin():
    assert validate_gtin("09506000134352")
    assert not validate_gtin("09506000134353")
    assert not validate_gtin("abc")
