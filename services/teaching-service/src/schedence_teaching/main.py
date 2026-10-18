
import asyncio
from contextlib import asynccontextmanager
import re
from uuid import uuid4

from fastapi import FastAPI, Request

from .api.errors import install_errors
from .api.routes import router
from .api.generations import router as generation_router
from .api.reports import router as report_router


def create_app(runtime=None):
    """Inject trusted test adapters, or configure lazily on first authenticated API use."""
    @asynccontextmanager
    async def lifespan(app):
        if app.state.runtime is not None and app.state.runtime.generations is not None:
            start = getattr(app.state.runtime.generations, "start", None)
            if start is not None:
                start()
        try:
            yield
        finally:
            if app.state.runtime is not None:
                if app.state.runtime.generations is not None:
                    await app.state.runtime.generations.close()
                for close in app.state.runtime.shutdown_hooks:
                    await close()

    application = FastAPI(title="Schedence Teaching Timetable Service", version="0.1.0",
        description="Backend API for teaching timetable generation", lifespan=lifespan,
        swagger_ui_parameters={"persistAuthorization": False})
    application.state.runtime = runtime
    application.state.runtime_lock = asyncio.Lock()
    install_errors(application)

    @application.middleware("http")
    async def response_headers(request: Request, call_next):
        value = request.headers.get("X-Request-ID", "")
        request.state.request_id = value if re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value) else uuid4().hex
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    application.get("/health")(health_check)
    application.include_router(router, prefix="/api/v1/teaching")
    application.include_router(router, prefix="/api/teaching")
    application.include_router(generation_router, prefix="/api/v1/teaching")
    application.include_router(generation_router, prefix="/api/teaching")
    application.include_router(report_router, prefix="/api/v1/teaching")
    application.include_router(report_router, prefix="/api/teaching")
    return application


def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "teaching-timetable",
    }


app = create_app()
