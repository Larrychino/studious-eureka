"""FastAPI application factory."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import admin, core, devices, operations
from .config import Settings, settings as default_settings
from .db import Database
from .worker import tick

log = logging.getLogger("medverify")
STATIC = Path(__file__).parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or default_settings
    database = Database(settings.database_url)
    database.create_all()
    if settings.demo:
        from .seed import seed_demo

        with contextlib.closing(database.session()) as db:
            seed_demo(db)

    async def worker_loop():
        while True:
            await asyncio.sleep(settings.worker_interval_s)
            try:
                await asyncio.to_thread(_run_tick, database)
            except Exception:  # keep the loop alive; errors are logged
                log.exception("worker tick failed")

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(worker_loop()) if settings.worker else None
        yield
        if task:
            task.cancel()

    app = FastAPI(title="MedVerify", version=__version__, lifespan=lifespan,
                  description="Physical verification layer for medicines in care homes.")
    app.state.db = database
    app.state.settings = settings
    for module in (core, operations, devices, admin):
        app.include_router(module.router)

    @app.get("/api/health")
    def health():
        return {"ok": True, "version": __version__, "demo": settings.demo}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app


def _run_tick(database: Database) -> None:
    with contextlib.closing(database.session()) as db:
        tick(db)
