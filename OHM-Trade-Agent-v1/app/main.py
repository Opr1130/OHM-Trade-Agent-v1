from fastapi import FastAPI

from app.api.routes import router
from app.services.telegram_callback_listener import (
    start_telegram_callback_listener,
    stop_telegram_callback_listener,
)

app = FastAPI(
    title="OHM Trade Agent v1",
    version="0.1.0",
    description="Alert-only trade signal scoring and risk-validation service.",
)

app.include_router(router)


@app.on_event("startup")
def startup_event() -> None:
    start_telegram_callback_listener()


@app.on_event("shutdown")
def shutdown_event() -> None:
    stop_telegram_callback_listener()
