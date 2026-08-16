"""Entry Point des Containers: startet Uvicorn (mit Auto-Reload bei Code-Änderungen).

Der Procrastinate-Worker läuft NICHT als separater Prozess, sondern als Hintergrund-Task
innerhalb des FastAPI-Lifespans (siehe app/main.py) - dadurch startet ein Uvicorn-Reload
automatisch auch den Worker neu, und es bleibt bei "ein Container, ein Prozess".
"""
from __future__ import annotations

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_dirs=["app"] if settings.reload else None,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
