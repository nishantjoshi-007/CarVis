"""
Unit tests for the cleaning rules.

These exist because etl/clean.py was written as PURE functions -- string in,
value plus optional Issue out, no database, no files. That design choice is
only worth anything if it is actually exercised, and "the rules are testable"
is a claim until there are tests.

Every case below is drawn from a real defect in the source CSV. The numbers in
the comments are the true counts from data/car_price_prediction.csv.

Run:  ./.venv/bin/pytest -q
"""

from decimal import Decimal

from etl.clean import (
    MILEAGE_INT_MAX_SENTINEL,
    parse_doors,
    parse_engine_volume,
    parse_int,
    parse_levy,
    parse_mileage,
    parse_price,
    parse_year,
    parse_yes_no,
)


# --- the governing rule ----------------------------------------------------
# A value that was never real becomes NULL. It is never replaced with a
# substitute number, because that would invent data nobody recorded.

class TestCorruptValuesBecomeNull:
    def test_int32_max_mileage_is_nulled_not_capped(self):
        """2^31-1 on 7 rows: an overflow sentinel, not an odometer reading."""
        value, suspect, issue = parse_mileage(f"{MILEAGE_INT_MAX_SENTINEL} km")
        assert value is None, "must be NULL -- capping would invent a reading"
        assert not suspect
        assert issue.issue == "int32_max_sentinel"
        assert issue.action == "nulled"

    def test_zero_mileage_is_nulled(self):
        """721 rows read 0 km, which no used car has."""
        value, _, issue = parse_mileage("0 km")
        assert value is None
        assert issue.issue == "zero_means_missing"

    def test_hyphen_levy_is_nulled(self):
        """5,819 rows (30.2%) hold a literal '-' in a numeric column."""
        value, issue = parse_levy("-")
        assert value is None, "0 would drag AVG() down by ~30%"
        assert issue.action == "nulled"


class TestImplausibleValuesAreKeptAndFlagged:
    """Real-but-extreme is not the same as corrupt. Keep it, raise a flag."""

    def test_high_mileage_is_flagged_not_dropped(self):
        value, suspect, issue = parse_mileage("1500000 km")
        assert value == 1_500_000, "not provably corrupt, so it survives"
        assert suspect is True
        assert issue.action == "flagged"

    def test_low_price_is_flagged_not_dropped(self):
        value, suspect, issue = parse_price("30")
        assert value == Decimal("30")
        assert suspect is True
        assert issue.action == "flagged"

    def test_high_price_is_flagged(self):
        value, suspect, issue = parse_price("26307500")
        assert value == Decimal("26307500")
        assert suspect is True


class TestMileage:
    def test_strips_the_unit(self):
        assert parse_mileage("186005 km") == (186005, False, None)

    def test_negative_is_nulled(self):
        value, _, issue = parse_mileage("-500 km")
        assert value is None and issue.issue == "negative"

    def test_garbage_is_nulled(self):
        value, _, issue = parse_mileage("unknown")
        assert value is None and issue.issue == "unparseable_number"


class TestDoorsExcelDamage:
    """Excel rewrote every row of this column as a date."""

    def test_repairs_four_to_five(self):
        assert parse_doors("04-May")[0] == "4-5"        # 18,332 rows

    def test_repairs_two_to_three(self):
        assert parse_doors("02-Mar")[0] == "2-3"        # 777 rows

    def test_repair_is_logged_as_repaired_not_nulled(self):
        _, issue = parse_doors("04-May")
        assert issue.action == "repaired", "recoverable, unlike a sentinel"
        assert issue.issue == "excel_date_coercion"

    def test_undamaged_value_passes_through(self):
        assert parse_doors(">5") == (">5", None)        # no date could be made of it


class TestEngineVolumeSplitsTwoFactsApart:
    """'2.0 Turbo' is a measurement AND an attribute in one column."""

    def test_turbo_is_extracted(self):
        volume, turbo, issue = parse_engine_volume("2.0 Turbo")
        assert volume == Decimal("2.0") and turbo is True and issue is None

    def test_plain_volume(self):
        volume, turbo, _ = parse_engine_volume("3.5")
        assert volume == Decimal("3.5") and turbo is False

    def test_zero_volume_is_nulled(self):
        volume, _, issue = parse_engine_volume("0")
        assert volume is None and issue.issue == "zero_or_negative"


class TestPriceRejectsUnusableRows:
    """Price is the measure the dashboard exists to show: no price, no row."""

    def test_zero_price_is_rejected(self):
        value, _, issue = parse_price("0")
        assert value is None and issue.action == "rejected"

    def test_normal_price_has_no_issue(self):
        assert parse_price("13172") == (Decimal("13172"), False, None)


class TestSmallParsers:
    def test_yes_no(self):
        assert parse_yes_no("Yes", "Leather interior")[0] is True
        assert parse_yes_no("No", "Leather interior")[0] is False

    def test_unrecognised_boolean_is_nulled(self):
        value, issue = parse_yes_no("maybe", "Leather interior")
        assert value is None and issue.issue == "unrecognised_boolean"

    def test_int_accepts_float_text(self):
        assert parse_int("6.0", "Cylinders")[0] == 6      # source writes cylinders as floats

    def test_year_in_range(self):
        assert parse_year("2015") == (2015, None)

    def test_year_out_of_range_is_rejected(self):
        value, issue = parse_year("1800")
        assert value is None and issue.action == "rejected"


class TestEveryRuleToleratesEmptyInput:
    """No rule may raise on missing input -- a crash here kills the whole load."""

    def test_no_parser_raises_on_empty_string(self):
        parse_levy("")
        parse_mileage("")
        parse_doors("")
        parse_engine_volume("")
        parse_price("")
        parse_year("")
        parse_int("", "Cylinders")
        parse_yes_no("", "Leather interior")
