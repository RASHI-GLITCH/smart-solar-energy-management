"""
Deterministic energy policy + safety validator.

Two separate jobs, deliberately kept apart:

  recommend_action()  decides what SHOULD happen, using the rules in section 7
                      of the project document. Pure if/else -- no LLM.

  validate_action()   decides what is ALLOWED to happen. This is the hard gate.
                      It runs after the LLM, and the LLM cannot widen it.

If the validator rejects, the command never reaches the ESP32.
"""
import time
from typing import Dict, Optional

from . import config as C
from . import formulas as F
from .analytics import health_check

_last_switch = {"at": 0.0, "state": None}


# ---------------------------------------------------------------------------
# Policy engine  (project document, section 7)
# ---------------------------------------------------------------------------
def recommend_action(reading: Dict, predicted_gen_w: Optional[float] = None) -> Dict:
    """The deterministic decision. Runs on every tick, no LLM involved."""
    health = health_check(reading)

    if health["status"] == "FAULT":
        return _d("SAFE_STATE",
                  "Health checks failed, so the system must not optimise: "
                  + " ".join(health["faults"]),
                  "fault", health)

    if reading.get("stale"):
        return _d("SAFE_STATE",
                  f"Data is {reading.get('age_seconds', 0):.0f} s old. The policy never "
                  "guesses from stale readings; it holds the current safe state.",
                  "stale_data", health)

    pv_w = float(reading.get("power") or 0)
    load_w = float(reading.get("load_power") or 0)
    batt_v = float(reading.get("battery_v") or C.BATTERY_NOMINAL_V)
    soc = F.soc_from_voltage(batt_v).value or 0
    s = C.SAFETY

    high_solar = pv_w > max(5.0, load_w * 1.2)
    surplus_w = pv_w - load_w

    if soc < s["soc_critical"]:
        if pv_w > 1.0:
            return _d("CHARGE_BATTERY",
                      f"Battery is critically low at {soc:.1f} % (below the "
                      f"{s['soc_critical']:.0f} % floor) but {pv_w:.1f} W of solar is "
                      f"available, so all generation goes to charging and non-critical "
                      f"load is shed.", "critical_soc_charge", health, soc, pv_w, load_w)
        return _d("SHED_NONCRITICAL_LOAD",
                  f"Battery at {soc:.1f} % is below the {s['soc_critical']:.0f} % floor "
                  f"and there is no solar input ({pv_w:.1f} W). Non-critical load is shed "
                  f"to protect the battery from deep discharge.",
                  "critical_soc_shed", health, soc, pv_w, load_w)

    if high_solar and soc < s["soc_target"]:
        return _d("CHARGE_BATTERY",
                  f"Solar generation is {pv_w:.1f} W against a {load_w:.1f} W load, and "
                  f"the battery is at {soc:.1f} %, below the {s['soc_target']:.0f} % target. "
                  f"The {surplus_w:.1f} W surplus is directed into charging.",
                  "high_solar_charge", health, soc, pv_w, load_w)

    if high_solar and soc >= s["soc_target"]:
        return _d("USE_SOLAR_DIRECT",
                  f"Solar is producing {pv_w:.1f} W and the battery is already at "
                  f"{soc:.1f} %, at or above target. The load is served directly from "
                  f"the panel, which avoids a charge/discharge round trip.",
                  "high_solar_direct", health, soc, pv_w, load_w)

    if pv_w <= load_w and soc >= s["soc_low"]:
        return _d("USE_BATTERY",
                  f"Solar ({pv_w:.1f} W) cannot cover the {load_w:.1f} W load, but the "
                  f"battery is at {soc:.1f} %, above the {s['soc_low']:.0f} % reserve. "
                  f"The battery makes up the shortfall.",
                  "low_solar_battery", health, soc, pv_w, load_w)

    if pv_w <= load_w and soc < s["soc_low"]:
        return _d("USE_BACKUP_GRID",
                  f"Solar ({pv_w:.1f} W) is short of the {load_w:.1f} W load and the "
                  f"battery is down to {soc:.1f} %, below the {s['soc_low']:.0f} % reserve. "
                  f"Backup supply carries the load so the battery can recover.",
                  "low_solar_backup", health, soc, pv_w, load_w)

    return _d("NO_ACTION",
              f"Generation {pv_w:.1f} W and load {load_w:.1f} W are balanced with the "
              f"battery at {soc:.1f} %. No change improves the outcome, and needless "
              f"switching wears the relay.", "balanced", health, soc, pv_w, load_w)


def _d(action, reason, rule, health, soc=None, pv=None, load=None) -> Dict:
    return {
        "action": action,
        "reason": reason,
        "rule_fired": rule,
        "health_status": health["status"],
        "inputs": {"soc_pct": None if soc is None else round(soc, 1),
                   "pv_w": None if pv is None else round(pv, 1),
                   "load_w": None if load is None else round(load, 1)},
        "requires_control": C.ACTIONS_REQUIRING_CONTROL.get(action) is not None,
        "target_relay_state": C.ACTIONS_REQUIRING_CONTROL.get(action),
    }


# ---------------------------------------------------------------------------
# Safety validator  (project document, section 12)  -- THE HARD GATE
# ---------------------------------------------------------------------------
def validate_action(action: str, reading: Dict) -> Dict:
    """
    Final permission gate. Returns PASSED or REJECTED with every check listed.
    Nothing reaches the hardware without passing here.
    """
    checks, blockers = [], []
    s = C.SAFETY

    def rec(name, ok, detail):
        checks.append([name, "PASS" if ok else "REJECT", detail])
        if not ok:
            blockers.append(detail)

    # 1. Whitelist
    rec("Action is on the allow-list", action in C.ALLOWED_ACTIONS,
        f"'{action}' is {'permitted' if action in C.ALLOWED_ACTIONS else 'NOT a recognised action'}. "
        f"Allowed: {', '.join(C.ALLOWED_ACTIONS)}.")

    # 2. Data present and fresh
    has_data = bool(reading.get("ok"))
    rec("Sensor data present", has_data,
        "A reading is available." if has_data else "No sensor reading is available.")

    age = reading.get("age_seconds", 0) or 0
    fresh = age <= s["data_max_age_s"]
    rec("Data freshness", fresh or action in ("SAFE_STATE", "NO_ACTION"),
        f"Reading is {age:.0f} s old (limit {s['data_max_age_s']} s)."
        + ("" if fresh else " Only SAFE_STATE and NO_ACTION are permitted on stale data."))

    # 3. Electrical envelope
    v = _f(reading.get("voltage"))
    i = _f(reading.get("current"))
    bv = _f(reading.get("battery_v"))
    t = _f(reading.get("temperature"))

    rec("Voltage within limits", v is None or s["voltage_min"] <= v <= s["voltage_max"],
        f"Panel voltage {v if v is None else f'{v:.2f}'} V against limits "
        f"{s['voltage_min']}..{s['voltage_max']} V.")
    rec("Current within limits", i is None or s["current_min"] <= i <= s["current_max"],
        f"Panel current {i if i is None else f'{i:.2f}'} A against limits "
        f"{s['current_min']}..{s['current_max']} A.")
    rec("Temperature within limits",
        t is None or s["temperature_min"] <= t <= s["temperature_max"],
        f"Temperature {t if t is None else f'{t:.1f}'} degC against limits "
        f"{s['temperature_min']}..{s['temperature_max']} degC.")
    rec("Battery voltage within limits",
        bv is None or s["battery_v_min"] <= bv <= s["battery_v_max"],
        f"Battery {bv if bv is None else f'{bv:.2f}'} V against limits "
        f"{s['battery_v_min']}..{s['battery_v_max']} V.")

    # 4. Battery protection -- never energise a load on a critically low battery
    soc = F.soc_from_voltage(bv or C.BATTERY_NOMINAL_V).value or 0
    draws_battery = action in ("USE_BATTERY", "USE_SOLAR_DIRECT")
    ok_soc = not (draws_battery and soc < s["soc_critical"])
    rec("Battery protection", ok_soc,
        f"SOC is {soc:.1f} %. "
        + (f"'{action}' would draw from a battery below the {s['soc_critical']} % floor."
           if not ok_soc else "Within the safe discharge envelope."))

    # 5. Overcharge protection
    ok_charge = not (action == "CHARGE_BATTERY" and bv is not None and bv >= s["battery_v_max"])
    rec("Overcharge protection", ok_charge,
        f"Battery {bv if bv is None else f'{bv:.2f}'} V against the "
        f"{s['battery_v_max']} V charge ceiling.")

    # 6. Relay anti-chatter
    target = C.ACTIONS_REQUIRING_CONTROL.get(action)
    since = time.time() - _last_switch["at"]
    chatter = (target is not None and _last_switch["state"] is not None
               and target != _last_switch["state"] and since < s["min_switch_interval_s"])
    rec("Switching rate limit", not chatter,
        f"Last relay change was {since:.0f} s ago (minimum interval "
        f"{s['min_switch_interval_s']} s)."
        + (" Blocked to prevent relay chatter." if chatter else ""))

    passed = not blockers
    return {
        "safety_check": "PASSED" if passed else "REJECTED",
        "action": action,
        "blockers": blockers,
        "table": {
            "title": f"Safety validation -- {action}",
            "columns": ["Check", "Result", "Detail"],
            "rows": checks,
        },
        "notes": ["This gate is deterministic Python. The LLM cannot relax or bypass it."],
    }


def note_switch(state: Optional[int]) -> None:
    if state is not None:
        _last_switch["at"] = time.time()
        _last_switch["state"] = state


def _f(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None
