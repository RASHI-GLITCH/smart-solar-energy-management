"""
One interface, three backends: simulator / ThingSpeak / CSV.

Everything upstream (tools, agent, dashboard) talks only to `get_latest()` and
`get_history()`. Swapping to your friend's real feed is a one-line change in
.env -- no other file needs to be touched.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

import pandas as pd
import requests

from . import config as C
from . import simulator

_CANONICAL = ["timestamp", "voltage", "current", "power", "temperature",
              "light", "battery_v", "load_state", "load_power"]


# ---------------------------------------------------------------------------
# ThingSpeak
# ---------------------------------------------------------------------------
def _thingspeak_frame(results: int = 8000) -> pd.DataFrame:
    if not C.THINGSPEAK_CHANNEL_ID:
        raise RuntimeError("THINGSPEAK_CHANNEL_ID is not set in .env")

    url = f"https://api.thingspeak.com/channels/{C.THINGSPEAK_CHANNEL_ID}/feeds.json"
    params = {"results": min(results, 8000)}
    if C.THINGSPEAK_READ_API_KEY:
        params["api_key"] = C.THINGSPEAK_READ_API_KEY

    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    feeds = resp.json().get("feeds", [])
    if not feeds:
        return pd.DataFrame(columns=_CANONICAL)

    df = pd.DataFrame(feeds)
    out = pd.DataFrame()
    out["timestamp"] = pd.to_datetime(df["created_at"], utc=True, errors="coerce")
    out["timestamp"] = out["timestamp"].dt.tz_convert(
        timezone(timedelta(hours=C.TZ_OFFSET_HOURS))).dt.tz_localize(None)

    for field, name in C.THINGSPEAK_FIELDS.items():
        out[name] = pd.to_numeric(df.get(field), errors="coerce") if field in df else pd.NA

    for col in _CANONICAL:
        if col not in out.columns:
            out[col] = pd.NA

    # If he doesn't send power, derive it. Never leave it missing.
    if out["power"].isna().all():
        out["power"] = out["voltage"] * out["current"]

    return out[_CANONICAL].dropna(subset=["timestamp"]).sort_values("timestamp")


def push_command(command: str) -> Dict:
    """
    Send a command back to the ESP32 through ThingSpeak TalkBack.
    The ESP32 polls the TalkBack queue and executes the next command.
    """
    if not (C.THINGSPEAK_TALKBACK_ID and C.THINGSPEAK_TALKBACK_API_KEY):
        return {"sent": False, "reason": "TalkBack not configured -- simulated only",
                "command": command}
    url = f"https://api.thingspeak.com/talkbacks/{C.THINGSPEAK_TALKBACK_ID}/commands.json"
    try:
        r = requests.post(url, data={"api_key": C.THINGSPEAK_TALKBACK_API_KEY,
                                     "command_string": command,
                                     "position": 1}, timeout=15)
        r.raise_for_status()
        return {"sent": True, "command": command, "response": r.json()}
    except Exception as exc:
        return {"sent": False, "reason": str(exc), "command": command}


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------
_cache: Dict[str, object] = {"frame": None, "fetched_at": None}


def get_history(days: int = 14, force: bool = False) -> pd.DataFrame:
    """Historical frame, cached for 60 s so the dashboard can poll freely."""
    now = datetime.now()
    if (not force and _cache["frame"] is not None
            and _cache["fetched_at"] and (now - _cache["fetched_at"]).total_seconds() < 60):
        return _cache["frame"]

    if C.DATA_SOURCE == "thingspeak":
        df = _thingspeak_frame()
    elif C.DATA_SOURCE == "csv":
        path = C.ROOT / C.CSV_PATH
        df = pd.read_csv(path, parse_dates=["timestamp"])
    else:
        df = simulator.ensure_history(days=days)

    if not df.empty:
        df = df[df["timestamp"] >= now - timedelta(days=days)]

    _cache["frame"], _cache["fetched_at"] = df, now
    return df


_latest_cache: Dict[str, object] = {"row": None, "at": None}
LATEST_TTL_S = 4.0


def get_latest(force: bool = False) -> Dict:
    """
    Most recent reading plus data-freshness metadata.
    `stale` is the flag the safety layer uses to refuse to act on old data.

    Cached for a few seconds so that every tool called while answering one
    question sees the SAME reading. Without this, a single dashboard render
    could show the battery at 22 % in one panel and 23 % in another.
    """
    now_t = datetime.now()
    if (not force and _latest_cache["row"] is not None and _latest_cache["at"]
            and (now_t - _latest_cache["at"]).total_seconds() < LATEST_TTL_S):
        return dict(_latest_cache["row"])

    if C.DATA_SOURCE == "simulator":
        row = simulator.latest_reading()
        ts = datetime.fromisoformat(row["timestamp"])
        age = 0.0
    else:
        df = get_history(days=1)
        if df.empty:
            return {"ok": False, "reason": "no data available from source",
                    "source": C.DATA_SOURCE, "age_seconds": 0, "stale": True}
        last = df.iloc[-1]
        row = {k: (None if pd.isna(last.get(k)) else last.get(k)) for k in _CANONICAL}
        ts = pd.to_datetime(last["timestamp"]).to_pydatetime()
        age = (datetime.now() - ts).total_seconds()
        row["timestamp"] = ts.isoformat()

    if row.get("power") in (None, 0) and row.get("voltage") and row.get("current"):
        row["power"] = float(row["voltage"]) * float(row["current"])

    row["ok"] = True
    row["source"] = C.DATA_SOURCE
    row["age_seconds"] = round(age, 1)
    row["stale"] = age > C.SAFETY["data_max_age_s"]

    _latest_cache["row"], _latest_cache["at"] = dict(row), now_t
    return row


def source_info() -> Dict:
    df = get_history()
    return {
        "source": C.DATA_SOURCE,
        "rows": int(len(df)),
        "first": df["timestamp"].min().isoformat() if len(df) else None,
        "last": df["timestamp"].max().isoformat() if len(df) else None,
        "thingspeak_configured": bool(C.THINGSPEAK_CHANNEL_ID),
        "talkback_configured": bool(C.THINGSPEAK_TALKBACK_ID),
        "columns": [c for c in _CANONICAL if c in df.columns],
    }
