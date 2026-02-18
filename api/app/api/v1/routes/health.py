from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.session import get_db

router = APIRouter()

@router.get("/health")
def health_check(db: Session = Depends(get_db)):
    db_ok = True  # In a real implementation, you would check the database connection here
    
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_ok = False
    
    return {
        "status": "health",
        "environment": settings.ENV,
        "app_version": settings.APP_VERSION,
        "db": "ok" if db_ok else "down",
    }