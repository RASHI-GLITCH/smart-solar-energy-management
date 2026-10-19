"""
The agent's toolbox.

Design rule that makes the answers trustworthy: the LLM chooses WHICH tool to
call and narrates the result, but every number is computed here, in Python.
The model never does arithmetic. Each tool returns the value together with the
formula and the substitution, so the agent can show its working honestly.
"""
import csv
import json
from datetime import datetime
from typing import Dict, List

from . import analytics, config as C, graph_store, policy
from . import formulas as F
from .datasource import get_latest, push_command, source_info


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
def get_latest_sensor_data() -> Dict:
    r = get_latest()
    if not r.get("ok"):
        return r
    derived = analytics.derived_state(r)
    return {
        "reading": {k: r.get(k) for k in
                    ("timestamp", "voltage", "current", "power", "temperature",
                     "light", "battery_v", "load_state", "load_power")},
        "freshness": {"age_seconds": r.get("age_seconds"), "stale": r.get("stale"),
                      "source": r.get("source")},
        "derived": derived["values"],
        "formulas": derived["formulas"],
        "table": {
            "title": "Current system state",
            "columns": ["Quantity", "Value", "Unit"],
            "rows": [
                ["Panel voltage", _r(r.get("voltage")), "V"],
                ["Panel current", _r(r.get("current"), 3), "A"],
                ["PV power", _r(r.get("power")), "W"],
                ["Load power", _r(r.get("load_power")), "W"],
                ["Battery voltage", _r(r.get("battery_v"), 3), "V"],
                ["Battery SOC", _r(derived["values"].get("soc"), 1), "%"],
                ["Temperature", _r(r.get("temperature"), 1), "degC"],
                ["Light (raw)", _r(r.get("light"), 0), "ADC"],
                ["Est. irradiance", _r(derived["values"].get("irradiance"), 1), "W/m^2"],
                ["Panel efficiency", _r(derived["values"].get("efficiency"), 2), "%"],
                ["Load relay", "ON" if r.get("load_state") else "OFF", ""],
            ],
        },
    }


def get_battery_status() -> Dict:
    r = get_latest()
    bv = float(r.get("battery_v") or C.BATTERY_NOMINAL_V)
    load_w = float(r.get("load_power") or 0)
    soc = F.soc_from_voltage(bv)
    usable = F.usable_battery_energy(soc.value or 0)
    auton = F.autonomy_hours(usable.value or 0, load_w)
    pv_w = float(r.get("power") or 0)
    chg_i = max(0.0, (pv_w - load_w) / C.BATTERY_NOMINAL_V)
    to_target = F.charge_time(soc.value or 0, C.SAFETY["soc_target"], chg_i)

    level = ("CRITICAL" if (soc.value or 0) < C.SAFETY["soc_critical"] else
             "LOW" if (soc.value or 0) < C.SAFETY["soc_low"] else
             "FULL" if (soc.value or 0) >= C.SAFETY["soc_full"] else "HEALTHY")
    return {
        "state_of_charge_pct": _r(soc.value, 1),
        "terminal_voltage_v": _r(bv, 3),
        "level": level,
        "usable_energy_wh": _r(usable.value, 1),
        "autonomy_hours": _r(auton.value, 2),
        "hours_to_target_soc": _r(to_target.value, 2),
        "thresholds": {k: C.SAFETY[k] for k in
                       ("soc_critical", "soc_low", "soc_target", "soc_full")},
        "table": {
            "title": "Battery status",
            "columns": ["Metric", "Value", "Unit"],
            "rows": [
                ["State of charge", _r(soc.value, 1), "%"],
                ["Terminal voltage", _r(bv, 3), "V"],
                ["Usable energy", _r(usable.value, 1), "Wh"],
                ["Autonomy at present load", _r(auton.value, 2), "h"],
                ["Time to target SOC", _r(to_target.value, 2), "h"],
                ["Nominal capacity", _r(C.BATTERY_CAPACITY_AH * C.BATTERY_NOMINAL_V, 1), "Wh"],
            ],
        },
        "formulas": [c.to_dict() for c in (soc, usable, auton, to_target)],
    }


def get_load_status() -> Dict:
    r = get_latest()
    return {
        "relay_state": "ON" if r.get("load_state") else "OFF",
        "load_power_w": _r(r.get("load_power")),
        "pv_power_w": _r(r.get("power")),
        "deficit_w": _r(float(r.get("load_power") or 0) - float(r.get("power") or 0)),
        "note": "A positive deficit means the battery or backup is covering the gap.",
    }


def get_energy_summary(period: str = "today") -> Dict:
    return analytics.energy_summary(period)


def forecast_generation(target: str = "tomorrow", date: str = None) -> Dict:
    return analytics.forecast(target=target, date=date)


def check_system_health() -> Dict:
    return analytics.health_check()


def analyse_correlations(top: int = 5, days: int = 14) -> Dict:
    return graph_store.query_strongest(top=top, days=days)


def recommend_action() -> Dict:
    r = get_latest()
    decision = policy.recommend_action(r)
    validation = policy.validate_action(decision["action"], r)
    return {"decision": decision, "validation": validation}


def set_load_state(state: int, reason: str = "") -> Dict:
    """
    Request a relay state. Goes through the safety validator first -- if it
    rejects, nothing is sent to the hardware.
    """
    r = get_latest()
    intent = "USE_BATTERY" if int(state) == 1 else "SHED_NONCRITICAL_LOAD"
    validation = policy.validate_action(intent, r)
    if validation["safety_check"] != "PASSED":
        return {"executed": False, "safety_check": "REJECTED",
                "blockers": validation["blockers"], "validation": validation,
                "note": "Command was blocked by the safety validator and never sent."}

    result = push_command(f"LOAD_{'ON' if int(state) else 'OFF'}")
    policy.note_switch(int(state))
    log_decision(action=intent, reason=reason or "manual/agent request",
                 safety="PASSED", outcome=json.dumps(result))
    return {"executed": True, "safety_check": "PASSED",
            "requested_state": "ON" if int(state) else "OFF",
            "transport": result, "validation": validation}


def send_alert(severity: str, message: str) -> Dict:
    row = {"timestamp": datetime.now().isoformat(), "severity": severity.upper(),
           "message": message}
    _append_csv(C.ALERT_LOG, row)
    return {"alert_sent": True, **row}


def log_decision(action: str, reason: str, safety: str = "", outcome: str = "") -> Dict:
    row = {"timestamp": datetime.now().isoformat(), "action": action,
           "reason": reason, "safety_check": safety, "outcome": outcome}
    _append_csv(C.DECISION_LOG, row)
    return {"logged": True, **row}


def get_decision_log(limit: int = 20) -> Dict:
    if not C.DECISION_LOG.exists():
        return {"entries": [], "note": "No decisions have been logged yet."}
    with open(C.DECISION_LOG, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))[-limit:]
    return {
        "entries": rows,
        "table": {
            "title": f"Decision log -- last {len(rows)} entries",
            "columns": ["Time", "Action", "Safety", "Reason"],
            "rows": [[r.get("timestamp", "")[:19], r.get("action", ""),
                      r.get("safety_check", ""), (r.get("reason", "") or "")[:110]]
                     for r in reversed(rows)],
        },
    }


def get_data_source_info() -> Dict:
    return source_info()


def _r(x, nd: int = 2):
    try:
        return None if x is None else round(float(x), nd)
    except (TypeError, ValueError):
        return x


def _append_csv(path, row: Dict) -> None:
    new = not path.exists()
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(row.keys()))
        if new:
            w.writeheader()
        w.writerow(row)


# ---------------------------------------------------------------------------
# Registry + OpenAI/Groq tool schemas
# ---------------------------------------------------------------------------
REGISTRY = {
    "get_latest_sensor_data": get_latest_sensor_data,
    "get_battery_status": get_battery_status,
    "get_load_status": get_load_status,
    "get_energy_summary": get_energy_summary,
    "forecast_generation": forecast_generation,
    "check_system_health": check_system_health,
    "analyse_correlations": analyse_correlations,
    "recommend_action": recommend_action,
    "set_load_state": set_load_state,
    "send_alert": send_alert,
    "log_decision": log_decision,
    "get_decision_log": get_decision_log,
    "get_data_source_info": get_data_source_info,
}

# Tools that change hardware state. Blocked unless the request explicitly allows control.
CONTROL_TOOLS = {"set_load_state"}


def _schema(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {},
                       "required": required or []}}}


SCHEMAS: List[Dict] = [
    _schema("get_latest_sensor_data",
            "Read the most recent sensor values (voltage, current, power, temperature, "
            "light, battery voltage, load state) with derived quantities such as SOC, "
            "irradiance and efficiency. Use this for any question about the state right now."),
    _schema("get_battery_status",
            "Battery state of charge, usable energy, how long it can run the present load, "
            "and how long to reach the target charge level."),
    _schema("get_load_status",
            "Current relay state, load power draw and the shortfall against solar generation."),
    _schema("get_energy_summary",
            "Total energy generated and consumed over a past period, with an hourly "
            "breakdown, cost saved and CO2 avoided. Use for 'how much did we generate/use'.",
            {"period": {"type": "string",
                        "enum": ["today", "yesterday", "hour", "week", "month", "all"],
                        "description": "Which past period to summarise."}},
            ["period"]),
    _schema("forecast_generation",
            "Predict generation and consumption for a FUTURE day from solar geometry and "
            "the recently measured performance ratio. Use for any question about tomorrow "
            "or a future date.",
            {"target": {"type": "string",
                        "enum": ["today", "tomorrow", "day_after_tomorrow"],
                        "description": "Which upcoming day to forecast."},
             "date": {"type": "string",
                      "description": "Optional explicit date, YYYY-MM-DD. Overrides target."}}),
    _schema("check_system_health",
            "Run all deterministic fault checks: value ranges, data freshness, battery "
            "thresholds and the sustained prediction residual. Use for 'is anything wrong'."),
    _schema("analyse_correlations",
            "The strongest statistical relationships between measured variables, from the "
            "Neo4j knowledge graph. Use for 'what affects what' or 'why does X change'.",
            {"top": {"type": "integer", "description": "How many relationships to return."},
             "days": {"type": "integer", "description": "Days of history to analyse."}}),
    _schema("recommend_action",
            "Run the deterministic energy policy engine and the safety validator, "
            "returning the recommended action, the rule that fired and the safety verdict."),
    _schema("set_load_state",
            "Request a relay state change. Passes through the safety validator; a rejected "
            "command is never sent to the ESP32. Only call this when explicitly asked to "
            "control the hardware.",
            {"state": {"type": "integer", "enum": [0, 1],
                       "description": "1 to switch the load on, 0 to switch it off."},
             "reason": {"type": "string", "description": "Why this change is needed."}},
            ["state", "reason"]),
    _schema("send_alert",
            "Record a fault or critical-condition alert.",
            {"severity": {"type": "string", "enum": ["INFO", "WARNING", "CRITICAL"]},
             "message": {"type": "string"}},
            ["severity", "message"]),
    _schema("get_decision_log",
            "Past decisions with their reasons, safety verdicts and outcomes. Use for "
            "'why did you do X' or 'what happened earlier'.",
            {"limit": {"type": "integer", "description": "How many recent entries."}}),
    _schema("get_data_source_info",
            "Where the data is coming from, how many samples exist and the time range "
            "covered. Use when asked about data availability or coverage."),
]
