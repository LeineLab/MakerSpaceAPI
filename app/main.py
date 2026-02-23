from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.v1.router import api_router
from app.config import settings
from app.web.router import router as web_router
import pathlib

_STATIC_DIR = pathlib.Path(__file__).parent / "web" / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing needed (SQLAlchemy manages connections lazily)
    yield
    # Shutdown: nothing needed (pool closed by GC)


app = FastAPI(
    title="MakerSpaceAPI",
    description="Unified REST API for makerspace NFC devices and frontend.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

app.add_middleware(
    SessionMiddleware,
    secret_key=settings.SECRET_KEY,
    https_only=not settings.DEBUG,
    same_site="lax",
)

# Static files (JS/CSS served locally)
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# API routes
app.include_router(api_router, prefix="/api/v1")

# Web / frontend routes
app.include_router(web_router)
