"""Run the Siemens NX .prt to glTF web service or command-line converter.

Inputs:
  * Web mode: no arguments, starts FastAPI through Uvicorn.
  * CLI mode: ``python app.py part.prt [destination]``.

Outputs:
  * Web mode serves the FastAPI application as ``app``.
  * CLI mode writes glTF output and returns a process exit code.

The application implementation is organized under ``application/``.
"""
from application.cli import main
from application.web import app


if __name__ == "__main__":
    import sys
    import uvicorn

    if len(sys.argv) > 1:
        sys.exit(main(sys.argv))
    uvicorn.run(app, host="127.0.0.1", port=8000)
