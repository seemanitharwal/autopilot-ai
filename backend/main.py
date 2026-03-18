"""AutoPilot AI — Entry Point

Usage:
    cd backend
    uvicorn main:app --host 0.0.0.0 --port 8080 --reload
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.server import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8080)),
        reload=os.environ.get("ENV", "development") == "development",
        log_level="info",
    )