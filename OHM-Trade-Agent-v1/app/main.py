from fastapi import FastAPI

from app.api.routes import router

app = FastAPI(
    title="OHM Trade Agent v1",
    version="0.1.0",
    description="Alert-only trade signal scoring and risk-validation service.",
)
app.include_router(router)
