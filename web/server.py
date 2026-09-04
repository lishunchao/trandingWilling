from __future__ import annotations

import argparse
import json
import mimetypes
import urllib.error
import urllib.parse
import urllib.request
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from web.adapters import TrackerAdapter

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "web" / "static"
ADAPTER = TrackerAdapter(ROOT)


class AppHandler(BaseHTTPRequestHandler):
    server_version = "QingyunConsole/1.0"

    def _json(self, value, status=200):
        payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path == "/api/v1/dashboard":
            return self._json(ADAPTER.snapshot())
        if url.path == "/api/v1/candles":
            return self._candles(urllib.parse.parse_qs(url.query))
        if url.path == "/api/v1/marks":
            return self._marks(urllib.parse.parse_qs(url.query))
        if url.path == "/api/v1/dynamic-stops":
            return self._dynamic_stops()
        if url.path == "/api/v1/health":
            return self._json({"ok": True, "service": "qingyun-web-v1"})
        path = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
        target = (PUBLIC / path).resolve()
        if PUBLIC.resolve() not in target.parents and target != PUBLIC.resolve():
            return self.send_error(403)
        if not target.is_file():
            return self.send_error(404)
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _candles(self, query):
        symbol = query.get("symbol", ["BTCUSDT"])[0].upper()
        interval = query.get("interval", ["15m"])[0]
        if not symbol.endswith("USDT") or not symbol.replace("USDT", "").isalnum() or interval not in {"5m", "15m", "1h", "4h"}:
            return self._json({"error": "invalid market parameters"}, 400)
        endpoint = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": interval, "limit": 96}
        )
        try:
            request = urllib.request.Request(endpoint, headers={"User-Agent": "Qingyun-Web-V1/1.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                rows = json.loads(response.read().decode("utf-8"))
            candles = [{"t": r[0], "o": float(r[1]), "h": float(r[2]), "l": float(r[3]), "c": float(r[4]), "v": float(r[5])} for r in rows]
            return self._json({"symbol": symbol, "interval": interval, "candles": candles})
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": "market data temporarily unavailable", "detail": str(exc)}, 502)

    def _marks(self, query):
        requested = {x.upper() for x in query.get("symbols", [""])[0].split(",") if x}
        if not requested or len(requested) > 60 or any(not x.endswith("USDT") or not x.replace("USDT", "").isalnum() for x in requested):
            return self._json({"error": "invalid symbols"}, 400)
        endpoint = "https://fapi.binance.com/fapi/v1/premiumIndex"
        try:
            request = urllib.request.Request(endpoint, headers={"User-Agent": "Trading-Console-V1/1.0"})
            with urllib.request.urlopen(request, timeout=8) as response:
                rows = json.loads(response.read().decode("utf-8"))
            marks = {row["symbol"]: {"price": float(row["markPrice"]), "time": int(row["time"])}
                     for row in rows if row.get("symbol") in requested}
            return self._json({"source": "binance_usdt_perpetual_mark_price", "marks": marks})
        except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            return self._json({"error": "mark prices temporarily unavailable", "detail": str(exc)}, 502)

    @staticmethod
    def _dynamic_stop(item):
        symbol = item["symbol"]
        endpoint = "https://fapi.binance.com/fapi/v1/klines?" + urllib.parse.urlencode(
            {"symbol": symbol, "interval": "15m", "limit": 40}
        )
        request = urllib.request.Request(endpoint, headers={"User-Agent": "Trading-Console-V1/1.0"})
        with urllib.request.urlopen(request, timeout=8) as response:
            rows = json.loads(response.read().decode("utf-8"))
        now_ms = int(time.time() * 1000)
        bars = [{"h": float(r[2]), "l": float(r[3]), "c": float(r[4])} for r in rows if int(r[6]) < now_ms]
        if len(bars) < 20:
            raise ValueError("insufficient completed candles")
        true_ranges = []
        for index in range(1, len(bars)):
            bar, previous = bars[index], bars[index - 1]
            true_ranges.append(max(bar["h"] - bar["l"], abs(bar["h"] - previous["c"]), abs(bar["l"] - previous["c"])))
        atr = sum(true_ranges[-14:]) / 14
        original = float(item["stop"])
        recent = bars[-5:]
        if item["side"] == "做多":
            candidate = min(x["l"] for x in recent) - atr * 0.4
            dynamic = max(original, candidate)
        else:
            candidate = max(x["h"] for x in recent) + atr * 0.4
            dynamic = min(original, candidate)
        return {
            "key": f'{item.get("strategy")}:{symbol}:{item.get("side")}',
            "symbol": symbol,
            "stop": dynamic,
            "original_stop": original,
            "atr": atr,
            "changed": abs(dynamic - original) > max(abs(original) * 1e-10, 1e-12),
        }

    def _dynamic_stops(self):
        snapshot = ADAPTER.snapshot()
        unique = {}
        for item in snapshot["signals"] + snapshot["positions"]:
            key = f'{item.get("strategy")}:{item.get("symbol")}:{item.get("side")}'
            unique[key] = item
        results, errors = {}, 0
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._dynamic_stop, item): key for key, item in unique.items()}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results[result["key"]] = result
                except Exception:
                    errors += 1
        return self._json({"basis": "completed_15m_structure_plus_0.4_atr", "stops": results, "errors": errors})

    def log_message(self, fmt, *args):
        print("[web]", fmt % args)


def main():
    parser = argparse.ArgumentParser(description="Web V1 本地交易控制台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), AppHandler)
    print(f"Web V1：http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
