"""
Formula registry.

Every number this system reports is produced by a function in here, and every
function returns BOTH the value and a human-readable substitution string.
That is what lets the agent answer a question first, then show exactly how the
number was calculated -- with no hallucinated arithmetic.

    result = F.power(voltage=18.4, current=2.31)
    result.value          -> 42.5
    result.substitution   -> "P = 18.40 V x 2.31 A = 42.50 W"
    result.latex          -> "P = V \\times I"
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import config as C


@dataclass
class Calc:
    """A computed value plus the full audit trail of how it was computed."""
    key: str
    name: str
    value: Optional[float]
    unit: str
    latex: str
    substitution: str
    explanation: str
    assumptions: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "value": None if self.value is None else round(float(self.value), 4),
            "unit": self.unit,
            "latex": self.latex,
            "substitution": self.substitution,
            "explanation": self.explanation,
            "assumptions": self.assumptions,
        }


def _n(x: Optional[float], nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:,.{nd}f}"


# ---------------------------------------------------------------------------
# 1. Electrical basics
# ---------------------------------------------------------------------------
def power(voltage: float, current: float) -> Calc:
    val = voltage * current
    return Calc(
        key="power",
        name="Instantaneous DC power",
        value=val,
        unit="W",
        latex=r"P = V \times I",
        substitution=f"P = {_n(voltage)} V x {_n(current)} A = {_n(val)} W",
        explanation="Instantaneous power on a DC circuit is voltage times current.",
    )


def energy_from_samples(power_series_w: List[float], interval_s: float) -> Calc:
    """Discrete integration of a power series into energy (Wh)."""
    clean = [p for p in power_series_w if p is not None]
    if not clean:
        return Calc("energy", "Energy", None, "Wh",
                    r"E = \sum P_i \, \Delta t",
                    "no samples available", "Energy needs at least one sample.")
    total_ws = sum(clean) * interval_s
    val = total_ws / 3600.0
    return Calc(
        key="energy",
        name="Energy over the period",
        value=val,
        unit="Wh",
        latex=r"E = \frac{\Delta t}{3600}\sum_{i=1}^{n} P_i",
        substitution=(
            f"E = ({interval_s:.0f} s / 3600) x {_n(sum(clean))} W "
            f"(sum of {len(clean)} samples) = {_n(val)} Wh"
        ),
        explanation=(
            "Each sample holds for one measurement interval, so energy is the sum of "
            "the power samples multiplied by the interval, converted from watt-seconds "
            "to watt-hours."
        ),
        assumptions=[f"Power is assumed constant across each {interval_s:.0f} s interval."],
    )


def mean_power(energy_wh: float, hours: float) -> Calc:
    val = energy_wh / hours if hours else None
    return Calc(
        key="mean_power",
        name="Average power",
        value=val,
        unit="W",
        latex=r"\bar{P} = \frac{E}{t}",
        substitution=f"P_avg = {_n(energy_wh)} Wh / {_n(hours)} h = {_n(val)} W",
        explanation="Average power is total energy divided by the elapsed time.",
    )


# ---------------------------------------------------------------------------
# 2. Battery
# ---------------------------------------------------------------------------
def soc_from_voltage(battery_v: float) -> Calc:
    v_min, v_max = C.BATTERY_V_EMPTY, C.BATTERY_V_FULL
    raw = (battery_v - v_min) / (v_max - v_min) * 100.0
    val = max(0.0, min(100.0, raw))
    return Calc(
        key="soc",
        name="Battery state of charge",
        value=val,
        unit="%",
        latex=r"SOC = \frac{V_{bat} - V_{empty}}{V_{full} - V_{empty}} \times 100",
        substitution=(
            f"SOC = ({_n(battery_v)} - {_n(v_min)}) / ({_n(v_max)} - {_n(v_min)}) "
            f"x 100 = {_n(val)} %"
        ),
        explanation=(
            "State of charge is estimated by linear interpolation between the empty "
            "and full resting voltages of the battery."
        ),
        assumptions=[
            "Linear voltage-to-SOC approximation.",
            "Accurate only at rest; under load or charge the terminal voltage is "
            "displaced, so this reads low when discharging and high when charging.",
            f"Calibrated for V_empty={v_min} V and V_full={v_max} V.",
        ],
    )


def usable_battery_energy(soc_pct: float) -> Calc:
    cap_wh = C.BATTERY_CAPACITY_AH * C.BATTERY_NOMINAL_V
    usable_frac = max(0.0, (soc_pct / 100.0) - (1.0 - C.BATTERY_MAX_DOD))
    val = cap_wh * usable_frac
    return Calc(
        key="battery_usable_wh",
        name="Usable battery energy",
        value=val,
        unit="Wh",
        latex=r"E_{usable} = C_{Ah} \times V_{nom} \times \left(\frac{SOC}{100} - (1 - DoD_{max})\right)",
        substitution=(
            f"E = {_n(C.BATTERY_CAPACITY_AH)} Ah x {_n(C.BATTERY_NOMINAL_V)} V x "
            f"({_n(soc_pct)}/100 - {_n(1 - C.BATTERY_MAX_DOD)}) = {_n(val)} Wh"
        ),
        explanation=(
            "Only the charge above the depth-of-discharge floor may be used. Draining "
            "below that floor shortens battery life, so it is excluded from usable energy."
        ),
        assumptions=[f"Maximum depth of discharge limited to {C.BATTERY_MAX_DOD * 100:.0f} %."],
    )


def autonomy_hours(usable_wh: float, load_w: float) -> Calc:
    val = usable_wh / load_w if load_w and load_w > 0 else None
    return Calc(
        key="autonomy_h",
        name="Battery autonomy",
        value=val,
        unit="h",
        latex=r"t_{autonomy} = \frac{E_{usable}}{P_{load}}",
        substitution=(
            f"t = {_n(usable_wh)} Wh / {_n(load_w)} W = {_n(val)} h"
            if val is not None else "t = undefined (load power is zero)"
        ),
        explanation="How long the battery alone can carry the present load.",
        assumptions=["Load is assumed constant and no solar input is available."],
    )


def charge_time(soc_now: float, soc_target: float, charge_current_a: float) -> Calc:
    if not charge_current_a or charge_current_a <= 0:
        return Calc("charge_time_h", "Time to target SOC", None, "h",
                    r"t = \frac{C_{Ah}(SOC_{target}-SOC_{now})}{100 \times I_{charge}}",
                    "undefined (no charging current)",
                    "Charging time needs a positive charging current.")
    ah_needed = C.BATTERY_CAPACITY_AH * (soc_target - soc_now) / 100.0
    val = ah_needed / charge_current_a
    return Calc(
        key="charge_time_h",
        name="Time to reach target SOC",
        value=val,
        unit="h",
        latex=r"t = \frac{C_{Ah}\,(SOC_{target} - SOC_{now})}{100 \times I_{charge}}",
        substitution=(
            f"t = {_n(C.BATTERY_CAPACITY_AH)} Ah x ({_n(soc_target)} - {_n(soc_now)}) / 100 "
            f"/ {_n(charge_current_a)} A = {_n(val)} h"
        ),
        explanation="Charge deficit in amp-hours divided by the available charging current.",
        assumptions=["Constant charging current; charge efficiency losses ignored."],
    )


# ---------------------------------------------------------------------------
# 3. Solar / panel physics
# ---------------------------------------------------------------------------
def irradiance_from_light(light_raw: float) -> Calc:
    val = (light_raw / C.LIGHT_ADC_MAX) * C.IRRADIANCE_MAX_W_M2
    return Calc(
        key="irradiance",
        name="Estimated irradiance",
        value=val,
        unit="W/m^2",
        latex=r"G \approx \frac{ADC}{ADC_{max}} \times G_{max}",
        substitution=(
            f"G = ({_n(light_raw, 0)} / {_n(C.LIGHT_ADC_MAX, 0)}) x "
            f"{_n(C.IRRADIANCE_MAX_W_M2, 0)} = {_n(val)} W/m^2"
        ),
        explanation="Irradiance is inferred from the light sensor's raw reading.",
        assumptions=[
            "UNCALIBRATED. An LDR responds non-linearly to irradiance; this linear map "
            "is a rough proxy only.",
            "To calibrate properly you would need a reference pyranometer.",
        ],
    )


def cell_temperature(ambient_c: float, irradiance: float) -> Calc:
    val = ambient_c + ((C.PANEL_NOCT - 20.0) / 800.0) * irradiance
    return Calc(
        key="cell_temp",
        name="Estimated cell temperature",
        value=val,
        unit="degC",
        latex=r"T_{cell} = T_{amb} + \frac{NOCT - 20}{800} \times G",
        substitution=(
            f"T_cell = {_n(ambient_c)} + ({_n(C.PANEL_NOCT, 0)} - 20)/800 x "
            f"{_n(irradiance)} = {_n(val)} degC"
        ),
        explanation=(
            "The standard NOCT model: cells run hotter than ambient in proportion to "
            "the irradiance falling on them."
        ),
        assumptions=[f"NOCT assumed {C.PANEL_NOCT:.0f} degC."],
    )


def panel_efficiency(power_w: float, irradiance: float) -> Calc:
    denom = irradiance * C.PANEL_AREA_M2
    val = (power_w / denom * 100.0) if denom > 1e-6 else None
    return Calc(
        key="efficiency",
        name="Panel conversion efficiency",
        value=val,
        unit="%",
        latex=r"\eta = \frac{P_{out}}{G \times A} \times 100",
        substitution=(
            f"eta = {_n(power_w)} W / ({_n(irradiance)} W/m^2 x {_n(C.PANEL_AREA_M2, 3)} m^2) "
            f"x 100 = {_n(val)} %"
            if val is not None else "eta = undefined (no irradiance)"
        ),
        explanation="Fraction of the solar energy hitting the panel that becomes electricity.",
        assumptions=[f"Panel area assumed {C.PANEL_AREA_M2} m^2.",
                     "Inherits the irradiance estimate's calibration uncertainty."],
    )


def temperature_derated_power(irradiance: float, cell_temp_c: float) -> Calc:
    ratio = irradiance / 1000.0
    derate = 1.0 + C.PANEL_TEMP_COEFF * (cell_temp_c - 25.0)
    val = C.PANEL_RATED_W * ratio * derate
    return Calc(
        key="expected_power",
        name="Expected power (physics model)",
        value=val,
        unit="W",
        latex=r"P = P_{STC} \times \frac{G}{1000} \times \left[1 + \gamma (T_{cell} - 25)\right]",
        substitution=(
            f"P = {_n(C.PANEL_RATED_W, 1)} W x ({_n(irradiance)}/1000) x "
            f"[1 + ({C.PANEL_TEMP_COEFF}) x ({_n(cell_temp_c)} - 25)] = {_n(val)} W"
        ),
        explanation=(
            "Panel output scales with irradiance and falls as the cells heat up. "
            "This is the physics baseline the Random Forest prediction is checked against."
        ),
        assumptions=[
            f"P_STC = {C.PANEL_RATED_W:.0f} W, temperature coefficient "
            f"{C.PANEL_TEMP_COEFF} per degC.",
        ],
    )


def performance_ratio(actual_wh: float, theoretical_wh: float) -> Calc:
    val = (actual_wh / theoretical_wh * 100.0) if theoretical_wh > 1e-6 else None
    return Calc(
        key="performance_ratio",
        name="Performance ratio",
        value=val,
        unit="%",
        latex=r"PR = \frac{E_{actual}}{E_{theoretical}} \times 100",
        substitution=(
            f"PR = {_n(actual_wh)} Wh / {_n(theoretical_wh)} Wh x 100 = {_n(val)} %"
            if val is not None else "PR = undefined"
        ),
        explanation=(
            "How much of the theoretically available energy the system actually captured. "
            "A falling PR points to soiling, shading or a developing fault."
        ),
    )


# ---------------------------------------------------------------------------
# 4. Fault detection  (PDF section 13)
# ---------------------------------------------------------------------------
def prediction_residual(measured_w: float, predicted_w: float) -> Calc:
    val = measured_w - predicted_w
    pct = (val / predicted_w * 100.0) if abs(predicted_w) > 1e-6 else None
    return Calc(
        key="residual",
        name="Prediction residual",
        value=val,
        unit="W",
        latex=r"e(t) = P_{measured}(t) - P_{predicted}(t)",
        substitution=(
            f"e = {_n(measured_w)} W - {_n(predicted_w)} W = {_n(val)} W"
            + (f"  ({_n(pct, 1)} % of prediction)" if pct is not None else "")
        ),
        explanation=(
            "The gap between what the panel actually produced and what the model expected. "
            "A large, persistent negative residual warrants investigation -- it does not by "
            "itself prove a hardware fault."
        ),
    )


# ---------------------------------------------------------------------------
# 5. Energy balance, cost, emissions
# ---------------------------------------------------------------------------
def net_energy(generated_wh: float, consumed_wh: float) -> Calc:
    val = generated_wh - consumed_wh
    verdict = "surplus" if val >= 0 else "deficit"
    return Calc(
        key="net_energy",
        name="Net energy balance",
        value=val,
        unit="Wh",
        latex=r"E_{net} = E_{generated} - E_{consumed}",
        substitution=(
            f"E_net = {_n(generated_wh)} Wh - {_n(consumed_wh)} Wh = {_n(val)} Wh ({verdict})"
        ),
        explanation="Positive means the system generated more than it used over the period.",
    )


def self_sufficiency(generated_wh: float, consumed_wh: float) -> Calc:
    val = min(100.0, generated_wh / consumed_wh * 100.0) if consumed_wh > 1e-6 else None
    return Calc(
        key="self_sufficiency",
        name="Self-sufficiency",
        value=val,
        unit="%",
        latex=r"S = \min\left(100,\; \frac{E_{gen}}{E_{load}} \times 100\right)",
        substitution=(
            f"S = {_n(generated_wh)} / {_n(consumed_wh)} x 100 = {_n(val)} %"
            if val is not None else "S = undefined (no load)"
        ),
        explanation="Share of the load that solar generation was able to cover.",
    )


def cost_saving(energy_wh: float) -> Calc:
    kwh = energy_wh / 1000.0
    val = kwh * C.TARIFF_PER_KWH
    return Calc(
        key="cost_saving",
        name="Grid cost avoided",
        value=val,
        unit="INR",
        latex=r"Cost = \frac{E_{Wh}}{1000} \times \text{tariff}",
        substitution=(
            f"Cost = {_n(energy_wh)} Wh / 1000 x {_n(C.TARIFF_PER_KWH)} INR/kWh = {_n(val)} INR"
        ),
        explanation="Money not spent on grid electricity because solar supplied the load.",
        assumptions=[f"Flat tariff of {C.TARIFF_PER_KWH} INR/kWh assumed."],
    )


def co2_avoided(energy_wh: float) -> Calc:
    kwh = energy_wh / 1000.0
    val = kwh * C.GRID_CO2_KG_PER_KWH
    return Calc(
        key="co2_avoided",
        name="CO2 emissions avoided",
        value=val,
        unit="kg",
        latex=r"m_{CO_2} = \frac{E_{Wh}}{1000} \times EF_{grid}",
        substitution=(
            f"CO2 = {_n(energy_wh)} Wh / 1000 x {_n(C.GRID_CO2_KG_PER_KWH, 2)} kg/kWh "
            f"= {_n(val, 3)} kg"
        ),
        explanation="Emissions displaced by using solar instead of grid electricity.",
        assumptions=[f"Grid emission factor {C.GRID_CO2_KG_PER_KWH} kg CO2/kWh."],
    )


# ---------------------------------------------------------------------------
# 6. Statistics (model evaluation + correlation)
# ---------------------------------------------------------------------------
def pearson_r(x: List[float], y: List[float], name_x: str, name_y: str) -> Calc:
    import numpy as np
    ax, ay = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    mask = ~(np.isnan(ax) | np.isnan(ay))
    ax, ay = ax[mask], ay[mask]
    if len(ax) < 3 or ax.std() < 1e-9 or ay.std() < 1e-9:
        val = None
        sub = "r = undefined (not enough varying data)"
    else:
        val = float(np.corrcoef(ax, ay)[0, 1])
        sub = (f"r({name_x}, {name_y}) = {val:+.3f}  "
               f"over n = {len(ax)} paired samples")
    return Calc(
        key=f"corr_{name_x}_{name_y}",
        name=f"Correlation: {name_x} vs {name_y}",
        value=val,
        unit="",
        latex=r"r = \frac{\sum (x_i-\bar{x})(y_i-\bar{y})}"
              r"{\sqrt{\sum (x_i-\bar{x})^2 \sum (y_i-\bar{y})^2}}",
        substitution=sub,
        explanation=(
            "Pearson correlation measures how strongly two variables move together "
            "in a straight line. It ranges from -1 to +1. Correlation is not causation."
        ),
    )


def regression_metrics(y_true: List[float], y_pred: List[float]) -> List[Calc]:
    import numpy as np
    yt, yp = np.asarray(y_true, float), np.asarray(y_pred, float)
    n = len(yt)
    mae = float(np.mean(np.abs(yt - yp)))
    rmse = float(np.sqrt(np.mean((yt - yp) ** 2)))
    ss_res = float(np.sum((yt - yp) ** 2))
    ss_tot = float(np.sum((yt - yt.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 1e-9 else None
    return [
        Calc("mae", "Mean absolute error", mae, "W",
             r"MAE = \frac{1}{n}\sum |y_i - \hat{y}_i|",
             f"MAE = (1/{n}) x sum|error| = {_n(mae)} W",
             "Average size of the prediction error, in watts."),
        Calc("rmse", "Root mean squared error", rmse, "W",
             r"RMSE = \sqrt{\frac{1}{n}\sum (y_i - \hat{y}_i)^2}",
             f"RMSE = sqrt((1/{n}) x sum(error^2)) = {_n(rmse)} W",
             "Like MAE but penalises large errors more heavily."),
        Calc("r2", "Coefficient of determination", r2, "",
             r"R^2 = 1 - \frac{\sum (y_i - \hat{y}_i)^2}{\sum (y_i - \bar{y})^2}",
             f"R2 = 1 - {_n(ss_res)} / {_n(ss_tot)} = {_n(r2, 4)}",
             "Share of the variance in the data the model explains. 1.0 is perfect."),
    ]


# Human-readable catalogue, exposed to the UI so students can browse the maths.
CATALOGUE = [
    ("power", "Instantaneous DC power", r"P = V \times I", "Electrical"),
    ("energy", "Energy from power samples", r"E = \frac{\Delta t}{3600}\sum P_i", "Electrical"),
    ("soc", "Battery state of charge", r"SOC = \frac{V_{bat}-V_{empty}}{V_{full}-V_{empty}}\times 100", "Battery"),
    ("battery_usable_wh", "Usable battery energy", r"E_{usable} = C_{Ah} V_{nom}\left(\frac{SOC}{100}-(1-DoD)\right)", "Battery"),
    ("autonomy_h", "Battery autonomy", r"t = \frac{E_{usable}}{P_{load}}", "Battery"),
    ("charge_time_h", "Time to target SOC", r"t = \frac{C_{Ah}(SOC_{tgt}-SOC_{now})}{100\,I_{chg}}", "Battery"),
    ("irradiance", "Irradiance from LDR", r"G \approx \frac{ADC}{ADC_{max}}G_{max}", "Solar"),
    ("cell_temp", "Cell temperature (NOCT)", r"T_{cell}=T_{amb}+\frac{NOCT-20}{800}G", "Solar"),
    ("efficiency", "Panel efficiency", r"\eta = \frac{P_{out}}{G\,A}\times 100", "Solar"),
    ("expected_power", "Expected power", r"P = P_{STC}\frac{G}{1000}[1+\gamma(T_{cell}-25)]", "Solar"),
    ("performance_ratio", "Performance ratio", r"PR=\frac{E_{actual}}{E_{theoretical}}\times100", "Solar"),
    ("residual", "Prediction residual", r"e(t)=P_{meas}(t)-P_{pred}(t)", "Fault"),
    ("net_energy", "Net energy balance", r"E_{net}=E_{gen}-E_{load}", "Energy"),
    ("self_sufficiency", "Self-sufficiency", r"S=\min(100,\frac{E_{gen}}{E_{load}}\times100)", "Energy"),
    ("cost_saving", "Cost avoided", r"Cost=\frac{E_{Wh}}{1000}\times tariff", "Impact"),
    ("co2_avoided", "CO2 avoided", r"m_{CO_2}=\frac{E_{Wh}}{1000}\times EF", "Impact"),
    ("corr", "Pearson correlation", r"r=\frac{\sum(x-\bar{x})(y-\bar{y})}{\sqrt{\sum(x-\bar{x})^2\sum(y-\bar{y})^2}}", "Statistics"),
    ("mae", "Mean absolute error", r"MAE=\frac{1}{n}\sum|y-\hat{y}|", "Statistics"),
    ("rmse", "Root mean squared error", r"RMSE=\sqrt{\frac{1}{n}\sum(y-\hat{y})^2}", "Statistics"),
    ("r2", "R-squared", r"R^2=1-\frac{\sum(y-\hat{y})^2}{\sum(y-\bar{y})^2}", "Statistics"),
]
