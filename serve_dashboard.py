"""Serve the dashboard and its simulation/optimization APIs."""

from __future__ import annotations

import json
import socketserver
import traceback
import urllib.parse
from http.server import SimpleHTTPRequestHandler

from dashboard_data import build_dashboard_payload, build_optimization_payload

PORT = 8080

# Cache optimization results for the lifetime of the server.
_optimize_cache: dict = {}


def _bool_param(query: dict, name: str, default: bool) -> bool:
    raw = query.get(name, [str(default).lower()])[0].strip().lower()
    return raw not in ("false", "0", "no")


class DashboardHandler(SimpleHTTPRequestHandler):
    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)

        if parsed.path == "/api/dashboard":
            scenario = query.get("scenario", ["Normal"])[0]
            if scenario not in ("Normal", "Worst"):
                scenario = "Normal"
            include_events = _bool_param(query, "include_events", True)

            try:
                payload = build_dashboard_payload(
                    scenario=scenario, include_events=include_events
                )
                self._send_json(payload)
            except Exception as exc:  # dataset loi, ... - tra loi ro rang thay vi treo
                tb = traceback.format_exc()
                print(tb)
                self._send_json({"error": str(exc)}, status=500)
            return

        if parsed.path == "/api/optimize":
            scenario = query.get("scenario", ["Normal"])[0]
            if scenario not in ("Normal", "Worst"):
                scenario = "Normal"
            include_events = _bool_param(query, "include_events", True)
            try:
                max_moves = int(query.get("max_moves", ["3"])[0])
            except ValueError:
                max_moves = 3

            cache_key = (scenario, include_events, max_moves)
            if cache_key not in _optimize_cache:
                print(
                    f"[optimize] dang chay optimize_multi_move cho {cache_key} "
                    "(lan dau se mat vai chuc giay)..."
                )
                try:
                    _optimize_cache[cache_key] = build_optimization_payload(
                        scenario=scenario,
                        include_events=include_events,
                        max_moves=max_moves,
                    )
                except Exception as exc:
                    tb = traceback.format_exc()
                    print(tb)
                    self._send_json({"available": False, "reason": str(exc)}, status=500)
                    return
                print(f"[optimize] xong, da cache {cache_key}")
            else:
                print(f"[optimize] tra tu cache cho {cache_key}")

            self._send_json(_optimize_cache[cache_key])
            return

        super().do_GET()


class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True


if __name__ == "__main__":
    with ReusableTCPServer(("", PORT), DashboardHandler) as httpd:
        print(f"Server dashboard dang chay tai: http://localhost:{PORT}")
        print("Mo trinh duyet truy cap duong dan tren de xem Dashboard.")
        print("Lan toi uu dau tien co the mat 10-15 giay; cac lan sau dung cache.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nDa dung server.")
