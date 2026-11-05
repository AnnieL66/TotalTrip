"""Rule-based assistant used when no OpenAI key is configured.

It's deliberately simple: pull trip details out of the message with patterns,
record them in the brief, and ask for the next detail that changes the plan.
It also keeps the app fully demoable and testable without network access.
"""
from __future__ import annotations

import re
from datetime import date, timedelta

from .. import brief as B
from .. import clock, geo
from ..trips import current_brief
from .tools import ToolBox

MONTH_NAMES = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, start=1)}
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
MONTH_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
DAY_RE = r"(\d{1,2})(?:st|nd|rd|th)?"
WORD_NUMS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "no": 0, "zero": 0}
OTHER_CITIES = ["boston", "los angeles", "miami", "seattle", "denver", "austin", "san francisco", "atlanta",
                "washington dc", "philadelphia", "orlando", "las vegas", "dallas", "houston", "new orleans", "nashville"]

RETURN_WORDS = {"back", "home", "return", "returning"}
# A number followed by one of these is a count, not an amount of money.
NOT_MONEY = r"(?!\s*(?:checked|carry|bags?|nights?|days?|weeks?|hours?|hrs?|minutes?|mins?|stops?|transfers?|people|adults|travelers|am|pm)\b)"

QUESTIONS = {
    "origin": "Where are you leaving from? A neighborhood is enough, like Upper West Side or Park Slope.",
    "destination": "Where in Chicago are you headed? A neighborhood or landmark helps, because it decides which airport is cheapest overall.",
    "depart_date": "What day do you want to leave?",
    "return_date": "And when are you coming back? A date or a number of nights works.",
}


def _year_for(month: int, day: int, today: date) -> date | None:
    """The next time this month and day occurs, or None if it isn't a real date."""
    for year in (today.year, today.year + 1):
        try:
            d = date(year, month, day)
        except ValueError:
            continue
        if d >= today:
            return d
    return None


def _return_on(dep: date | None, dep_month: int, month: int, day: int) -> date | None:
    if not dep:
        return None
    try:
        return date(dep.year + (1 if month < dep_month else 0), month, day)
    except ValueError:
        return None


def _could_be_date(month: int, day: int) -> bool:
    """Tells a mistyped date (2/30) from numbers that were never a date (24/7, 50/50)."""
    return 1 <= month <= 12 and 1 <= day <= 31


def _num(token: str) -> int:
    return int(token) if token.isdigit() else WORD_NUMS.get(token.lower(), 1)


def _nights_in(t: str) -> int | None:
    m = re.search(r"\b(\d+|one|two|three|four|five|six|seven)\s+nights?\b", t) or re.search(r"\b(a)\s+week\b", t)
    if m:
        return 7 if m.group(0).endswith("week") else _num(m.group(1))
    m = re.search(r"\b(\d+|one|two|three|four|five|six|seven)[\s-]+days?\b", t)
    if m:  # a 3-day trip is Friday to Sunday: two nights
        return max(_num(m.group(1)) - 1, 1)
    if re.search(r"\blong\s+weekend\b", t):
        return 3
    return 2 if re.search(r"\ba\s+weekend\b", t) else None


def _month_day(d: date) -> str:
    return f"{MONTH_NAMES[d.month - 1]} {d.day}"


def _normalize_dates(t: str, today: date, current_depart: str | None) -> str:
    """Rewrite the other ways people give dates ("next friday", "12 dec", 2026-12-08) as "dec 12", which `extract` reads."""

    def iso(m: re.Match) -> str:
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return m.group(0)
        # The rewrite drops the year, so leave dates it would move to a different year.
        return _month_day(d) if today <= d <= today + timedelta(days=B.MAX_DAYS_AHEAD) else m.group(0)

    def day_first(m: re.Match) -> str:
        if re.search(rf"{MONTH_RE}\s*$", t_so_far[: m.start()]):  # "nov 30 dec 2" is already month first
            return m.group(0)
        return f"{m.group(2)} {m.group(1)}"

    def day_range_first(m: re.Match) -> str:
        if re.search(rf"{MONTH_RE}\s*$", t_so_far[: m.start()]):  # "dec 12 to 15 jan" is already month first
            return m.group(0)
        return f"{m.group(3)} {m.group(1)} to {m.group(2)}"

    t = re.sub(r"\b(20\d{2})-(\d{2})-(\d{2})\b", iso, t)
    t_so_far = t
    t = re.sub(rf"\b{DAY_RE}\s*(?:-|–|—|to|through|thru|until|till)\s*{DAY_RE}\s+(?:of\s+)?{MONTH_RE}(?![a-z])", day_range_first, t)
    t_so_far = t
    t = re.sub(rf"\b{DAY_RE}\s+(?:of\s+)?{MONTH_RE}(?![a-z])", day_first, t)
    # A weekday next to an explicit date only repeats it ("friday, dec 11"), and the date is what counts.
    weekdays = "|".join(WEEKDAYS)
    t = re.sub(rf"\b(?:(?:this|next|coming)\s+)?(?:{weekdays}),?\s+(?={MONTH_RE}\s+{DAY_RE}\b|\d{{1,2}}/\d{{1,2}}\b)", "", t)
    t = re.sub(rf"\b({MONTH_RE}\s+{DAY_RE}),?\s+(?:{weekdays})\b", r"\1", t)
    t = re.sub(r"\b(to|through|thru|until|till)\s+the\s+(\d)", r"\1 \2", t)
    t = re.sub(r"\bday after tomorrow\b", _month_day(today + timedelta(days=2)), t)
    t = re.sub(r"\btomorrow\b", _month_day(today + timedelta(days=1)), t)
    t = re.sub(r"\b(leav\w*|fly\w*|go\w*|depart\w*)\s+today\b", lambda m: f"{m.group(1)} {_month_day(today)}", t)
    t = re.sub(r"\b(this|next)\s+weekend\b", r"\1 friday to sunday", t)

    first = re.search(rf"{MONTH_RE}\s+{DAY_RE}", t)
    explicit = _year_for(MONTHS[first.group(1)[:3]], int(first.group(2)), today) if first else None
    anchor: list[date | None] = [None]

    def weekday(m: re.Match) -> str:
        target = WEEKDAYS.index(m.group(2))
        if m.group(1) == "next":
            d = today + timedelta(days=7 - today.weekday() + target)
        else:
            base = anchor[0]
            if base is None and re.search(r"(back|return\w*|home|until|till|to|through|thru)\s+(?:on\s+)?$", t[: m.start()]):
                base = explicit or (date.fromisoformat(current_depart) if current_depart else None)
            base = base or today
            d = base + timedelta(days=(target - base.weekday()) % 7 or 7)
        anchor[0] = d
        return _month_day(d)

    return re.sub(rf"\b(?:(this|next|on|coming)\s+)?({'|'.join(WEEKDAYS)})\b", weekday, t)


def _is_reversed(text: str, spans: list[tuple[int, int, geo.Place]]) -> bool:
    """True if the traveler is starting in Chicago or heading to New York, which we can't plan yet."""
    for start, _, place in spans:
        before = text[:start].lower()
        if place.metro == "CHI":
            m = re.search(r"(\w+\s+)?\bfrom\s+(?:the\s+)?$", before)
            if m and (m.group(1) or "").strip() not in RETURN_WORDS:
                return True
        elif re.search(r"\b(?:fly|flying|go|going|head|headed|heading|travel\w*)\s+to\s+$", before):
            return True
    for (a_start, a_end, a), (b_start, _, b) in zip(spans, spans[1:]):
        if a.metro == "CHI" and b.metro == "NYC" and re.fullmatch(r"\s*(?:to|->|→)\s*", text[a_end:b_start].lower()):
            # "flying home from Wicker Park to Harlem" is the way back of a normal trip
            m = re.search(r"(\w+)\s+from\s+(?:the\s+)?$", text[:a_start].lower())
            if not (m and m.group(1) in RETURN_WORDS):
                return True
    return False


def extract(text: str, current: dict, today: date | None = None) -> tuple[dict, dict]:
    """Return (fields, signals) found in a message. Signals are non-brief hints (group trip, unsupported city…)."""
    today = today or clock.today()
    text = geo.straighten_quotes(text)
    t = _normalize_dates(text.lower(), today, current["depart_date"]["value"])
    fields: dict = {}
    signals: dict = {}

    # Places: NYC mentions are the origin, Chicago mentions the destination.
    spans = geo.find_place_spans(text)
    if _is_reversed(text, spans):
        signals["reversed"] = True
    else:
        found = [p for _, _, p in spans]
        for metro, key in (("NYC", "origin"), ("CHI", "destination")):
            places = [p for p in found if p.metro == metro]
            if places:
                specific = [p for p in places if not p.generic]
                choice = (specific or places)[0]
                fields[key] = choice.key
                if not specific:
                    signals.setdefault("generic_place", []).append(key)
    unsupported = [c for c in OTHER_CITIES if re.search(rf"\b{c}\b", t)]
    if unsupported:
        signals["unsupported_city"] = unsupported[0]

    # Dates
    m = re.search(rf"{MONTH_RE}\s+{DAY_RE}\s*(?:-|–|—|to|through|thru|until|till)\s*(?:{MONTH_RE}\s+)?{DAY_RE}", t)
    if m:
        m1 = MONTHS[m.group(1)[:3]]
        m2 = MONTHS[m.group(3)[:3]] if m.group(3) else m1
        dep = _year_for(m1, int(m.group(2)), today)
        ret = _return_on(dep, m1, m2, int(m.group(4)))
        if dep and ret:
            fields["depart_date"], fields["return_date"] = dep.isoformat(), ret.isoformat()
        else:
            signals["bad_date"] = True
    else:
        m = re.search(r"(\d{1,2})/(\d{1,2})(?:/\d{2,4})?\s*(?:-|–|to|through)\s*(\d{1,2})/(\d{1,2})", t)
        if m:
            m1, d1, m2, d2 = (int(g) for g in m.groups())
            dep = _year_for(m1, d1, today)
            ret = _return_on(dep, m1, m2, d2)
            if dep and ret:
                fields["depart_date"], fields["return_date"] = dep.isoformat(), ret.isoformat()
            elif _could_be_date(m1, d1) or _could_be_date(m2, d2):
                signals["bad_date"] = True
        else:
            singles: list[tuple[int, date]] = []
            for mm in re.finditer(rf"{MONTH_RE}\s+{DAY_RE}\b", t):
                d = _year_for(MONTHS[mm.group(1)[:3]], int(mm.group(2)), today)
                if d:
                    singles.append((mm.start(), d))
                else:
                    signals["bad_date"] = True
            for mm in re.finditer(r"\b(\d{1,2})/(\d{1,2})\b", t):
                month, day = int(mm.group(1)), int(mm.group(2))
                d = _year_for(month, day, today)
                if d:
                    singles.append((mm.start(), d))
                elif _could_be_date(month, day):
                    signals["bad_date"] = True
            for pos, d in sorted(singles):
                before = t[max(0, pos - 25):pos]
                is_return = bool(re.search(r"back|return|home|fly home|until|till", before))
                if is_return or ("depart_date" in fields) or (current["depart_date"]["value"] and not current["return_date"]["value"] and not re.search(r"leav|depart|go\b|fly out", before)):
                    fields.setdefault("return_date", d.isoformat())
                else:
                    fields["depart_date"] = d.isoformat()

    n = _nights_in(t)
    if n is not None and "return_date" not in fields:
        dep = fields.get("depart_date") or current["depart_date"]["value"]
        if n > B.MAX_DAYS_AHEAD:
            signals["bad_date"] = True
        elif dep:
            fields["return_date"] = (date.fromisoformat(dep) + timedelta(days=n)).isoformat()
        else:
            signals["nights"] = n

    # Budget
    m = re.search(rf"(?:under|below|max(?:imum)?|budget(?:\s+(?:of|is))?|less than|up to|no more than|spend)\s*(?:about|around)?\s*\$?\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?\b{NOT_MONEY}", t)
    if not m and not re.search(r"\blast time\b", t):
        # A bare dollar amount is a budget, unless it's describing what something cost.
        m = re.search(r"(?<!\bwas )(?<!\bwere )(?<!\bcost )(?<!\bcosts )(?<!\bpaid )\$\s*(\d[\d,]*(?:\.\d+)?)\s*(k)?", t)
    if m:
        amount = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
        fields["budget"] = amount

    # Bags
    if re.search(r"backpack only|personal item only|just a backpack|no carry[- ]?on|only a backpack", t):
        fields["carry_on"], fields["checked_bags"] = 0, 0
    elif re.search(r"carry[- ]?on", t):  # "no bags to check, just a carry-on" still brings the carry-on
        fields["carry_on"] = 1
    elif re.search(r"\bno (?:bags?|luggage)\b(?!\s+(?:to\s+check|fees?))", t):
        fields["carry_on"], fields["checked_bags"] = 0, 0
    m = (
        re.search(r"\b(\d|a|an|one|two|no|zero)\s+checked(?:\s+bags?)?", t)
        or re.search(r"\bcheck(?:ing)?\s+(a|an|one|two|\d)\s+bags?", t)
        or re.search(r"\b(\d|a|an|one|two|no|zero)\s+(?:(?:big|large)\s+)?suitcases?\b", t)
    )
    if m:
        fields["checked_bags"] = _num(m.group(1))
    elif re.search(r"carry[- ]?on only|only (?:a |one )?carry[- ]?on|just (?:a |one )?carry[- ]?on|no checked|\bno (?:bags?|luggage)\s+to\s+check", t):
        fields["checked_bags"] = 0

    # Priority
    if re.search(r"\b(cheapest|cheaper|lowest|cheap|save money|spend less|less money|tight budget|as little as possible)\b", t):
        fields["priority"] = "cheapest"
    elif re.search(r"\b(easiest|easy|least hassle|no hassle|convenient|comfortable|fastest|quickest|less travel)\b", t):
        fields["priority"] = "easiest"
    elif re.search(r"\bbalance", t):
        fields["priority"] = "balanced"

    # Landing time
    if re.search(r"(?:land|arrive|get (?:there|in)|be (?:there|in chicago))\w*\s+(?:in chicago\s+)?(?:before|by)\s+noon", t):
        fields["arrive_by"] = "12:00"
    else:
        m = re.search(r"(?:land|arrive|get (?:there|in)|be (?:there|in chicago))\w*\s+(?:in chicago\s+)?(?:before|by)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?", t)
        if m:
            h, mins, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
            if ampm == "pm" and h < 12:
                h += 12
            elif ampm == "am" and h == 12:
                h = 0
            elif not ampm and h < 8:
                h += 12
            fields["arrive_by"] = f"{h:02d}:{mins:02d}"

    # Driving
    if re.search(r"\b(i'?ll drive|drive to the airport|drive myself|my car|park at the airport|drive and park)\b", t):
        fields["will_drive"] = True
    elif re.search(r"\b(no car|don'?t have a car|don'?t drive)\b", t):
        fields["will_drive"] = False

    if re.search(r"\b(kids?|family|my (?:wife|husband|partner|girlfriend|boyfriend)|friends|two of us|three of us|\d+\s+(?:people|adults|travelers))\b", t):
        signals["group"] = True
    return fields, signals


def _is_question(text: str) -> bool:
    """A question asking for information. "Can we do Dec 10 instead?" is a request to change the trip, so it doesn't count."""
    t = text.strip().lower()
    if re.match(r"(can|could|would|will|what if|what about|how about)\b", t):
        return False
    if re.match(r"(why|how|what|which|is|are|should|explain|tell me)\b", t):
        return True
    return t.endswith("?") and bool(re.search(r"\b(plans?|flights?|airports?)\b", t))


def _wants_plans(text: str) -> bool:
    return bool(re.search(r"\b(compare|show (?:me )?(?:the )?plans|find (?:me )?plans|search|see (?:the )?plans|let'?s go(?!\s+to\b)|go ahead)\b", text.lower()))


def _money(x: float) -> str:
    return f"${x:,.0f}"


def explain_plans(summary: dict, text: str) -> str:
    reply = _explain_plans(summary, text)
    if summary.get("plans_are_current") is False:
        return f"Your brief changed since these plans were priced, so these numbers may be out of date. {reply}"
    return reply


def _explain_plans(summary: dict, text: str) -> str:
    plans = {p["id"]: p for p in summary["plans"]}
    asked = re.search(r"\bplan\s+([abc])\b", text.lower())
    comp = summary.get("comparison") or {}
    if asked and asked.group(1).upper() in plans:
        p = plans[asked.group(1).upper()]
        rides = "; ".join(f"{it['label']} {_money(it['likely'])}" for it in p["items"] if it["kind"] == "estimate")
        return (
            f"Plan {p['id']} ({p['title']}) totals about {_money(p['totals']['likely'])} "
            f"(likely {_money(p['totals']['low'])}–{_money(p['totals']['high'])}): {_money(p['totals']['bookings'])} for flights and bags, "
            f"{_money(p['totals']['getting_around'])} getting around. {p['convenience']['summary']}"
            + (f" Estimated rides: {rides}." if rides else "")
        )
    rec = plans.get(comp.get("recommended", ""))
    if not rec:
        return "I don't have plans to explain yet. Press Compare complete plans first."
    return (
        f"{comp.get('headline', '')} {comp.get('detail', '')} "
        f"I'd pick Plan {rec['id']} ({rec['title']}) at about {_money(rec['totals']['likely'])} door to door. "
        + (
            "Rides are estimates with a likely range. Flight prices are demo fares, not live quotes."
            if summary.get("data_mode") == "demo"
            else "Rides are estimates with a likely range; flights and bag fees are quotes."
        )
    ).strip()


class OfflineAgent:
    mode = "offline"

    def respond(self, box: ToolBox, history: list, text: str) -> dict:
        text = geo.straighten_quotes(text)
        current = current_brief(box.session, box.trip)
        fields, signals = extract(text, current)
        asking = _is_question(text)
        if asking and box.state()["plans_searched"]:
            # Words in a question ("why is the cheapest plan...") describe the plans, not the trip.
            fields, signals = {}, {}
        elif asking:
            fields.pop("priority", None)
        if "depart_date" in fields and "return_date" not in fields and not current["return_date"]["value"]:
            nights = _earlier_nights(history)
            if nights is not None and nights <= B.MAX_DAYS_AHEAD:
                fields["return_date"] = (date.fromisoformat(fields["depart_date"]) + timedelta(days=nights)).isoformat()
        generic = set(signals.get("generic_place", []))
        stated = {k: v for k, v in fields.items() if k not in generic}
        assumed = {k: v for k, v in fields.items() if k in generic}
        result = box.tool_update_brief(stated=stated or None, assumed=assumed or None) if fields else box.state() | {"errors": []}

        state = box.state()
        bad_date = signals.get("bad_date")
        reversed_trip = signals.get("reversed")
        if not fields and not bad_date and not reversed_trip and state["plans_searched"] and (asking or not _wants_plans(text)):
            return {"reply": explain_plans(box.tool_get_plans(), text)}
        if _wants_plans(text) and state["ready_to_search"] and not bad_date:
            res = box.tool_search_plans()
            if "error" in res:
                return {"reply": res["error"]}
            if not res["plans"]:
                return {"reply": " ".join(res["notes"]) or "I couldn't find any complete plans for these dates. Try different dates in your brief."}
            return {"reply": f"Done. I compared {len(res['plans'])} complete plans door to door. " + explain_plans(res, "")}

        parts: list[str] = []
        if reversed_trip:
            parts.append(
                "For now TotalTrip only plans trips starting in the New York City area and going to Chicago, "
                "so I can't plan one the other way. If you're leaving from New York, tell me your neighborhood and where in Chicago you're headed."
            )
        if bad_date:
            parts.append("I can't find that date on the calendar. What date did you mean?")
        if signals.get("unsupported_city"):
            parts.append(f"TotalTrip starts with trips from New York City to Chicago, so I can't plan {signals['unsupported_city'].title()} yet.")
        if signals.get("group"):
            parts.append("Heads up: this first version prices trips for one traveler, so the totals are per person.")
        parts += result.get("errors", [])

        brief = state["brief"]
        understood = []
        if "origin" in fields or "destination" in fields:
            if brief["origin"]["value"] and brief["destination"]["value"]:
                understood.append(f"{brief['origin']['display']} to {brief['destination']['display']}")
        if "depart_date" in fields or "return_date" in fields:
            if brief["depart_date"]["value"] and brief["return_date"]["value"]:
                understood.append(f"{brief['depart_date']['display']} to {brief['return_date']['display']}")
        if "budget" in fields and brief["budget"]["value"]:
            understood.append(f"a budget of {_money(brief['budget']['value'])} for flights, bags and every ride")
        if understood:
            parts.insert(0, "Got it: " + "; ".join(understood) + ".")
        elif fields and not result.get("errors"):
            parts.insert(0, "Got it, I updated your trip brief.")

        for key in generic:
            parts.append(f"I assumed {brief[key]['display']}. Tell me the neighborhood if it's somewhere else; it changes which airport is cheapest.")

        missing = state["missing_required"]
        if missing:
            if signals.get("nights") and missing[0] == "depart_date":
                parts.append("What day do you want to leave? I'll count the nights from there.")
            else:
                parts.append(QUESTIONS[missing[0]])
        else:
            assumed_fields = [n for n in ("carry_on", "checked_bags", "priority") if brief[n]["provenance"] == "assumed"]
            if assumed_fields:
                parts.append("I assumed " + ", ".join(brief[n]["display"].lower() for n in assumed_fields) + ". Fix anything that's wrong in your brief.")
            if not brief["budget"]["value"] and not any("budget" in (m.content or "") for m in history if m.role == "assistant"):
                parts.append("Do you have a budget for flights, bags and all rides? It's optional.")
            parts.append("When you're ready, press Compare complete plans.")
        if not parts:
            parts.append("Tell me where you're leaving from, where in Chicago you're going, and your dates.")
        return {"reply": " ".join(parts)}


def _earlier_nights(history: list) -> int | None:
    """Trip length the traveler gave before we knew their departure date."""
    for message in reversed(history):
        if message.role == "user":
            n = _nights_in(geo.straighten_quotes(message.content).lower())
            if n is not None:
                return n
    return None
