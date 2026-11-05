# TotalTrip: starting product

Plan the whole trip. See the whole cost. TotalTrip compares complete **door-to-door** trip options (flights, bag fees and every ride to and from the airport) before you book.

This first release follows the non-Future slides of *Maker Day #2, Team #111*:

| Deck slide | Built here |
|---|---|
| Start with a conversation | `/`: Discover page, free-text trip box, starter chips, "cheapest flight isn't the cheapest trip" explainer |
| The AI builds your trip brief | `/trip/[id]`: chat + live brief with **You said / I assumed** tags, inline editing, "Still needed", Compare button |
| Compare complete plans, door to door | `/trip/[id]/plans`: 2–3 plans with totals, likely range, **Quote / Estimate** labels, convenience, budget fit, comparison line, recheck, tracked click to the airline site |
| Starting product | One traveler · NYC area → Chicago · flights + bags + local transport |

Also included for the pilot: price-error reports, a post-trip "what did you actually spend" form (`/trip/[id]/after`), event tracking and `/api/admin/metrics` (click-to-book rate, LLM tokens, estimate vs. actual).

**Deferred (marked Future in the deck):** step-by-step map, refine-by-talking with locks, memberships/loyalty, review-before-booking recheck flow, hotels, multiple travelers, other cities.

## Run it

```bash
# Backend (FastAPI), port 8000
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000

# Frontend (Next.js), port 3000; proxies /api to the backend
cd frontend
npm install
npm run dev
```

Open http://localhost:3000. Tests: `cd backend && .venv/bin/python -m pytest`.

With no configuration the app runs fully offline: SQLite, an in-memory cache, **demo fares**, distance-based drive times and a rule-based assistant. Add keys in `.env` (copy `.env.example`) to switch each piece on:

| Env var | Turns on |
|---|---|
| `OPENAI_API_KEY` (+ `OPENAI_MODEL`) | LLM conversation via the Responses API with function calling |
| `DATABASE_URL` | PostgreSQL (`docker compose up -d` starts one) |
| `REDIS_URL` | Shared cache and rate limits |
| `GOOGLE_MAPS_API_KEY` | Traffic-aware drive times (Google Routes) |

For demos and concept testing, the homepage has one-click sample trips (`backend/app/data/demo_trips.json`). The mock fares are set so these trips show a clear door-to-door difference, so their results illustrate the idea and aren't evidence of real savings. If you change fares, `tests/test_demo_trips.py` checks that each trip still tells its story.

Flights use mock data for now (`backend/app/data/fixture_routes.json`). Airlines, schedules and fares there are illustrative and are labeled as demo fares throughout the app. The Amadeus adapter is kept for reference, but Amadeus closed its self-service API in July 2026, so leave `AMADEUS_*` unset.

## How it works

```
Next.js ──/api──► FastAPI ──► Trip brief (versioned, with provenance)   ──► PostgreSQL / SQLite
                     │      ──► Assistant: OpenAI tools  |  offline rules
                     │      ──► Cost engine (deterministic Python)
                     │             ├─ Flight adapter: Amadeus | demo fixtures   (cached)
                     │             ├─ Routing adapter: Google Routes | estimate
                     │             └─ Tables: bag fees, ride rate cards, transit fares, parking
                     └─► Events, price reports, spend reports
```

- **The AI never produces a price.** The assistant can only call `update_brief`, `get_trip_state`, `search_plans` and `get_plans`. Every number comes from `backend/app/engine/`.
- **Every number is labeled.** Mock flight prices are *demo* fares. Bag fees and transit fares are *quotes* from reference tables. Rides and parking are *estimates* with a low–likely–high range. Every number carries its source and an as-of date.
- **Engine** (`engine/plans.py`): for each NYC airport × Chicago airport pair, take the cheapest offers each way, price four ground legs per trip (rideshare and transit when it's running; a traveler who chose to drive always drives and parks at home), and build candidates with three ground strategies. It then applies the landing-time constraint (relaxed with a visible note if nothing fits) and scores candidates as total + time value + transfer, early-start and over-budget penalties, weighted by priority. It picks *Lowest booking price*, *Best fit*, *Lowest total cost* and *Least travel time*, merges duplicates, and shows at most 3.
- **Basic economy** fares that don't include a carry-on are priced as if the bag is checked, with a note.

## Before the pilot (not done yet)

1. **Verify the reference data.** `backend/app/data/*.json` holds illustrative bag fees, ride rate cards, transit fares and parking rates (as of 2026-10-01). Check each against official sources and keep the `as_of` dates current.
2. **Choose a live flight provider, when mock data is no longer enough.** The deck's validation tests below need real prices. The Amadeus adapter can't be used (self-service API closed July 2026), and the SerpApi adapter the plan named as a fallback isn't built.
3. **Pick and test an OpenAI model.** Set `OPENAI_MODEL`, then test the conversation prompt (`app/ai/agent.py`) with real traveler phrasing.
4. **Run the deck's validation tests:**
   - 20 real trips: do most save $100 or more?
   - 15 travelers: do 6 or more click to book? Track this in `/api/admin/metrics`.
5. **Production basics:** accounts and auth, protect `/api/admin/*`, DB migrations (Alembic), streaming chat responses, deploy (e.g. Vercel + Render/Fly).
