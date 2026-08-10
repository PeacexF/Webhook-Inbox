from pathlib import Path

from fastapi import APIRouter, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.deps import DatabaseDep

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

PAGE_SIZE = 50


@router.get("/")
async def index() -> RedirectResponse:
    return RedirectResponse("/events")


@router.get("/events")
async def events(request: Request, db: DatabaseDep) -> Response:
    cursor = db.events.find().sort("received_at", -1).limit(PAGE_SIZE)
    rows = [doc async for doc in cursor]
    total = await db.events.count_documents({})
    return templates.TemplateResponse(request, "events.html", {"events": rows, "total": total})
