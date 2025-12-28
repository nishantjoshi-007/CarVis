# Cleaning rules for the source CSV. Pure functions: string in, value plus an
# optional Issue out. No database, no files -- which is what makes them testable.
#
# The rule: a value that was never real becomes NULL. A value that is real but
# extreme is kept and flagged. NULL means "unknown", never zero. (decision.md D5)

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# 2^31-1. An overflow sentinel from upstream, not an odometer reading. 7 rows.
MILEAGE_INT_MAX_SENTINEL = 2_147_483_647

# 67 rows sit between 1M and 2M km -- implausible, but not provably corrupt.
MILEAGE_IMPLAUSIBLE_KM = 1_000_000

# Placeholder prices at both ends: 356 rows under $100, one at $26.3M.
PRICE_MIN_PLAUSIBLE_USD = 100
PRICE_MAX_PLAUSIBLE_USD = 1_000_000

# Excel read the door-range strings as dates and rewrote them. All rows are
# affected; only these three values survive. Reversible only because the domain
# is tiny -- a wider range would have collapsed onto duplicate dates. (D7)
DOORS_EXCEL_REPAIR = {
    "04-May": "4-5",
    "02-Mar": "2-3",
}

# The source writes a missing levy as a literal hyphen. 5,819 rows.
MISSING_MARKER = "-"


@dataclass(frozen=True)
class Issue:
    # One field-level problem, destined for etl_quarantine
    column: str
    raw_value: str
    issue: str      # machine-readable reason code
    action: str     # nulled | repaired | flagged | rejected


def parse_levy(raw: str) -> tuple[Decimal | None, Issue | None]:
    # NULL not 0: AVG() ignores NULLs but averages zeros, which would understate
    # the mean levy by ~30% and destroy the missing-data signal. (D5)
    raw = (raw or "").strip()
    if raw == "" or raw == MISSING_MARKER:
        return None, Issue("Levy", raw, "missing_marker", "nulled")
    try:
        return Decimal(raw), None
    except InvalidOperation:
        return None, Issue("Levy", raw, "unparseable_number", "nulled")


def parse_mileage(raw: str) -> tuple[int | None, bool, Issue | None]:
    # '186005 km' -> 186005. Returns (value, is_suspect, issue).
    raw = (raw or "").strip()
    text = raw.replace(" km", "").replace("km", "").strip()
    try:
        value = int(text)
    except ValueError:
        return None, False, Issue("Mileage", raw, "unparseable_number", "nulled")

    if value == MILEAGE_INT_MAX_SENTINEL:
        return None, False, Issue("Mileage", raw, "int32_max_sentinel", "nulled")
    if value == 0:
        # 721 rows; no used car has zero km, so this is missing data as zero
        return None, False, Issue("Mileage", raw, "zero_means_missing", "nulled")
    if value > MILEAGE_IMPLAUSIBLE_KM:
        return value, True, Issue("Mileage", raw, "implausibly_high", "flagged")
    if value < 0:
        return None, False, Issue("Mileage", raw, "negative", "nulled")
    return value, False, None


def parse_doors(raw: str) -> tuple[str, Issue | None]:
    # 'repaired', not 'nulled': unlike a sentinel, the original is recoverable
    raw = (raw or "").strip()
    if raw in DOORS_EXCEL_REPAIR:
        return DOORS_EXCEL_REPAIR[raw], Issue("Doors", raw, "excel_date_coercion", "repaired")
    return raw, None


def parse_engine_volume(raw: str) -> tuple[Decimal | None, bool, Issue | None]:
    # '2.0 Turbo' packs a measurement and an attribute into one column. Split
    # them, or nobody can filter on turbo without string matching. 1,931 rows.
    raw = (raw or "").strip()
    is_turbo = "turbo" in raw.lower()
    text = raw.lower().replace("turbo", "").strip()
    if text == "":
        return None, is_turbo, Issue("Engine volume", raw, "empty", "nulled")
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None, is_turbo, Issue("Engine volume", raw, "unparseable_number", "nulled")
    if value <= 0:
        return None, is_turbo, Issue("Engine volume", raw, "zero_or_negative", "nulled")
    return value, is_turbo, None


def parse_price(raw: str) -> tuple[Decimal | None, bool, Issue | None]:
    # Price is the measure the dashboard exists to show, so a row without one is
    # rejected. Implausible prices are kept and flagged so queries can choose.
    raw = (raw or "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, False, Issue("Price", raw, "unparseable_number", "rejected")
    if value <= 0:
        return None, False, Issue("Price", raw, "non_positive", "rejected")
    if value < PRICE_MIN_PLAUSIBLE_USD:
        return value, True, Issue("Price", raw, "implausibly_low", "flagged")
    if value > PRICE_MAX_PLAUSIBLE_USD:
        return value, True, Issue("Price", raw, "implausibly_high", "flagged")
    return value, False, None


def parse_yes_no(raw: str, column: str) -> tuple[bool | None, Issue | None]:
    raw = (raw or "").strip()
    if raw.lower() == "yes":
        return True, None
    if raw.lower() == "no":
        return False, None
    return None, Issue(column, raw, "unrecognised_boolean", "nulled")


def parse_int(raw: str, column: str) -> tuple[int | None, Issue | None]:
    # Tolerant: cylinders arrive as '6.0', airbags as '12'
    raw = (raw or "").strip()
    if raw == "" or raw == MISSING_MARKER:
        return None, Issue(column, raw, "missing_marker", "nulled")
    try:
        return int(float(raw)), None
    except ValueError:
        return None, Issue(column, raw, "unparseable_number", "nulled")


def parse_year(raw: str) -> tuple[int | None, Issue | None]:
    # Source range is 1939-2020
    raw = (raw or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None, Issue("Prod. year", raw, "unparseable_number", "rejected")
    if not (1900 <= value <= 2100):
        return None, Issue("Prod. year", raw, "out_of_range", "rejected")
    return value, None
