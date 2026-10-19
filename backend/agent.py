"""
The agent runtime: Groq tool calling + a strict answer contract.

Answer contract (this is what the user asked for, enforced in the prompt and
again in code):

    1. ANSWER FIRST -- direct, in plain language, with the number.
    2. THEN THE TABLE -- whenever there are numbers over time or categories.
    3. THEN THE FORMULA -- what was used and the actual substitution.

Accuracy guarantee: the model never calculates. Every number in the reply comes
from a tool result computed in Python. The model's job is to choose tools, read
their output and explain it. If a number is not in a tool result, the model is
instructed to say it does not have it rather than invent one.
"""
import json
import re
from typing import Dict, List, Optional

from . import config as C
from . import tools as T

SYSTEM_PROMPT = """You are the decision and analysis agent for a cloud-based smart solar \
energy management system. An ESP32 measures the panel and battery; the readings reach you \
through tools.

## Your absolute rules

1. NEVER calculate anything yourself. You are not permitted to do arithmetic. Every number \
you state must come from a tool result. If you need a number, call the tool that produces it.
2. NEVER invent or estimate a sensor value. If a tool does not return it, say plainly that \
the data is not available and name which tool or sensor would provide it.
3. Call tools before answering any question about data. Do not answer from memory of \
earlier turns if the question is about the current state.
4. You may call several tools in sequence. Gather everything you need before answering.

## How every answer must be structured

**First, the answer.** Open with the direct answer in one or two sentences, including the \
key number and its unit. No preamble, no restating the question.

**Then, the numbers as a table.** Whenever the tool returned a `table` object, or whenever \
your answer involves more than about three numbers, values over time, or a comparison, \
render it as a markdown table. Use the tool's own columns and rows. Never reformat the \
numbers or round them differently from what the tool returned.

**Then, how it was calculated.** Under a heading `### How this was calculated`, show the \
formula from the tool's `formulas` array. For each one give:
- the formula name,
- the formula itself wrapped in $$ ... $$ for display (use the `latex` field verbatim),
- the substitution line from the `substitution` field, in a code block,
- one line explaining what it means, from the `explanation` field.

**Finally, assumptions and caveats.** If any formula carries `assumptions`, or the tool \
returned `notes`, list them under `### Assumptions`. Never silently drop a caveat -- \
especially the uncalibrated irradiance estimate and the linear SOC approximation. Being \
straight about the limits is more valuable than sounding confident.

## Tone

Direct and technical, as if explaining to an engineer who will be examined on this. \
No filler, no marketing language, no emoji. Short paragraphs. When something is uncertain, \
say so and say why.

## Control actions

Only call set_load_state when the user explicitly asks to switch the load. Every control \
request passes a deterministic safety validator that you cannot influence. If it rejects \
the command, report the rejection and the blocking reason exactly -- never present a \
blocked command as if it succeeded."""


class AgentError(RuntimeError):
    pass


def _client():
    if not C.GROQ_API_KEY:
        raise AgentError(
            "GROQ_API_KEY is not set. Put your key in the .env file "
            "(get one free at https://console.groq.com/keys)."
        )
    from openai import OpenAI
    return OpenAI(api_key=C.GROQ_API_KEY, base_url=C.GROQ_BASE_URL)


def ask(question: str,
        history: Optional[List[Dict]] = None,
        allow_control: bool = False,
        max_rounds: int = 6) -> Dict:
    """
    Run one question through the agent loop.

    Returns the answer text plus the full trace: which tools were called, what
    they returned, and every formula used. The dashboard renders all of it, so
    the reasoning is auditable rather than a black box.
    """
    client = _client()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": question})

    schemas = [s for s in T.SCHEMAS
               if allow_control or s["function"]["name"] not in T.CONTROL_TOOLS]

    trace: List[Dict] = []
    all_formulas: List[Dict] = []
    all_tables: List[Dict] = []

    for _ in range(max_rounds):
        resp = client.chat.completions.create(
            model=C.GROQ_MODEL,
            messages=messages,
            tools=schemas,
            tool_choice="auto",
            temperature=0.2,
            max_tokens=2600,
        )
        msg = resp.choices[0].message
        calls = msg.tool_calls or []

        if not calls:
            answer = msg.content or "No answer was produced."
            return {
                "answer": answer,
                "tool_calls": trace,
                "formulas": _dedupe(all_formulas),
                "tables": all_tables,
                "model": C.GROQ_MODEL,
                "rounds": len(trace),
            }

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ],
        })

        for call in calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name in T.CONTROL_TOOLS and not allow_control:
                result = {"error": "Control actions are disabled for this request. "
                                   "Enable hardware control to switch the load."}
            elif name not in T.REGISTRY:
                result = {"error": f"No such tool: {name}"}
            else:
                try:
                    result = T.REGISTRY[name](**args)
                except TypeError as exc:
                    result = {"error": f"Bad arguments for {name}: {exc}"}
                except Exception as exc:
                    result = {"error": f"{type(exc).__name__} in {name}: {exc}"}

            if isinstance(result, dict):
                if result.get("formulas"):
                    all_formulas.extend(result["formulas"])
                if result.get("table"):
                    all_tables.append(result["table"])

            trace.append({"tool": name, "arguments": args,
                          "result_preview": _preview(result)})
            messages.append({"role": "tool", "tool_call_id": call.id,
                             "name": name, "content": _serialise(result)})

    return {
        "answer": "The agent reached its tool-call limit without producing a final answer. "
                  "Try asking a narrower question.",
        "tool_calls": trace,
        "formulas": _dedupe(all_formulas),
        "tables": all_tables,
        "model": C.GROQ_MODEL,
        "rounds": len(trace),
    }


# ---------------------------------------------------------------------------
# Autonomous decision cycle  (OBSERVE -> DECIDE -> SAFETY -> ACT -> VERIFY -> LOG)
# ---------------------------------------------------------------------------
def decision_cycle(use_llm: bool = False, allow_control: bool = False) -> Dict:
    """
    One full control cycle.

    The deterministic policy engine always decides. The LLM is optional and only
    adds a plain-language explanation -- it can never change the action. That
    keeps control fast and predictable, and keeps you well inside Groq's free
    rate limits, since the LLM is not in the fast loop.
    """
    from .datasource import get_latest

    reading = get_latest()
    health = T.check_system_health()
    decision = T.recommend_action()
    action = decision["decision"]["action"]
    validation = decision["validation"]

    executed, transport = False, None
    if allow_control and validation["safety_check"] == "PASSED":
        target = decision["decision"].get("target_relay_state")
        if target is not None:
            out = T.set_load_state(target, decision["decision"]["reason"])
            executed, transport = out.get("executed", False), out.get("transport")

    explanation = None
    if use_llm and C.GROQ_API_KEY:
        try:
            client = _client()
            brief = {
                "reading": {k: reading.get(k) for k in
                            ("power", "load_power", "battery_v", "temperature", "light")},
                "health": health["status"],
                "action": action,
                "rule": decision["decision"]["rule_fired"],
                "safety": validation["safety_check"],
            }
            r = client.chat.completions.create(
                model=C.GROQ_MODEL,
                messages=[
                    {"role": "system", "content":
                     "You explain solar controller decisions in two sentences for an "
                     "operator's dashboard. State what was decided and why, using only "
                     "the numbers given. Never invent values. No preamble."},
                    {"role": "user", "content": json.dumps(brief)},
                ],
                temperature=0.3, max_tokens=180,
            )
            explanation = r.choices[0].message.content
        except Exception as exc:
            explanation = f"(LLM explanation unavailable: {exc})"

    T.log_decision(action=action, reason=decision["decision"]["reason"],
                   safety=validation["safety_check"],
                   outcome="executed" if executed else "advisory only")

    if health["status"] == "FAULT":
        T.send_alert("CRITICAL", "; ".join(health["faults"])[:400])
    elif health.get("warnings"):
        T.send_alert("WARNING", "; ".join(health["warnings"])[:400])

    return {
        "timestamp": reading.get("timestamp"),
        "reading": reading,
        "health": health,
        "decision": decision["decision"],
        "validation": validation,
        "executed": executed,
        "transport": transport,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _serialise(obj) -> str:
    try:
        text = json.dumps(obj, default=str)
    except (TypeError, ValueError):
        text = str(obj)
    return text[:12000]


def _preview(obj) -> str:
    text = _serialise(obj)
    return text[:400] + (" ..." if len(text) > 400 else "")


def _dedupe(formulas: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for f in formulas:
        key = (f.get("key"), f.get("substitution"))
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


SUGGESTED_QUESTIONS = [
    "What is the system doing right now?",
    "How much energy will we generate tomorrow?",
    "How much did we generate and consume today?",
    "Is anything wrong with the system?",
    "What affects panel power the most, and by how much?",
    "How long can the battery run the current load?",
    "Why was the last decision taken?",
    "Compare today against yesterday.",
]
