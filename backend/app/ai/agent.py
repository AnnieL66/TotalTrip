"""Conversation agent on the OpenAI Responses API with function calling.

The model talks and decides which tool to call; every price and rule lives in backend code.
"""
from __future__ import annotations

import json
import logging

from .. import clock
from ..config import settings
from ..db import log_event
from .offline import OfflineAgent
from .tools import TOOLS, ToolBox

log = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 6

SYSTEM_PROMPT = """You are the TotalTrip planner. TotalTrip shows the real door-to-door cost of a trip
(flights, bag fees and every ride) before the traveler books, so the cheapest flight isn't mistaken
for the cheapest trip.

Today is {today}. Launch limits: one traveler, from the New York City area to Chicago, flights + bags +
local transport only. Hotels, multiple travelers, other cities, loyalty and booking are not supported
yet; say so briefly if asked.

How to work:
- Record details with update_brief as soon as the traveler gives them. Use `stated` for what they said
  and `assumed` for anything you inferred (e.g. "Chicago" alone → assume "The Loop"). Convert relative
  dates to YYYY-MM-DD using today's date.
- Ask only for details that change the plan, one or two at a time: where they leave from (neighborhood),
  where in Chicago they're going, dates, bags. Budget, landing-time limits and driving are optional.
- After updating, briefly say what you understood and what you assumed. Keep replies to 2–4 sentences,
  plain and friendly.
- Don't search until the traveler asks to see or compare plans; they can also press "Compare complete
  plans". When plans exist, use get_plans to answer questions about them.
- Never state a price, time or fee unless it came from a tool result. Say which numbers are quotes, which
  are estimates and which are demo fares (kind "demo"), which are illustrative and not real prices.
  Never call a total guaranteed.
"""


class OpenAIAgent:
    mode = "openai"

    def __init__(self) -> None:
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.openai_api_key)

    def respond(self, box: ToolBox, history: list, text: str) -> dict:
        items: list = [{"role": m.role, "content": m.content} for m in history]
        items.append({"role": "user", "content": f"{text}\n\n[Current trip state: {json.dumps(box.state())}]"})
        usage = {"input_tokens": 0, "output_tokens": 0}
        for _ in range(MAX_TOOL_ROUNDS):
            response = self.client.responses.create(
                model=settings.openai_model,
                instructions=SYSTEM_PROMPT.format(today=clock.today().isoformat()),
                input=items,
                tools=TOOLS,
            )
            if response.usage:
                usage["input_tokens"] += response.usage.input_tokens
                usage["output_tokens"] += response.usage.output_tokens
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                log_event(box.session, "llm_usage", box.trip.id, model=settings.openai_model, **usage)
                return {"reply": response.output_text}
            items += response.output
            for call in calls:
                try:
                    result = box.execute(call.name, json.loads(call.arguments or "{}"))
                except Exception as exc:  # report tool failures back to the model instead of crashing the turn
                    log.exception("tool %s failed", call.name)
                    result = {"error": f"Tool failed: {exc}"}
                items.append({"type": "function_call_output", "call_id": call.call_id, "output": json.dumps(result, default=str)})
        log_event(box.session, "llm_usage", box.trip.id, model=settings.openai_model, **usage)
        return {"reply": "Sorry, I got stuck working that out. Could you rephrase?"}


def get_agent():
    return OpenAIAgent() if settings.assistant_mode == "openai" else OfflineAgent()


def respond(box: ToolBox, history: list, text: str) -> dict:
    agent = get_agent()
    try:
        out = agent.respond(box, history, text)
    except Exception as exc:
        if agent.mode != "openai":
            raise
        # The planner keeps working even if the LLM is down; the brief UI still works too.
        log.exception("OpenAI agent failed; falling back to offline assistant")
        log_event(box.session, "llm_error", box.trip.id, error=str(exc)[:300])
        out = OfflineAgent().respond(box, history, text)
        agent = OfflineAgent()
    return {**out, "assistant_mode": agent.mode, "brief_changed": box.brief_changed, "plans_searched": box.plans_searched}
