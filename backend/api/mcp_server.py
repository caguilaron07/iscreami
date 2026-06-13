"""MCP server for iscreami — exposes recipe calculator tools to AI clients."""
from __future__ import annotations

from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

from api.db import SessionLocal

mcp = FastMCP("iscreami")


@contextmanager
def _db():
    """Open a SQLAlchemy session for use in MCP tool functions.

    MCP tools are not FastAPI route handlers, so Depends(get_db) never fires.
    This replicates the same open/close lifecycle as get_db() in db.py.
    """
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
