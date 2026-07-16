from __future__ import annotations

import uvicorn

from control_plane.app_factory import create_app
from control_plane.config import get_settings

app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "control_plane.main:app",
        host="0.0.0.0",
        port=settings.port,
        reload=settings.app_env == "local",
    )


if __name__ == "__main__":
    run()
