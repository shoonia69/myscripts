from __future__ import annotations

import asyncio
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Awaitable, Callable

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from .config import Settings
from .identity import ClientIdentity, build_identity
from .three_x_ui import PanelError, ThreeXUIClient

Provisioner = Callable[[ClientIdentity], Awaitable[str]]
BASE_DIR = Path(__file__).resolve().parent


class RateLimiter:
    def __init__(self, limit: int, window_seconds: int = 3600) -> None:
        self.limit = limit
        self.window = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> bool:
        now = time.monotonic()
        async with self._lock:
            events = self._events[key]
            while events and now - events[0] >= self.window:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


def create_app(settings: Settings, provisioner: Provisioner | None = None) -> FastAPI:
    if len(settings.app_secret) < 32:
        raise ValueError("APP_SECRET должен содержать не менее 32 символов")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if provisioner is not None:
            app.state.provisioner = provisioner
            yield
            return
        async with httpx.AsyncClient(
            verify=settings.verify_tls,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        ) as http:
            panel = ThreeXUIClient(settings, http)
            app.state.provisioner = panel.provision
            yield

    app = FastAPI(title=settings.site_title, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.app_secret,
        same_site="lax",
        https_only=False,
        max_age=1800,
    )
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    limiter = RateLimiter(settings.rate_limit_per_hour)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; form-action 'self'; frame-ancestors 'none'"
        )
        return response

    def render(request: Request, *, status: int = 200, **context: object) -> HTMLResponse:
        values = {"request": request, "site_title": settings.site_title, **context}
        return templates.TemplateResponse(request, "index.html", values, status_code=status)

    @app.get("/", response_class=HTMLResponse)
    async def form_page(request: Request) -> HTMLResponse:
        token = secrets.token_urlsafe(32)
        request.session["csrf"] = token
        return render(request, csrf_token=token)

    @app.post("/", response_class=HTMLResponse)
    async def submit(
        request: Request,
        name: str = Form(""),
        csrf_token: str = Form(""),
        website: str = Form(""),
    ) -> HTMLResponse:
        expected = request.session.pop("csrf", "")
        if not expected or not secrets.compare_digest(expected, csrf_token):
            return render(request, status=403, error="Форма устарела. Обновите страницу и попробуйте снова.", csrf_token="")
        if website:
            return render(request, status=400, error="Некорректный запрос.", csrf_token="")
        client_host = request.client.host if request.client else "unknown"
        if not await limiter.allow(client_host):
            return render(request, status=429, error="Слишком много запросов. Попробуйте позже.", csrf_token="")
        try:
            identity = build_identity(name, settings.app_secret)
            subscription_url = await request.app.state.provisioner(identity)
        except ValueError as exc:
            token = secrets.token_urlsafe(32)
            request.session["csrf"] = token
            return render(request, status=422, error=str(exc), name=name, csrf_token=token)
        except (PanelError, httpx.HTTPError):
            token = secrets.token_urlsafe(32)
            request.session["csrf"] = token
            return render(
                request,
                status=502,
                error="Не удалось создать доступ. Свяжитесь с администратором.",
                name=name,
                csrf_token=token,
            )
        return render(request, name=identity.comment, subscription_url=subscription_url, csrf_token="")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app
