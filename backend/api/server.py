"""AutoPilot AI — FastAPI Server"""
from __future__ import annotations

import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents.agent_controller import AgentController

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

SCREENSHOT_DIR = Path("screenshots")
SCREENSHOT_DIR.mkdir(exist_ok=True)


# ── Thread-safe session store ─────────────────────────────────────────────────

_SESSIONS: Dict[str, Dict[str, Any]] = {}
_LOCK = RLock()


def _get(sid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        return _SESSIONS.get(sid)


def _set(sid: str, data: Dict[str, Any]) -> None:
    with _LOCK:
        _SESSIONS[sid] = data


def _all() -> List[Dict[str, Any]]:
    with _LOCK:
        return list(_SESSIONS.values())


def _clear() -> int:
    with _LOCK:
        n = len(_SESSIONS)
        _SESSIONS.clear()
        return n


# ── App setup ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AutoPilot AI starting up")
    yield
    logger.info("AutoPilot AI shutting down")


app = FastAPI(title="AutoPilot AI", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(SCREENSHOT_DIR)), name="static")


# ── Schemas ───────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=500)
    # ── FIX: Raised default max_steps from 8 → 15 ──
    # Amazon and Myntra need many more steps than books.toscrape.com
    max_steps: Optional[int] = Field(default=15, ge=1, le=20)
    headless:  bool = True


class RunResponse(BaseModel):
    session_id: str
    status:     str
    message:    str


# ── Background task ───────────────────────────────────────────────────────────

def _run_task(session_id: str, req: RunRequest) -> None:
    logger.info("[%s] Background task starting", session_id)

    _set(session_id, {
        "session_id":  session_id,
        "status":      "running",
        "step_count":  0,
        "steps":       [],
        "result":      None,
        "error":       None,
        "goal":        req.instruction,
        "final_url":   "",
        "final_title": "",
    })

    def on_step(session_dict: Dict[str, Any]) -> None:
        _set(session_id, session_dict)

    try:
        controller = AgentController(headless=req.headless)
        result = controller.run(
            user_instruction=req.instruction,
            max_steps=req.max_steps,
            session_id=session_id,
            step_callback=on_step,
        )
        _set(session_id, result.to_dict())
        logger.info("[%s] Task complete: status=%s", session_id, result.status)

    except Exception as e:
        logger.exception("[%s] Task crashed: %s", session_id, e)
        _set(session_id, {
            "session_id": session_id,
            "status":     "failed",
            "error":      str(e),
            "step_count": 0,
            "steps":      [],
            "result":     None,
            "goal":       req.instruction,
        })


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {"service": "AutoPilot AI", "status": "online", "docs": "/docs"}


@app.get("/health")
def health():
    return {
        "status":     "ok",
        "gemini_key": bool(os.environ.get("GEMINI_API_KEY")),
        "sessions":   len(_all()),
    }


@app.post("/run", response_model=RunResponse)
def run_agent(req: RunRequest, background_tasks: BackgroundTasks):
    """Start async agent. Poll /status/{session_id} for live step updates."""
    session_id = str(uuid.uuid4())
    _set(session_id, {
        "session_id": session_id,
        "status":     "queued",
        "steps":      [],
        "result":     None,
        "goal":       req.instruction,
    })
    background_tasks.add_task(_run_task, session_id, req)
    logger.info("Queued session %s: %r", session_id, req.instruction[:60])
    return RunResponse(
        session_id=session_id,
        status="queued",
        message="Agent started. Poll /status/{session_id} for live updates.",
    )


@app.post("/run/sync")
def run_sync(req: RunRequest):
    """Blocking run — for testing only."""
    sid = str(uuid.uuid4())
    controller = AgentController(headless=req.headless)
    result = controller.run(
        user_instruction=req.instruction,
        max_steps=req.max_steps,
        session_id=sid,
    )
    return result.to_dict()


@app.get("/status/{session_id}")
def get_status(session_id: str):
    """Poll this endpoint every second to get live step updates."""
    data = _get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    return data


@app.get("/sessions")
def list_sessions():
    sessions = _all()
    return {"total": len(sessions), "sessions": sessions}


@app.delete("/sessions")
def clear_sessions():
    return {"ok": True, "cleared": _clear()}


@app.post("/sessions/{session_id}/cancel")
def cancel_session(session_id: str):
    data = _get(session_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Session '{session_id}' not found.")
    if data.get("status") not in ("done", "failed", "stopped"):
        data["status"] = "cancelled"
        _set(session_id, data)
    return {"ok": True}