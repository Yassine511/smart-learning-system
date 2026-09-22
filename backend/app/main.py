from fastapi import FastAPI, Response, status
from fastapi.concurrency import run_in_threadpool

from app.api.auth import router as auth_router
from app.api.conversations import router as conversations_router
from app.api.documents import router as documents_router
from app.api.messages import router as messages_router
from app.config import get_settings
from app.services.readiness import probe_dependencies

settings = get_settings()

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    docs_url="/api/docs" if settings.app_env == "development" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.app_env == "development" else None,
)

app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(documents_router)
app.include_router(messages_router)


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    """Process liveness check; it does not depend on external services."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": settings.app_version,
        "environment": settings.app_env,
    }


@app.get("/api/ready", tags=["system"])
async def readiness(response: Response) -> dict[str, object]:
    """Report whether the API can reach all phase-one dependencies."""
    components = await run_in_threadpool(probe_dependencies)
    ready = all(component.ok for component in components.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready else "not_ready",
        "components": {
            name: component.as_dict() for name, component in components.items()
        },
    }
