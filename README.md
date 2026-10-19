# Smart Solar Energy Management — Agentic AI

Cloud-based solar monitoring and decision system. An ESP32 measures the panel and
battery, the readings land in ThingSpeak, and a Groq-powered agent answers questions
about the system in plain language — backed by exact Python calculations, never by
the model's own arithmetic.

Built to the structure of *AI Agent Build Guide — Smart Solar Energy Management System*,
with the ML layer replaced by deterministic solar geometry (see **Forecasting** below).

---

## Quick start

```bash
pip install -r requirements.txt
python run.py
```

Open **http://127.0.0.1:8000**

It runs immediately with a physics-based simulator, so you can build and demo the
whole system before any hardware data arrives. Add your Groq key to enable the agent:

```
GROQ_API_KEY=gsk_...
```

Free key: <https://console.groq.com/keys>

---

## The accuracy guarantee

This is the central design decision. **The language model never does arithmetic.**

```
Question  →  agent picks a tool  →  Python computes the number
                                         ↓
          answer  ←  agent narrates  ←  value + formula + substitution
```

Every tool returns three things together:

| field | example |
|---|---|
| `value` | `42.5` |
| `latex` | `P = V \times I` |
| `substitution` | `P = 18.40 V x 2.31 A = 42.50 W` |

The agent's job is to choose the right tool and explain the result. If a number is not
in a tool result, it is instructed to say so rather than invent one. That is what makes
the answers reproducible and defensible in a viva.

Every answer follows a fixed shape, enforced in the system prompt:

1. **The answer** — direct, with the number and unit.
2. **The table** — whenever there are numbers over time or a comparison.
3. **How this was calculated** — formula, substitution, explanation.
4. **Assumptions** — caveats are never silently dropped.

---

## Forecasting without ML

"How much will we generate tomorrow?" is answered from **solar geometry**, not a
trained model:

```
E_pred = PR × Σ P_STC × G_clear(h)/1000
```

- `G_clear(h)` — clear-sky irradiance from the sun's position for your latitude and
  date. This is exact astronomy, computed from solar declination, the equation of time
  and the hour angle.
- `PR` — the performance ratio **measured on your own recent data**: actual output
  divided by clear-sky theoretical output. It bundles cloud, soiling, shading, wiring
  loss and panel ageing into one empirical number.

This is more defensible than a Random Forest trained on two weeks of data, and it
explains itself. Its honest limitation: it does not know tomorrow's weather. Adding a
free forecast API (Open-Meteo needs no key) would tighten it considerably.

---

## What to ask your friend

The system runs on simulated data until you get these. **The field mapping matters most** —
everything downstream depends on it.

**Connection**
1. ThingSpeak Channel ID, Read API Key, Write API Key
2. Update interval in seconds

**Field mapping** — fill in `THINGSPEAK_FIELDS` in [backend/config.py](backend/config.py)
```
field1 = ?   field2 = ?   field3 = ?   field4 = ?
field5 = ?   field6 = ?   field7 = ?   field8 = ?
```
Ask specifically whether the light sensor sends **raw ADC (0–4095)** or converted lux —
it changes the irradiance calculation.

**Hardware**
3. Is there a current sensor (ACS712 / INA219)? Without it there is no true power
   measurement, only voltage.
4. Is there a relay, and can the ESP32 *receive* a command? If his firmware is
   write-only, the agent is advisory rather than controlling.
5. Battery type, nominal voltage, capacity in Ah.
6. Safe limits: max voltage, max current, battery cutoff. The safety validator needs
   real numbers, not the defaults in this repo.

**Historical data — ask for this first**
7. A CSV export of everything logged so far. Nothing about past-period analysis works
   without history, and that is a wall-clock delay you cannot compress.
8. Timezone of the timestamps.

---

## Switching to real data

One line in `.env`:

```
DATA_SOURCE=thingspeak      # was: simulator
THINGSPEAK_CHANNEL_ID=...
THINGSPEAK_READ_API_KEY=...
```

Or, for a CSV he exports:

```
DATA_SOURCE=csv
CSV_PATH=data/his_export.csv
```

Nothing else changes. Every module talks to `get_latest()` / `get_history()` and does
not know or care where the data came from.

Then update the site and hardware constants in `.env` — latitude, panel rating, battery
capacity — so the physics matches his actual hardware.

---

## Neo4j correlation graph

Optional but recommended. The graph models the system as:

```cypher
(:Parameter {id, label, unit, mean, min, max, std})
    -[:CORRELATES_WITH {r, abs_r, strength, direction, r_squared}]->
(:Parameter)
```

Correlations form a network, not a list. Seeing that light drives power, power drives
battery voltage, and temperature degrades power — all at once, with edge thickness
showing strength — makes the physics legible in a way a 7×7 matrix of numbers does not.

**Without Neo4j** the same correlations are computed with pandas and the graph renders
identically. The dashboard works either way.

**With Neo4j** — free cloud instance at <https://console.neo4j.io> (AuraDB Free):

```
NEO4J_URI=neo4j+s://xxxxx.databases.neo4j.io
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
```

Then press **Sync to Neo4j** on the Correlations tab.

---

## Architecture

```
ESP32 → ThingSpeak → datasource.py ─┬→ analytics.py ─→ formulas.py
                                    │        (exact Python arithmetic)
                                    ├→ policy.py      (deterministic decision)
                                    ├→ policy.py      (safety validator — HARD GATE)
                                    └→ graph_store.py (Neo4j correlations)
                                              ↓
                                          tools.py
                                              ↓
                                     agent.py (Groq tool calling)
                                              ↓
                              validated command → TalkBack → ESP32 relay
```

The safety validator sits **after** the LLM and cannot be influenced by it. A rejected
command is never sent to the hardware.

The deterministic policy engine decides on every tick; the LLM is not in the control
loop. That keeps control fast and predictable, and keeps you inside Groq's free tier
(roughly 1,000 requests/day — an LLM call every 15 seconds would exhaust it before lunch).

---

## Files

| file | role | guide section |
|---|---|---|
| [backend/config.py](backend/config.py) | every threshold and constant, in one place | — |
| [backend/formulas.py](backend/formulas.py) | 20 formulas, each returning value + substitution | 11 |
| [backend/simulator.py](backend/simulator.py) | physics-based data until hardware arrives | — |
| [backend/datasource.py](backend/datasource.py) | simulator / ThingSpeak / CSV, one interface | 2 |
| [backend/analytics.py](backend/analytics.py) | energy, forecast, correlations, fault checks | 6, 13 |
| [backend/policy.py](backend/policy.py) | energy policy + safety validator | 7, 12 |
| [backend/graph_store.py](backend/graph_store.py) | Neo4j knowledge graph | — |
| [backend/tools.py](backend/tools.py) | the agent's toolbox | 5 |
| [backend/agent.py](backend/agent.py) | Groq loop + answer contract | 9, 10, 11 |
| [backend/app.py](backend/app.py) | FastAPI routes | — |
| [web/](web/) | dashboard | 3 |

---

## Dashboard

| tab | shows |
|---|---|
| **Live System** | animated energy flow, battery gauge, safety validation, 24 h charts |
| **Ask the Agent** | natural-language Q&A with tables, formulas and the tool trace |
| **Forecast** | tomorrow's hour-by-hour generation with the maths behind it |
| **Correlations** | the Neo4j force graph — drag nodes, hover for r and R² |
| **Decision Log** | every decision with reason, safety verdict and outcome |
| **Formulas** | all 20 formulas, rendered |

---

## Safety

- The validator is deterministic Python and runs after the LLM. It checks the action
  whitelist, voltage/current/temperature envelope, battery floor and ceiling, data
  freshness, and a relay anti-chatter interval.
- The LLM cannot generate GPIO commands. It can only request one of seven named
  actions, and only `set_load_state` touches hardware — disabled unless explicitly
  enabled per request.
- Use a low-voltage DC load and an appropriately rated relay for the demonstration.
  Do not switch mains without proper protection, isolation and qualified supervision.

---

## Known limits

Stated plainly, because a viva will ask:

- **Irradiance is uncalibrated.** An LDR responds non-linearly to light; the linear
  ADC→W/m² map is a proxy. Every number derived from it is flagged as approximate.
- **SOC is a linear voltage approximation.** Accurate only at rest — it reads low under
  load and high while charging. Coulomb counting would be better.
- **The forecast has no weather input.** It assumes conditions similar to the recent
  average.
- **The agent's live Groq calls are untested here** — the tool-calling loop was verified
  against a mocked client, but no key was available to exercise the real API.
