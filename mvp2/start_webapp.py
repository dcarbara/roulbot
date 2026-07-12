import threading
import time
import webview
import uvicorn
import logging
import os
import subprocess
from api.server import app

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Determine if we're in dev mode (Vite dev server) or prod mode (built dist)
DEV_MODE = os.environ.get("SPINEDGE_DEV", "0") == "1"
FRONTEND_DIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frontend", "dist")

# In production, serve the React build from FastAPI
if not DEV_MODE and os.path.exists(FRONTEND_DIST):
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    INDEX_HTML = os.path.join(FRONTEND_DIST, "index.html")

    class SPAMiddleware(BaseHTTPMiddleware):
        """Serve index.html for any non-API, non-asset request (SPA fallback)."""
        async def dispatch(self, request: Request, call_next):
            path = request.url.path
            # Let API and WebSocket routes pass through
            if path.startswith("/api"):
                return await call_next(request)
            # Let static assets pass through
            if path.startswith("/assets"):
                return await call_next(request)
            # Serve index.html for everything else (SPA routing)
            return FileResponse(INDEX_HTML)

    # Add middleware BEFORE routes are checked
    app.add_middleware(SPAMiddleware)

    # Mount static assets directory
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")), name="static-assets")


def start_api_server():
    """Runs the FastAPI backend."""
    logger.info("Starting up native Engine API on port 8000...")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")

if __name__ == '__main__':
    # 1. Spin up the Python Backend API in a background thread
    server_thread = threading.Thread(target=start_api_server, daemon=True)
    server_thread.start()

    # Give uvicorn a brief moment to bind to port 8000
    time.sleep(1.5)

    # 1.5 Spin up the actual SpinEdge engine in headless mode in the background
    logger.info("Starting up Engine Automator natively in background...")
    env = os.environ.copy()
    env["SPINEDGE_HEADLESS"] = "1"

    def read_engine_logs(process):
        import requests
        for line in iter(process.stdout.readline, ''):
            if line:
                try:
                    requests.post("http://localhost:8000/api/internal/log", json={"message": line.strip()}, timeout=1)
                except Exception:
                    pass

    engine_process = subprocess.Popen(
        ["python", "main.py"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    threading.Thread(target=read_engine_logs, args=(engine_process,), daemon=True).start()

    # 2. Spin up the Native Window Wrapper
    if DEV_MODE:
        url = "http://localhost:5173"
        logger.info("DEV MODE — pointing to Vite dev server at :5173")
    else:
        url = "http://localhost:8000"
        logger.info("PROD MODE — serving built React app from FastAPI")

    logger.info("Starting PyWebView wrapper...")
    window = webview.create_window(
        'SpinEdge v2',
        url=url,
        width=1280,
        height=800,
        background_color='#09090B',
        min_size=(1024, 600)
    )

    webview.start(debug=DEV_MODE)
