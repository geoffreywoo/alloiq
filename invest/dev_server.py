from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import os


class CleanUrlHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        route = path.split("?", 1)[0].split("#", 1)[0]
        if route != "/" and "." not in Path(route).name:
            html_path = Path("." + route + ".html")
            if html_path.exists():
                path = route + ".html"
        return super().translate_path(path)


def main() -> None:
    os.chdir("web")
    server = ThreadingHTTPServer(("", 4173), CleanUrlHandler)
    print("Serving AlloIQ on http://127.0.0.1:4173")
    server.serve_forever()


if __name__ == "__main__":
    main()
