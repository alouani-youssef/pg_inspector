from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


async def http_error(request: Request, exc: Exception) -> Response:
    """Return JSON for unknown routes (404), wrong methods (405) and other HTTP errors."""
    assert isinstance(exc, HTTPException)
    if exc.status_code == 404:
        message = f"Route {request.url.path!r} not found"
    elif exc.status_code == 405:
        message = f"Method {request.method} not allowed on {request.url.path!r}"
    else:
        message = exc.detail
    return JSONResponse(
        {"error": {"status": exc.status_code, "message": message}},
        status_code=exc.status_code,
        headers=exc.headers,  # keeps the Allow header on 405
    )
