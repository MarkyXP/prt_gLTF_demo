"""Run the Siemens NX .prt to glTF web service or command-line converter.

Inputs:
  * Web mode: no arguments, starts FastAPI through Uvicorn.
  * CLI mode: ``python app.py part.prt [destination]``.

Outputs:
  * Web mode serves the FastAPI application as ``app``.
  * CLI mode writes glTF output and returns a process exit code.

The application implementation is organized under ``application/``.
"""
import copy
import logging
import os


class _ConsoleFormatter(logging.Formatter):
    """Format application logs with timestamps and terminal level colors."""

    _COLORS = {
        logging.DEBUG: "\033[36m",
        logging.INFO: "\033[32m",
        logging.WARNING: "\033[33m",
        logging.ERROR: "\033[31m",
        logging.CRITICAL: "\033[35m",
    }
    _RESET = "\033[0m"

    def __init__(self, stream):
        super().__init__(
            "%(asctime)s.%(msecs)03d %(levelname)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._use_color = stream.isatty() and "NO_COLOR" not in os.environ

    def format(self, record):
        if not self._use_color:
            return super().format(record)
        colored = copy.copy(record)
        color = self._COLORS.get(colored.levelno, "")
        colored.levelname = "%s%s%s" % (
            color, colored.levelname, self._RESET)
        return super().format(colored)


_handler = logging.StreamHandler()
_handler.setFormatter(_ConsoleFormatter(_handler.stream))
logging.basicConfig(level=logging.INFO, handlers=[_handler])

from application.cli import main
from application.web import app


if __name__ == "__main__":
    import sys
    import uvicorn

    if len(sys.argv) > 1:
        sys.exit(main(sys.argv))
    uvicorn.run(app, host="127.0.0.1", port=8000)
