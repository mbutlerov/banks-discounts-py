from fastapi import APIRouter
from app.api.v1.routes.health import router as health_router
from app.api.v1.routes.promotions import router as promotions_router
from app.api.v1.routes.scraping import router as scraping_router, legacy_router
from app.api.v1.routes.catalogs import router as catalogs_router

router = APIRouter()
router.include_router(health_router)
router.include_router(promotions_router)
router.include_router(scraping_router)
router.include_router(legacy_router)
router.include_router(catalogs_router)
