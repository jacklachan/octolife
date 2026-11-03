from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import router
from app.config import settings
from app.database import SessionLocal, init_engine
from app.models import ensure_user
from app.orchestration.scheduler import start_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_engine(settings.database_url)
    with SessionLocal() as s:
        ensure_user(s)
    sched = start_scheduler() if settings.scheduler_enabled else None
    yield
    if sched:
        sched.shutdown(wait=False)


app = FastAPI(title="OctoLife", version="0.1.0", lifespan=lifespan)
app.include_router(router)
