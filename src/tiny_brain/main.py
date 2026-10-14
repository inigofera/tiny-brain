from __future__ import annotations

import uvicorn

from .config import Settings
from .web.app import create_app


def run() -> None:
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.app_host, port=settings.app_port)


if __name__ == "__main__":
    run()
