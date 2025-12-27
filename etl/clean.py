"""
Cleaning rules for the CarVis source CSV.

Every function here is PURE: raw string in, cleaned value plus an optional
Issue out. No database, no file access, no globals. That is deliberate --
pure functions are the part of a pipeline you can unit-test without standing
up infrastructure, and they are the part you can reason about out loud.

THE GUIDING RULE
----------------
NULL means "we do not know this value". It never means zero.

    * A value that was never real  -> NULL      (corrupt, sentinel, placeholder)
    * A value that is real but extreme -> KEEP, flagged  (implausible, not impossible)

Substituting a made-up number for a corrupt one is not cleaning, it is
inventing data. Capping the INT_MAX mileage to 1,000,000 would assert that a
car drove a million kilometres -- a claim nobody ever made. NULL asserts
nothing, which is exactly what we know.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

# --- Constants, with the reasoning that produced them -----------------------

# 2^31 - 1, the maximum value of a signed 32-bit integer. Seven rows carry it.
# No odometer reads this; it is an overflow or an "unknown" sentinel escaping
# from an upstream system.
MILEAGE_INT_MAX_SENTINEL = 2_147_483_647

# 67 rows sit between 1,000,000 and 2,000,000 km -- values like 1,111,111 and
# 1,888,000 that smell like keyboard mash, but are not provably corrupt the way
# INT_MAX is. We keep them and raise a flag rather than destroy them.
MILEAGE_IMPLAUSIBLE_KM = 1_000_000

# 356 listings are priced under $100 (values of 3, 25, 30, 50...). These are
# placeholder prices -- "call for price" encoded as a number. One listing is
# priced at $26,307,500. Both ends are kept but flagged, because price is the
# measure this dashboard exists to show; nulling it would empty the chart.
PRICE_MIN_PLAUSIBLE_USD = 100
PRICE_MAX_PLAUSIBLE_USD = 1_000_000

# Excel opened the source CSV and converted the door-range strings into dates.
# All 19,237 rows are affected; only three distinct values survive.
#   "4-5" -> "04-May"   (18,332 rows)
#   "2-3" -> "02-Mar"   (   777 rows)
#   ">5"  -> ">5"       (   128 rows, undamaged: no date could be made of it)
# We can reverse this ONLY because the domain is tiny and the mapping is
# unambiguous. Had Doors ranged 1-10, the information would be gone for good.
DOORS_EXCEL_REPAIR = {
    "04-May": "4-5",
    "02-Mar": "2-3",
}

# The source encodes a missing levy as a literal hyphen, in an otherwise
# numeric column. 5,819 rows (30.2%).
MISSING_MARKER = "-"


@dataclass(frozen=True)
class Issue:
    """One field-level data-quality event, destined for etl_quarantine."""

    column: str
    raw_value: str
    issue: str      # machine-readable reason code
    action: str     # 'nulled' | 'repaired' | 'flagged' | 'rejected'


# --- Field parsers ----------------------------------------------------------


def parse_levy(raw: str) -> tuple[Decimal | None, Issue | None]:
    """'1399' -> 1399 ;  '-' -> NULL.

    Why NULL and not 0: AVG() ignores NULLs but happily averages in zeros.
    Loading 30% of this column as 0 would understate the average levy by
    roughly a third -- a wrong number on a dashboard someone makes decisions
    from. It would also destroy the missing-data signal, because the gap
    between COUNT(levy_usd) and COUNT(*) is what tells you how much is absent.
    """
    raw = (raw or "").strip()
    if raw == "" or raw == MISSING_MARKER:
        return None, Issue("Levy", raw, "missing_marker", "nulled")
    try:
        return Decimal(raw), None
    except InvalidOperation:
        return None, Issue("Levy", raw, "unparseable_number", "nulled")


def parse_mileage(raw: str) -> tuple[int | None, bool, Issue | None]:
    """'186005 km' -> 186005. Returns (value, is_suspect, issue).

    Three distinct problems live in this one column:
      * the unit is glued to the number in every row       -> strip it
      * 721 rows read 0, which no used car has             -> NULL (missing)
      * 7 rows read 2,147,483,647 (INT_MAX)                -> NULL (sentinel)
      * 67 rows exceed 1,000,000 km                        -> keep, flagged
    """
    raw = (raw or "").strip()
    text = raw.replace(" km", "").replace("km", "").strip()
    try:
        value = int(text)
    except ValueError:
        return None, False, Issue("Mileage", raw, "unparseable_number", "nulled")

    if value == MILEAGE_INT_MAX_SENTINEL:
        return None, False, Issue("Mileage", raw, "int32_max_sentinel", "nulled")
    if value == 0:
        return None, False, Issue("Mileage", raw, "zero_means_missing", "nulled")
    if value > MILEAGE_IMPLAUSIBLE_KM:
        return value, True, Issue("Mileage", raw, "implausibly_high", "flagged")
    if value < 0:
        return None, False, Issue("Mileage", raw, "negative", "nulled")
    return value, False, None


def parse_doors(raw: str) -> tuple[str, Issue | None]:
    """'04-May' -> '4-5'. Undoes Excel's date coercion.

    Note this is 'repaired', not 'nulled': unlike the mileage sentinel, the
    original value here is RECOVERABLE, because the mapping is one-to-one over
    a three-value domain.
    """
    raw = (raw or "").strip()
    if raw in DOORS_EXCEL_REPAIR:
        return DOORS_EXCEL_REPAIR[raw], Issue("Doors", raw, "excel_date_coercion", "repaired")
    return raw, None


def parse_engine_volume(raw: str) -> tuple[Decimal | None, bool, Issue | None]:
    """'2.0 Turbo' -> (2.0, True). '3.5' -> (3.5, False).

    The source packs two different facts into one column: a measurement
    (displacement in litres) and an attribute (whether it is turbocharged).
    1,931 rows carry the suffix. We split them, because a column should hold
    one fact -- otherwise nobody can filter on turbo without string matching.
    """
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
    """Returns (value, is_suspect, issue).

    Price is the measure the whole dashboard is built on, so a row without one
    is useless and gets rejected outright. Implausible prices are kept and
    flagged so that queries can exclude them from averages by choice, rather
    than having that choice made for them at load time.
    """
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
    """'Yes'/'No' -> True/False."""
    raw = (raw or "").strip()
    if raw.lower() == "yes":
        return True, None
    if raw.lower() == "no":
        return False, None
    return None, Issue(column, raw, "unrecognised_boolean", "nulled")


def parse_int(raw: str, column: str) -> tuple[int | None, Issue | None]:
    """Tolerant integer parse. Cylinders arrive as '6.0', airbags as '12'."""
    raw = (raw or "").strip()
    if raw == "" or raw == MISSING_MARKER:
        return None, Issue(column, raw, "missing_marker", "nulled")
    try:
        return int(float(raw)), None
    except ValueError:
        return None, Issue(column, raw, "unparseable_number", "nulled")


def parse_year(raw: str) -> tuple[int | None, Issue | None]:
    """Production year. Source range is 1939-2020; anything outside is wrong."""
    raw = (raw or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return None, Issue("Prod. year", raw, "unparseable_number", "rejected")
    if not (1900 <= value <= 2100):
        return None, Issue("Prod. year", raw, "out_of_range", "rejected")
    return value, None
