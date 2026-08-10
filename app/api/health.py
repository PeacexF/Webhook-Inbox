from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.deps import DatabaseDep

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
async def ready(db: DatabaseDep) -> JSONResponse:
    try:
        await db.command("ping")
    except Exception:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})
