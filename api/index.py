"""Vercel serverless entry point — exposes the FastAPI ASGI app."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

from api.app import app  # noqa: E402
