from app import brief as B


def test_defaults_are_labeled_assumptions(sample_brief):
    assert sample_brief["carry_on"] == {"value": 1, "display": "1 carry-on bag", "provenance": "assumed"}
    assert sample_brief["priority"]["provenance"] == "assumed"
    assert sample_brief["origin"]["provenance"] == "user_said"
    assert B.is_ready(sample_brief)


def test_assumption_never_overwrites_what_user_said(sample_brief):
    b, _ = B.apply_updates(sample_brief, {"checked_bags": 2}, "user_said")
    b, _ = B.apply_updates(b, {"checked_bags": 0}, "assumed")
    assert b["checked_bags"]["value"] == 2


def test_launch_market_is_enforced():
    _, errors = B.apply_updates(B.empty_brief(), {"origin": "Hyde Park"}, "user_said")
    assert errors and "New York City" in errors[0]


def test_return_must_follow_departure(sample_brief):
    b, errors = B.apply_updates(sample_brief, {"return_date": "2026-11-10"}, "user_said")
    assert errors
    assert b["return_date"]["value"] == "2026-11-16"


def test_missing_required_fields():
    b = B.with_defaults(B.empty_brief())
    assert B.missing_required(b) == ["origin", "destination", "depart_date", "return_date"]


def test_only_solo_trips():
    _, errors = B.apply_updates(B.empty_brief(), {"travelers": 3}, "user_said")
    assert errors
