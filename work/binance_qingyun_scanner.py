#!/usr/bin/env python3
"""Public-market-data helpers for the local Qingyun paper tracker.

This module intentionally contains no exchange-account, order, withdrawal,
Telegram, Bark, API-key, or private-key functionality.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASES = ("https://fapi.binance.com", "https://www.binance.com")


def get_json(path, params=None, timeout=20):
    query = "?" + urllib.parse.urlencode(params) if params else ""
    last_error = None
    for base in BASES:
        for attempt in range(3):
            try:
                request = urllib.request.Request(
                    base + path + query,
                    headers={"User-Agent": "QingyunPaperTracker/1.0"},
                )
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (418, 429):
                    raise
                if exc.code == 451:
                    break
            except Exception as exc:
                last_error = exc
            if attempt < 2:
                time.sleep(0.4 * (2**attempt))
    raise last_error


def ema(values, length):
    factor = 2 / (length + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * factor + result[-1] * (1 - factor))
    return result


def atr(bars, length=14):
    confirmed = [bar for bar in bars if bar["closed"]]
    if len(confirmed) < length + 1:
        return None
    ranges = []
    for index in range(len(confirmed) - length, len(confirmed)):
        bar = confirmed[index]
        previous_close = confirmed[index - 1]["c"]
        ranges.append(max(bar["h"] - bar["l"], abs(bar["h"] - previous_close), abs(bar["l"] - previous_close)))
    return sum(ranges) / len(ranges)


def candles(symbol, interval, limit=120):
    raw = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    now_ms = int(time.time() * 1000)
    return [
        {"t": int(row[0]), "o": float(row[1]), "h": float(row[2]), "l": float(row[3]),
         "c": float(row[4]), "v": float(row[5]), "closed": int(row[6]) < now_ms}
        for row in raw
    ]


def cached_candles(symbol, interval, cache, history_limit=120, refresh_limit=5, interval_seconds=900):
    existing = cache.get(symbol, [])
    if len(existing) >= 70:
        elapsed = max(0, int((time.time() * 1000 - int(existing[-1]["t"])) // (interval_seconds * 1000)))
        limit = min(history_limit, max(refresh_limit, elapsed + 2))
    else:
        limit = history_limit
    merged = {int(item["t"]): item for item in existing}
    merged.update({int(item["t"]): item for item in candles(symbol, interval, limit)})
    result = [merged[key] for key in sorted(merged)][-history_limit:]
    cache[symbol] = result
    return result


def ordered_direction(bars, fast=12, slow=25):
    bars = [bar for bar in bars if bar["closed"]]
    if len(bars) < 70:
        return None
    closes = [bar["c"] for bar in bars]
    fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
    now = closes[-1]
    gap = abs(fast_ema[-1] - slow_ema[-1]) / now
    crossings = sum((closes[i] - fast_ema[i]) * (closes[i - 1] - fast_ema[i - 1]) < 0 for i in range(-12, 0))
    recent_high, older_high = max(x["h"] for x in bars[-12:]), max(x["h"] for x in bars[-24:-12])
    recent_low, older_low = min(x["l"] for x in bars[-12:]), min(x["l"] for x in bars[-24:-12])
    if gap < 0.001 or crossings >= 5:
        return None
    if now > fast_ema[-1] > slow_ema[-1] and fast_ema[-1] > fast_ema[-4] and slow_ema[-1] > slow_ema[-4] and recent_high > older_high and recent_low >= older_low:
        return "LONG"
    if now < fast_ema[-1] < slow_ema[-1] and fast_ema[-1] < fast_ema[-4] and slow_ema[-1] < slow_ema[-4] and recent_high <= older_high and recent_low < older_low:
        return "SHORT"
    return None


def execution_signal(bars, direction, fast=12, slow=25):
    bars = [bar for bar in bars if bar["closed"]]
    if len(bars) < 70:
        return None
    closes = [bar["c"] for bar in bars]
    fast_ema, slow_ema = ema(closes, fast), ema(closes, slow)
    index = len(bars) - 1
    bar, previous = bars[index], bars[index - 1]
    gap = abs(fast_ema[index] - slow_ema[index]) / bar["c"]
    crossings = sum((closes[j] - fast_ema[j]) * (closes[j - 1] - fast_ema[j - 1]) < 0 for j in range(index - 11, index + 1))
    if gap < 0.0008 or crossings >= 5:
        return None
    touch_long = any(bars[j]["l"] <= max(fast_ema[j], slow_ema[j]) * 1.002 and bars[j]["c"] >= min(fast_ema[j], slow_ema[j]) * 0.997 for j in range(index - 4, index))
    touch_short = any(bars[j]["h"] >= min(fast_ema[j], slow_ema[j]) * 0.998 and bars[j]["c"] <= max(fast_ema[j], slow_ema[j]) * 1.003 for j in range(index - 4, index))
    prior_high = max(x["h"] for x in bars[index - 20:index])
    prior_low = min(x["l"] for x in bars[index - 20:index])
    volume_average = sum(x["v"] for x in bars[index - 20:index]) / 20
    if direction == "LONG" and bar["c"] > fast_ema[index] > slow_ema[index] and touch_long and bar["c"] > previous["h"] and bar["c"] > bar["o"]:
        return {"side": "做多", "price": bar["c"], "time": bar["t"], "advance": bar["c"] > prior_high,
                "volume_confirm": bar["v"] >= volume_average, "volume_ratio": bar["v"] / volume_average,
                "prior_level": prior_high, "invalid": min(x["l"] for x in bars[index - 4:index + 1])}
    if direction == "SHORT" and bar["c"] < fast_ema[index] < slow_ema[index] and touch_short and bar["c"] < previous["l"] and bar["c"] < bar["o"]:
        return {"side": "做空", "price": bar["c"], "time": bar["t"], "advance": bar["c"] < prior_low,
                "volume_confirm": bar["v"] >= volume_average, "volume_ratio": bar["v"] / volume_average,
                "prior_level": prior_low, "invalid": max(x["h"] for x in bars[index - 4:index + 1])}
    return None


def universe(settings):
    info = get_json("/fapi/v1/exchangeInfo")
    symbols = [item["symbol"] for item in info["symbols"] if item.get("status") == "TRADING" and item.get("contractType") == "PERPETUAL" and item.get("quoteAsset") == "USDT"]
    minimum = float(settings.get("minimum_quote_volume_usdt", 0))
    if minimum:
        volumes = {item["symbol"]: float(item.get("quoteVolume", 0)) for item in get_json("/fapi/v1/ticker/24hr")}
        symbols = [symbol for symbol in symbols if volumes.get(symbol, 0) >= minimum]
    return symbols


def dominant_direction(symbol, settings):
    return ordered_direction(candles(symbol, settings["dominant_interval"]), settings["ema_fast"], settings["ema_slow"])
