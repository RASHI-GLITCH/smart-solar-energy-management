"""
FastAPI backend. Deliberately thin -- it routes HTTP to the modules that do the
real work and serves the dashboard. No database, no ORM, no auth layer.
"""
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import agent, analytics, config as C, graph_store, tools as T
from . import formulas as F
from .datasource import get_latest, source_info

app = FastAPI(title="Smart Solar Agentic AI", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


class AskRequest(BaseModel):
    question: str
    history: Optional[List[Dict]] = None
    allow_control: bool = False


class LoadRequest(BaseModel):
    state: int
    reason: str = "manual request from dashboard"


# ---------------------------------------------------------------------------
# Live state
# ---------------------------------------------------------------------------
@app.get("/api/state")
def api_state():
    reading = get_latest()
    if not reading.get("ok"):
        return {"ok": False, "reading": reading}
    derived = analytics.derived_state(reading)
    battery = T.get_battery_status()
    health = analytics.health_check(reading)
    decision = T.recommend_action()
    return {
        "ok": True,
        "reading": reading,
        "derived": derived["values"],
        "formulas": derived["formulas"],
        "battery": battery,
        "health": health,
        "decision": decision["decision"],
        "validation": decision["validation"],
        "source": source_info(),
    }


@app.get("/api/timeseries")
def api_timeseries(hours: int = 24):
    return analytics.timeseries(hours=hours)


@app.get("/api/summary")
def api_summary(period: str = "today"):
    return analytics.energy_summary(period)


@app.get("/api/forecast")
def api_forecast(target: str = "tomorrow", date: str = None):
    return analytics.forecast(target=target, date=date)


@app.get("/api/health-check")
def api_health():
    return analytics.health_check()


# ---------------------------------------------------------------------------
# Neo4j correlation graph
# ---------------------------------------------------------------------------
@app.get("/api/graph")
def api_graph(days: int = 14, refresh: bool = False):
    data = graph_store.get_graph(days=days, refresh=refresh)
    data["neo4j"] = graph_store.status()
    return data


@app.post("/api/graph/sync")
def api_graph_sync(days: int = 14):
    return graph_store.sync(days=days)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------
@app.post("/api/ask")
def api_ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(400, "Question is empty.")
    try:
        return agent.ask(req.question, history=req.history,
                         allow_control=req.allow_control)
    except agent.AgentError as exc:
        raise HTTPException(400, str(exc))
    except Exception as exc:
        raise HTTPException(500, f"{type(exc).__name__}: {exc}")


@app.post("/api/cycle")
def api_cycle(use_llm: bool = False, allow_control: bool = False):
    return agent.decision_cycle(use_llm=use_llm, allow_control=allow_control)


@app.get("/api/decisions")
def api_decisions(limit: int = 20):
    return T.get_decision_log(limit=limit)


@app.post("/api/load")
def api_load(req: LoadRequest):
    return T.set_load_state(req.state, req.reason)


# ---------------------------------------------------------------------------
# Reference
# ---------------------------------------------------------------------------
@app.get("/api/formulas")
def api_formulas():
    return {"catalogue": [{"key": k, "name": n, "latex": l, "category": c}
                          for k, n, l, c in F.CATALOGUE]}


@app.get("/api/config")
def api_config():
    return {
        "source": source_info(),
        "neo4j": graph_store.status(),
        "llm": {"model": C.GROQ_MODEL, "configured": bool(C.GROQ_API_KEY)},
        "safety": C.SAFETY,
        "allowed_actions": C.ALLOWED_ACTIONS,
        "site": {"latitude": C.SITE_LATITUDE, "longitude": C.SITE_LONGITUDE,
                 "panel_rated_w": C.PANEL_RATED_W,
                 "battery_wh": round(C.BATTERY_CAPACITY_AH * C.BATTERY_NOMINAL_V, 1)},
        "suggested_questions": agent.SUGGESTED_QUESTIONS,
    }


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(C.WEB_DIR)), name="static")


@app.get("/")
def index():
    return FileResponse(str(C.WEB_DIR / "index.html"))
