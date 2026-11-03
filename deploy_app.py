"""Render-ready wrapper around V10 FastAPI app.

Optional password protection: Set DASHBOARD_PASSWORD in Render > Environment.
The browser will request HTTP Basic username 'viewer' and that password.
/health remains public for platform health checks, with no stock data.
"""
import base64
import os
import secrets
from fastapi import Request
from fastapi.responses import JSONResponse
from app import app


@app.middleware("http")
async def optional_password_protection(request: Request, call_next):
    password = os.environ.get("DASHBOARD_PASSWORD", "")
    if password and request.url.path != "/health":
        header = request.headers.get("authorization", "")
        authorized = False
        if header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
                username, received_password = decoded.split(":", 1)
                authorized = (
                    secrets.compare_digest(username, "viewer")
                    and secrets.compare_digest(received_password, password)
                )
            except (ValueError, UnicodeDecodeError):
                pass
        if not authorized:
            return JSONResponse(
                status_code=401,
                content={"detail": "请登录后查看股息率工具"},
                headers={"WWW-Authenticate": 'Basic realm="Dividend Watch"'},
            )
    return await call_next(request)


@app.get("/health", include_in_schema=False)
def health():
    return {"ok": True, "service": "dividend-watch-v10"}
