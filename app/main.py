import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.healthchecks import ping_healthchecks
from app.core.logging import configure_logging
from app.routers import (
    admin,
    auth,
    broker_credentials,
    events,
    internal_bridge,
    internal_decommission,
    internal_provisioning,
    model_configs,
    models,
    settings as settings_router,
    trades,
    trading,
)

configure_logging()
logger = logging.getLogger("app")

# Monitoring/alerting (logging/audit review part 3, "process/service down"
# trigger): pings healthchecks.io on an interval for as long as this
# process is alive and its event loop is responsive -- see
# app.core.healthchecks' module docstring for why an external
# dead-man's-switch, not a same-VPS watcher. Dormant (no real network
# calls) until HEALTHCHECKS_PING_URL is set -- see ping_healthchecks().
_HEARTBEAT_INTERVAL_SECONDS = 60


async def _heartbeat_loop() -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_SECONDS)
        # ping_healthchecks() is a blocking `requests` call -- run it off
        # the event loop thread so a slow/hung healthchecks.io request
        # can never stall request handling.
        await asyncio.to_thread(ping_healthchecks)


@asynccontextmanager
async def lifespan(app: FastAPI):
    heartbeat_task = asyncio.create_task(_heartbeat_loop())
    yield
    heartbeat_task.cancel()


app = FastAPI(title="SMC/ICT Live Bot -- Phase 0 (Foundation)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router)
app.include_router(auth.router)
app.include_router(broker_credentials.router)
app.include_router(events.router)
app.include_router(internal_bridge.router)
app.include_router(internal_provisioning.router)
app.include_router(internal_decommission.router)
app.include_router(trades.router)
app.include_router(model_configs.router)
app.include_router(models.router)
app.include_router(settings_router.router)
app.include_router(trading.router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception on %s %s", request.method, request.url.path, exc_info=exc
    )
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        logger.exception("Health check failed: database unreachable")
        return JSONResponse(status_code=503, content={"status": "unhealthy"})
    return {"status": "ok"}
