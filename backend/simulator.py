"""
Physics-based solar system simulator.

This exists so the whole project can be built and demonstrated BEFORE your
friend's hardware data arrives. It is not random noise -- it models solar
geometry, cloud cover, panel temperature derating, battery charge/discharge and
a realistic household load profile. That means the correlations the Neo4j graph
finds are genuine physical relationships, and the Random Forest has real
structure to learn.

When real data arrives, set DATA_SOURCE=thingspeak in .env. Nothing else changes.
"""
import math
import random
from datetime import datetime, timedelta
from typing import Dict, List

import numpy as np
import pandas as pd

from . import config as C


def _solar_elevation(dt: datetime, lat: float, lon: float, tz_offset: float) -> float:
    """Solar elevation angle in degrees. Standard NOAA-style approximation."""
    day_of_year = dt.timetuple().tm_yday
    # Declination of the sun
    decl = 23.45 * math.sin(math.radians(360.0 * (284 + day_of_year) / 365.0))
    # Equation of time (minutes)
    b = math.radians(360.0 * (day_of_year - 81) / 364.0)
    eot = 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)
    # Local solar time
    lstm = 15.0 * tz_offset
    time_correction = 4.0 * (lon - lstm) + eot
    local_time = dt.hour + dt.minute / 60.0 + dt.second / 3600.0
    solar_time = local_time + time_correction / 60.0
    hour_angle = 15.0 * (solar_time - 12.0)

    lat_r, decl_r, ha_r = map(math.radians, (lat, decl, hour_angle))
    sin_elev = (math.sin(lat_r) * math.sin(decl_r)
                + math.cos(lat_r) * math.cos(decl_r) * math.cos(ha_r))
    return math.degrees(math.asin(max(-1.0, min(1.0, sin_elev))))


def _clear_sky_irradiance(elevation_deg: float) -> float:
    """Clear-sky global irradiance on a horizontal surface, W/m^2."""
    if elevation_deg <= 0:
        return 0.0
    zenith = math.radians(90.0 - elevation_deg)
    air_mass = 1.0 / (math.cos(zenith) + 0.50572 * (96.07995 - (90 - elevation_deg)) ** -1.6364)
    direct = 1353.0 * (0.7 ** (air_mass ** 0.678))
    return max(0.0, direct * math.sin(math.radians(elevation_deg)))


def _load_profile_w(dt: datetime, rng: random.Random) -> float:
    """A small DC load: constant base plus morning and evening usage peaks."""
    h = dt.hour + dt.minute / 60.0
    base = 3.0
    morning = 9.0 * math.exp(-((h - 7.5) ** 2) / 2.0)
    evening = 14.0 * math.exp(-((h - 19.5) ** 2) / 3.0)
    midday = 4.0 * math.exp(-((h - 13.0) ** 2) / 6.0)
    jitter = rng.gauss(0, 0.8)
    return max(0.5, base + morning + evening + midday + jitter)


def generate(days: int = 14,
             interval_s: int = 60,
             end: datetime = None,
             seed: int = 42,
             inject_fault_day: int = None) -> pd.DataFrame:
    """
    Generate `days` of history ending at `end` (default: now).

    inject_fault_day: if set, the panel on that day (0-indexed from the start)
    produces ~45 % less than physics predicts -- simulating soiling or partial
    shading. Useful for demonstrating the fault-detection scenario the project
    document asks for in section 16.
    """
    rng = random.Random(seed)
    np.random.seed(seed)
    end = end or datetime.now().replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    n_steps = int((end - start).total_seconds() // interval_s)

    # Smooth cloud-cover random walk, 0 (clear) .. 1 (overcast)
    cloud = np.zeros(n_steps)
    c = 0.25
    for i in range(n_steps):
        c += rng.gauss(0, 0.02)
        c = min(0.95, max(0.0, c * 0.995 + 0.25 * 0.005))
        cloud[i] = c

    soc = 75.0
    rows: List[Dict] = []
    load_state = 1

    for i in range(n_steps):
        ts = start + timedelta(seconds=i * interval_s)
        day_index = (ts - start).days

        # --- Solar resource --------------------------------------------------
        elev = _solar_elevation(ts, C.SITE_LATITUDE, C.SITE_LONGITUDE, C.TZ_OFFSET_HOURS)
        clear = _clear_sky_irradiance(elev)
        cloud_factor = 1.0 - 0.75 * cloud[i]
        irradiance = max(0.0, clear * cloud_factor + rng.gauss(0, 8))

        # --- Temperature -----------------------------------------------------
        h = ts.hour + ts.minute / 60.0
        ambient = 24.0 + 8.0 * math.sin(math.radians((h - 9.0) * 15.0)) + rng.gauss(0, 0.4)
        cell_temp = ambient + ((C.PANEL_NOCT - 20.0) / 800.0) * irradiance

        # --- Panel output ----------------------------------------------------
        derate = 1.0 + C.PANEL_TEMP_COEFF * (cell_temp - 25.0)
        pv_power = C.PANEL_RATED_W * (irradiance / 1000.0) * derate
        if inject_fault_day is not None and day_index == inject_fault_day:
            pv_power *= 0.55                      # soiling / partial shading
        pv_power = max(0.0, pv_power + rng.gauss(0, 0.35))

        # --- Load ------------------------------------------------------------
        demand = _load_profile_w(ts, rng)
        # Simple controller: shed load if the battery gets critically low
        if soc < 22.0:
            load_state = 0
        elif soc > 45.0:
            load_state = 1
        load_power = demand if load_state else 0.0

        # --- Battery integration ---------------------------------------------
        net_w = pv_power - load_power
        cap_wh = C.BATTERY_CAPACITY_AH * C.BATTERY_NOMINAL_V
        delta_wh = net_w * (interval_s / 3600.0)
        eff = 0.90 if delta_wh > 0 else 1.0      # charging losses
        soc = max(5.0, min(100.0, soc + (delta_wh * eff / cap_wh) * 100.0))

        # Terminal voltage: resting voltage displaced by charge/discharge current
        v_rest = C.BATTERY_V_EMPTY + (soc / 100.0) * (C.BATTERY_V_FULL - C.BATTERY_V_EMPTY)
        batt_current = net_w / C.BATTERY_NOMINAL_V
        battery_v = v_rest + batt_current * 0.08 + rng.gauss(0, 0.01)

        # --- What the ESP32 would actually report -----------------------------
        panel_v = 0.0 if pv_power <= 0.05 else min(21.5, 17.0 + 2.5 * (irradiance / 1000.0)
                                                   + rng.gauss(0, 0.15))
        panel_i = pv_power / panel_v if panel_v > 0.5 else 0.0
        light_adc = min(C.LIGHT_ADC_MAX,
                        (irradiance / C.IRRADIANCE_MAX_W_M2) * C.LIGHT_ADC_MAX + rng.gauss(0, 15))

        rows.append({
            "timestamp": ts,
            "voltage": round(panel_v, 3),
            "current": round(panel_i, 4),
            "power": round(pv_power, 3),
            "temperature": round(ambient, 2),
            "light": round(max(0.0, light_adc), 1),
            "battery_v": round(battery_v, 3),
            "load_state": int(load_state),
            "load_power": round(load_power, 3),
            # Ground-truth columns the real hardware will not provide.
            # Kept for validating the estimators; never used as model inputs.
            "_true_irradiance": round(irradiance, 2),
            "_true_soc": round(soc, 2),
            "_true_cell_temp": round(cell_temp, 2),
        })

    return pd.DataFrame(rows)


def latest_reading(now: datetime = None, seed: int = None) -> Dict:
    """One live-looking sample for the dashboard's real-time tick."""
    now = now or datetime.now()
    df = generate(days=1, interval_s=60, end=now,
                  seed=seed if seed is not None else random.randint(1, 10_000))
    row = df.iloc[-1].to_dict()
    row["timestamp"] = row["timestamp"].isoformat()
    return row


def ensure_history(days: int = 14, interval_s: int = 60) -> pd.DataFrame:
    """Load the cached simulated history, generating it on first run."""
    if C.HISTORY_CSV.exists():
        df = pd.read_csv(C.HISTORY_CSV, parse_dates=["timestamp"])
        if len(df) > 100:
            return df
    df = generate(days=days, interval_s=interval_s, inject_fault_day=days - 3)
    df.to_csv(C.HISTORY_CSV, index=False)
    return df


if __name__ == "__main__":
    frame = generate(days=14, interval_s=60, inject_fault_day=11)
    frame.to_csv(C.HISTORY_CSV, index=False)
    print(f"Wrote {len(frame):,} rows to {C.HISTORY_CSV}")
    print(frame.describe())
