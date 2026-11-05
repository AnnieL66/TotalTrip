from datetime import date

from app import brief as B
from app.ai.offline import extract

TODAY = date(2026, 10, 17)


def _extract(text, current=None):
    return extract(text, current or B.with_defaults(B.empty_brief()), today=TODAY)


def test_full_sentence():
    f, s = _extract("Going to a wedding near Millennium Park from the Upper West Side, Nov 12-16, under $900. One checked bag, land before 6pm")
    assert f["origin"] == "upper_west_side"
    assert f["destination"] == "millennium_park"
    assert f["depart_date"] == "2026-11-12" and f["return_date"] == "2026-11-16"
    assert f["budget"] == 900
    assert f["checked_bags"] == 1
    assert f["arrive_by"] == "18:00"
    assert not s.get("generic_place")


def test_generic_city_is_an_assumption():
    f, s = _extract("Chicago from NYC")
    assert f["destination"] == "chicago_generic"
    assert set(s["generic_place"]) == {"origin", "destination"}


def test_nights_and_year_rollover():
    f, _ = _extract("leaving Jan 3 for 4 nights")
    assert f["depart_date"] == "2027-01-03"
    assert f["return_date"] == "2027-01-07"


def test_cross_month_range():
    f, _ = _extract("Oct 30 - Nov 2")
    assert f["depart_date"] == "2026-10-30" and f["return_date"] == "2026-11-02"


def test_priority_and_driving():
    f, _ = _extract("I want the easiest trip, I'll drive to the airport")
    assert f["priority"] == "easiest" and f["will_drive"] is True


def test_group_and_unsupported_city_signals():
    _, s = _extract("me and my kids to Boston")
    assert s["group"] and s["unsupported_city"] == "boston"


def test_personal_item_only():
    f, _ = _extract("just a backpack")
    assert f["carry_on"] == 0 and f["checked_bags"] == 0
