"""Route registration for the capstone API."""

from fastapi import FastAPI

from building_with_rag.routes import chat, health, models, query


def register_routes(app: FastAPI) -> None:
    app.include_router(health.router)
    app.include_router(query.router)
    app.include_router(models.router)
    app.include_router(chat.router)
