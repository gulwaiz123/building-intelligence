"""FastAPI application factory."""

from fastapi import FastAPI

from building_with_rag.routes import register_routes


def create_app() -> FastAPI:
    app = FastAPI(title="Building Intelligence with RAG", version="0.1.0")
    register_routes(app)
    return app


app = create_app()
