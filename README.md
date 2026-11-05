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

On demo fares, the plans page runs a concept test instead of sending people to airline sites: testers pick "I'd book this" (or none of the plans), then answer whether the door-to-door cost would change which flight they book. A result row shows the plan the tester acted on last, with their answer only if it was about that plan. Answers about other plans stay in the `events` table.

Flights use mock data for now (`backend/app/data/fixture_routes.json`). Airlines, schedules and fares there are illustrative. The homepage example and the plans page footer label them as demo fares. There's no live flight provider yet.

## Before the pilot (not done yet)

1. **Pick and test an OpenAI model.** Set `OPENAI_MODEL`, then test the conversation prompt (`app/ai/agent.py`) with real traveler phrasing.
2. **Run the deck's validation tests:**
   - 20 real trips: do most save $100 or more?
   - 15 travelers: do 6 or more click to book? Track this in `/api/admin/metrics`.
3. **Production basics:** accounts and auth, DB migrations (Alembic), streaming chat responses, deploy (e.g. Vercel + Render/Fly).
