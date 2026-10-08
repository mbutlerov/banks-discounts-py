from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routes import router as v1_router
from app.core.api_access import APIAccessMiddleware
from app.core.config import settings

def create_app() -> FastAPI:
    application = FastAPI(title="Banks Discounts API", version="1.0.0")
    application.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(",")],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    application.add_middleware(APIAccessMiddleware, api_access_key=settings.API_ACCESS_KEY)

    @application.api_route("/healthz", methods=["GET", "HEAD"], include_in_schema=False)
    def liveness() -> JSONResponse:
        return JSONResponse({"status": "ok"}, headers={"Cache-Control": "no-store"})

    application.include_router(v1_router, prefix="/api/v1")
    return application


app = create_app()
