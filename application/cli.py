"""Command-line entrypoint for converting local Siemens NX .prt files.

Inputs:
  * ``python app.py part.prt [destination]`` — convert to glTF/GLB.
  * ``python app.py render part.prt [options]`` — render to PNG.
  * ``python app.py render part.glb [options]`` — render an existing model,
    skipping conversion.
  * ``python app.py render part.gltf [options]`` — render an existing glTF
    (and its sibling .bin/textures) without conversion.
  * ``python app.py info part.prt [--json] [--preview-out PATH]`` — show the
    embedded JPEG preview, attributes, and arrangements.

Outputs:
  * A ``.gltf`` and ``.bin`` pair by default.
  * A single ``.glb`` when the destination ends in ``.glb``.
  * A ``.png`` screenshot when using the render subcommand.
  * Part metadata as text or JSON, and optionally a saved JPEG preview.
"""
from __future__ import annotations

import base64
import http.server
import json
import socketserver
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlencode

from . import config as _config  # noqa: F401
from .conversion import convert_to_files, convert_to_glb
from .part_info import read_part_info
from .reference_index import initialize_reference_index


MODEL_SUFFIXES = (".gltf", ".glb")


def _parse_camera(cam_str: str) -> tuple[float, float, float, float, float, float]:
    """Parse camera string 'x,y,z,roll,pitch,yaw' into six floats."""
    parts = cam_str.split(',')
    if len(parts) != 6:
        raise ValueError("camera must be 'x,y,z,roll,pitch,yaw' (6 comma-separated values)")
    return tuple(float(p) for p in parts)


@contextmanager
def _render_model_path(source_path: Path):
    """Yield ``(model_path, stats)``, converting .prt only when needed.

    Existing ``.glb``/``.gltf`` files are passed through untouched so their
    sibling ``.bin`` and texture files can be served alongside them. ``stats``
    is ``None`` for pass-through sources and the conversion statistics for
    ``.prt`` inputs.

    > example
    > with _render_model_path(Path("cap.glb")) as (path, stats):
    ...     path.name, stats -> ("cap.glb", None)
    """
    if source_path.suffix.lower() in MODEL_SUFFIXES:
        yield source_path, None
        return
    print("converting %s to GLB..." % source_path.name)
    initialize_reference_index(_config.BASE_DIR)
    glb_bytes, stats = convert_to_glb(
        source_path.read_bytes(), source_path.name,
        source_dir=source_path.parent)
    with tempfile.TemporaryDirectory() as temp_dir:
        model_path = Path(temp_dir) / "model.glb"
        model_path.write_bytes(glb_bytes)
        yield model_path, stats


def _render_with_playwright(
    model_path: Path,
    output_png: Path,
    camera: tuple[float, float, float, float, float, float],
    width: int,
    height: int,
    base_dir: Path,
) -> None:
    """Render a GLB/glTF model to PNG using Playwright and a local HTTP server."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise RuntimeError("playwright not installed. Run: uv pip install playwright && playwright install chromium") from e

    cx, cy, cz, roll, pitch, yaw = camera
    render_page = base_dir / "static" / "render_page.html"
    if not render_page.is_file():
        raise FileNotFoundError(f"render page not found: {render_page}")

    # Serve the model's folder so .gltf files can reach sibling .bin/textures
    # (avoids file:// CORS issues).
    model_path = model_path.resolve()
    model_dir = model_path.parent

    class ModelHandler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(model_dir), **kwargs)

        def end_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            super().end_headers()

        def log_message(self, format, *args):
            pass  # Suppress log messages

    # Find a free port
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]

    server = socketserver.TCPServer(("127.0.0.1", port), ModelHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    try:
        model_url = f"http://localhost:{port}/{quote(model_path.name)}"
        page_url = render_page.resolve().as_uri() + "?" + urlencode({
            "cam": f"{cx},{cy},{cz},{roll},{pitch},{yaw}",
            "model": model_url,
            "width": str(width),
            "height": str(height),
        })

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.set_viewport_size({"width": width, "height": height})
                page.goto(page_url, wait_until="networkidle")

                # Wait for renderReady signal from the page
                page.wait_for_function("() => window.renderReady === true", timeout=60000)

                # Check for errors
                error = page.evaluate("() => window.renderError")
                if error:
                    raise RuntimeError(f"Render page error: {error}")

                # Screenshot
                page.screenshot(path=str(output_png), full_page=False)
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()


def _cmd_convert(argv: list[str]) -> int:
    """Handle the convert (default) subcommand."""
    if len(argv) == 2:
        source, output = argv[1], None
    elif len(argv) == 3:
        source, output = argv[1:]
    else:
        print(__doc__.strip())
        return 2
    source_path = Path(source)
    if not source_path.is_file():
        print("input file not found: %s" % source)
        return 2
    output = output or source_path.with_suffix(".gltf")
    try:
        initialize_reference_index(_config.BASE_DIR)
        written, points, triangles, meshes = convert_to_files(source, output)
    except Exception as error:
        print("conversion failed: %s" % error)
        return 1
    for path in written:
        print("wrote %s (%d B)" % (path, path.stat().st_size))
    print("meshes=%d pts=%d tris=%d" % (meshes, points, triangles))
    return 0


def _cmd_render(argv: list[str]) -> int:
    """Handle the render subcommand.

    Usage: python app.py render model [--camera x,y,z,roll,pitch,yaw] [--output out.png] [--width W] [--height H]

    ``model`` may be a .prt file (converted first) or an existing .glb/.gltf
    file (rendered directly).
    """
    import argparse

    parser = argparse.ArgumentParser(prog="app.py render", description="Render a .prt, .glb, or .gltf to PNG")
    parser.add_argument("source", help="Input .prt file, or .glb/.gltf to render directly")
    parser.add_argument(
        "--camera",
        default="0,0,0,0,0,0",
        help="Camera x,y,z,roll,pitch,yaw in radians; zero position auto-fits (default)",
    )
    parser.add_argument("--output", help="Output PNG path (default: temp/<source>_<timestamp>.png)")
    parser.add_argument("--width", type=int, default=1920, help="Render width (default: 1920)")
    parser.add_argument("--height", type=int, default=1080, help="Render height (default: 1080)")
    args = parser.parse_args(argv[2:])

    source_path = Path(args.source)
    if not source_path.is_file():
        print("input file not found: %s" % args.source)
        return 2

    try:
        camera = _parse_camera(args.camera)
    except ValueError as e:
        print("invalid camera: %s" % e)
        return 2

    # Default output to temp/<stem>_<timestamp>.png
    if args.output:
        output_png = Path(args.output)
    else:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_png = _config.BASE_DIR / "temp" / f"{source_path.stem}_{timestamp}.png"

    output_png.parent.mkdir(parents=True, exist_ok=True)

    try:
        with _render_model_path(source_path) as (model_path, stats):
            if stats is None:
                print("using %s directly (skipping .prt conversion)" % source_path.name)
            else:
                print("meshes=%d pts=%d tris=%d" % (stats["meshes"], stats["points"], stats["triangles"]))
                result = stats.get("result") or {}
                if result.get("status") == "drawings-only":
                    print("drawings-only part: no 3D geometry to render; "
                          "use `app.py info` for the sheet preview")
                    return 0

            print("rendering to %s (%dx%d)..." % (output_png, args.width, args.height))
            _render_with_playwright(model_path, output_png, camera, args.width, args.height, _config.BASE_DIR)
    except Exception as error:
        print("render failed: %s" % error)
        return 1

    print("wrote %s (%d B)" % (output_png, output_png.stat().st_size))

    return 0


def _cmd_info(argv: list[str]) -> int:
    """Handle the info subcommand.

    Usage: python app.py info part.prt [--json] [--preview-out PATH]
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="app.py info",
        description="Show the embedded preview and attributes of a .prt")
    parser.add_argument("source", help="Input .prt file")
    parser.add_argument("--json", action="store_true",
                        help="Print metadata as JSON")
    parser.add_argument("--preview-out", metavar="PATH",
                        help="Write the embedded JPEG preview to PATH")
    args = parser.parse_args(argv[2:])

    source_path = Path(args.source)
    if not source_path.is_file():
        print("input file not found: %s" % args.source)
        return 2

    info = read_part_info(source_path)
    preview = info.get("preview")
    notice = sys.stderr if args.json else sys.stdout

    if args.preview_out:
        if preview is None:
            print("no preview available", file=notice)
        else:
            preview_path = Path(args.preview_out)
            preview_path.parent.mkdir(parents=True, exist_ok=True)
            preview_path.write_bytes(preview["data"])
            print("wrote preview %s (%d B)"
                  % (preview_path, preview["byte_size"]), file=notice)

    if args.json:
        payload = {
            "file": source_path.name,
            "attributes": info.get("attributes", []),
            "preview": None,
            "arrangements": info.get("arrangements", []),
            "references": info.get("references", []),
        }
        if preview is not None:
            payload["preview"] = {
                "media_type": preview["media_type"],
                "width": preview["width"],
                "height": preview["height"],
                "byte_size": preview["byte_size"],
                "data_base64": base64.b64encode(
                    preview["data"]).decode("ascii"),
            }
        if "units" in info:
            payload["units"] = info["units"]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(source_path.name)
    if preview is None:
        print("preview: none")
    else:
        print("preview: %dx%d, %d B, %s"
              % (preview["width"], preview["height"],
                 preview["byte_size"], preview["media_type"]))
    arrangements = info.get("arrangements") or []
    if arrangements:
        rendered = ", ".join(
            item["name"] + (" (default)" if item["default"] else "")
            for item in arrangements)
        print("arrangements: %s" % rendered)
    references = info.get("references") or []
    if references:
        rendered = ", ".join(
            item["name"]
            + (" [%s]" % item["directory"] if item.get("directory") else "")
            for item in references)
        print("references: %s" % rendered)
    for attribute in info.get("attributes", []):
        print("%s = %s" % (attribute["title"], attribute["value"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code.

    > example
    > main(["app.py", "cap.prt", "cap.glb"])
      -> 0 after writing cap.glb
    > main(["app.py", "render", "cap.prt", "--camera", "4,3,6,0,0,0", "--output", "cap.png"])
      -> 0 after writing cap.png
    > main(["app.py", "info", "cap.prt", "--json"])
      -> 0 after printing the part metadata as JSON
    """
    argv = sys.argv if argv is None else argv
    if len(argv) < 2 or argv[1] in ("-h", "--help", "help"):
        print(__doc__.strip())
        return 0 if len(argv) >= 2 else 2

    subcommand = argv[1]
    if subcommand == "render":
        return _cmd_render(argv)
    elif subcommand == "info":
        return _cmd_info(argv)
    else:
        # Default: convert
        return _cmd_convert(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
