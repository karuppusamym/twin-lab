"""`python -m lab_agent`: run the agent with settings from the environment."""

from __future__ import annotations

import logging
import os

import uvicorn

from lab_agent.app import create_app
from lab_agent.config import Settings


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    agent = create_app(settings)
    uvicorn.run(
        agent,
        host=os.environ.get("LAB_HOST", "0.0.0.0"),
        port=settings.lab_port,
        lifespan="on",
        proxy_headers=False,
        server_header=False,
        date_header=False,
        access_log=os.environ.get("ACCESS_LOG", "0") == "1",
        timeout_keep_alive=30,
        log_config=None,
    )


if __name__ == "__main__":
    main()
