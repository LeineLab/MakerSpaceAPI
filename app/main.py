from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
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

_LOGIN_REQUIRED_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Login Required – MakerSpaceAPI</title>
  <link rel="stylesheet" href="/static/css/tailwind.css">
</head>
<body class="bg-gray-50 text-gray-900 min-h-screen flex items-center justify-center">
  <div class="text-center space-y-4">
    <h1 class="text-2xl font-bold">Login Required</h1>
    <p class="text-gray-500">You need to be logged in to access this page.</p>
    <a href="/auth/login"
       class="inline-block px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700">
      Log in
    </a>
  </div>
</body>
</html>
"""


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse | HTMLResponse:
    """Show a login-required page for unauthenticated web requests; return JSON for API routes."""
    if exc.status_code in (401, 403) and not request.url.path.startswith("/api/"):
        return HTMLResponse(content=_LOGIN_REQUIRED_HTML, status_code=exc.status_code)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


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
