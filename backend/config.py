"""
Central configuration: every threshold, constant and field mapping lives here.

Nothing else in the project should hardcode a number. When your friend confirms
the real hardware specs, you change values HERE and the whole system follows.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _f(key: str, default: float) -> float:
    raw = os.getenv(key, "")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _s(key: str, default: str = "") -> str:
    return (os.getenv(key) or default).strip()


# ----------------------------------------------------------------------------
# Paths
# ----------------------------------------------------------------------------
DATA_DIR = ROOT / "data"
LOGS_DIR = ROOT / "logs"
WEB_DIR = ROOT / "web"
for _d in (DATA_DIR, LOGS_DIR):
    _d.mkdir(exist_ok=True)

HISTORY_CSV = DATA_DIR / "history.csv"
DECISION_LOG = LOGS_DIR / "decisions.csv"
ALERT_LOG = LOGS_DIR / "alerts.csv"
FORECAST_CACHE = DATA_DIR / "forecast_cache.json"

# ----------------------------------------------------------------------------
# LLM
# ----------------------------------------------------------------------------
GROQ_API_KEY = _s("GROQ_API_KEY")
GROQ_MODEL = _s("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# ----------------------------------------------------------------------------
# Data source
# ----------------------------------------------------------------------------
DATA_SOURCE = _s("DATA_SOURCE", "simulator").lower()
CSV_PATH = _s("CSV_PATH", "data/history.csv")

THINGSPEAK_CHANNEL_ID = _s("THINGSPEAK_CHANNEL_ID")
THINGSPEAK_READ_API_KEY = _s("THINGSPEAK_READ_API_KEY")
THINGSPEAK_WRITE_API_KEY = _s("THINGSPEAK_WRITE_API_KEY")
THINGSPEAK_TALKBACK_ID = _s("THINGSPEAK_TALKBACK_ID")
THINGSPEAK_TALKBACK_API_KEY = _s("THINGSPEAK_TALKBACK_API_KEY")

# ---- ThingSpeak field mapping ----------------------------------------------
# ASK YOUR FRIEND FOR THIS. It is the single most important thing to confirm.
# Change the right-hand side to match whatever he actually writes to field1..8.
THINGSPEAK_FIELDS = {
    "field1": "voltage",       # V   - panel / bus voltage
    "field2": "current",       # A   - panel / load current
    "field3": "power",         # W   - if he sends it; else we compute V*I
    "field4": "temperature",   # degC- panel or ambient temperature
    "field5": "light",         # ADC or lux - irradiance proxy (LDR)
    "field6": "battery_v",     # V   - battery terminal voltage
    "field7": "load_state",    # 0/1 - relay state
    "field8": "load_power",    # W   - load side power, if measured
}

# ----------------------------------------------------------------------------
# Neo4j
# ----------------------------------------------------------------------------
NEO4J_URI = _s("NEO4J_URI")
NEO4J_USER = _s("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = _s("NEO4J_PASSWORD")

# ----------------------------------------------------------------------------
# Site + hardware constants
# ----------------------------------------------------------------------------
SITE_LATITUDE = _f("SITE_LATITUDE", 15.85)
SITE_LONGITUDE = _f("SITE_LONGITUDE", 74.50)
TZ_OFFSET_HOURS = _f("SITE_TIMEZONE_OFFSET", 5.5)

PANEL_RATED_W = _f("PANEL_RATED_W", 50.0)        # P_stc at 1000 W/m2, 25 degC
PANEL_AREA_M2 = _f("PANEL_AREA_M2", 0.35)
PANEL_TEMP_COEFF = _f("PANEL_TEMP_COEFF", -0.0040)  # gamma, per degC
PANEL_NOCT = 45.0                                 # degC

BATTERY_NOMINAL_V = _f("BATTERY_NOMINAL_V", 12.0)
BATTERY_CAPACITY_AH = _f("BATTERY_CAPACITY_AH", 7.0)
BATTERY_V_FULL = _f("BATTERY_V_FULL", 12.7)
BATTERY_V_EMPTY = _f("BATTERY_V_EMPTY", 11.8)
BATTERY_MAX_DOD = _f("BATTERY_MAX_DOD", 0.60)

TARIFF_PER_KWH = _f("TARIFF_PER_KWH", 8.0)         # INR
GRID_CO2_KG_PER_KWH = _f("GRID_CO2_KG_PER_KWH", 0.71)

# Irradiance proxy calibration: light sensor raw -> W/m2.
# 12-bit ADC on ESP32 = 0..4095. This is an APPROXIMATION and every answer
# derived from it is flagged as such.
LIGHT_ADC_MAX = 4095.0
IRRADIANCE_MAX_W_M2 = 1000.0

# ----------------------------------------------------------------------------
# Safety limits  -- the hard gate. The LLM can never widen these.
# ----------------------------------------------------------------------------
SAFETY = {
    "voltage_min": 0.0,
    "voltage_max": 25.0,          # panel open-circuit headroom
    "current_min": -10.0,
    "current_max": 10.0,
    "temperature_min": -10.0,
    "temperature_max": 80.0,
    "battery_v_min": 10.5,        # below this: emergency, disconnect load
    "battery_v_max": 14.8,        # above this: overcharge, stop charging
    "soc_critical": 20.0,         # %
    "soc_low": 40.0,              # %
    "soc_target": 80.0,           # %
    "soc_full": 95.0,             # %
    "data_max_age_s": 120,        # older than this = stale, do not act
    "min_switch_interval_s": 60,  # anti-chatter on the relay
}

# Actions the agent is allowed to request. Anything else is rejected outright.
ALLOWED_ACTIONS = [
    "CHARGE_BATTERY",
    "USE_SOLAR_DIRECT",
    "USE_BATTERY",
    "USE_BACKUP_GRID",
    "SHED_NONCRITICAL_LOAD",
    "SAFE_STATE",
    "NO_ACTION",
]

# Which actions actually energise the relay.
ACTIONS_REQUIRING_CONTROL = {
    "USE_SOLAR_DIRECT": 1,
    "USE_BATTERY": 1,
    "USE_BACKUP_GRID": 1,
    "SHED_NONCRITICAL_LOAD": 0,
    "SAFE_STATE": 0,
    "CHARGE_BATTERY": 0,
    "NO_ACTION": None,
}
