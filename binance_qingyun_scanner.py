#!/usr/bin/env python3
"""Binance USDT perpetual scanner based on the Qingyun video rules.

Alerts only; never places orders. 4h defines direction and 15m confirms a
pullback/retest. Telegram credentials are reused from config.json.
"""
from __future__ import annotations

import json
import logging
import math
import os
import signal
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"
SETTINGS = ROOT / "trading_scanner_config.json"
BARK_CONFIG = ROOT / "bark_private_config.json"
STATE_FILE = ROOT / "trading_scanner_state.json"
EXECUTION_CACHE_FILE = ROOT / "trading_execution_kline_cache.json"
LOG_FILE = ROOT / "trading_scanner.log"
STOP = False
BASE = "https://fapi.binance.com"
FALLBACK_BASES = ("https://www.binance.com",)


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def get_json(path, params=None, timeout=20):
    last_error = None
    query = "?" + urllib.parse.urlencode(params) if params else ""
    for base in (BASE, *FALLBACK_BASES):
        url = base + path + query
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "QingyunScanner/1.0"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code in (418, 429):
                    raise
                if exc.code == 451:
                    break
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
            except Exception as exc:
                last_error = exc
                if attempt < 2:
                    time.sleep(0.4 * (2 ** attempt))
        if isinstance(last_error, urllib.error.HTTPError) and last_error.code != 451:
            break
    raise last_error


def ema(values, length):
    k = 2 / (length + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def atr(bars, length=14):
    """Average true range from confirmed candles only."""
    bars = [x for x in bars if x["closed"]]
    if len(bars) < length + 1:
        return None
    ranges = []
    for i in range(len(bars) - length, len(bars)):
        high, low, prev_close = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(ranges) / len(ranges)


def candles(symbol, interval, limit=120):
    raw = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [{"t": int(x[0]), "o": float(x[1]), "h": float(x[2]), "l": float(x[3]),
             "c": float(x[4]), "v": float(x[5]), "closed": int(x[6]) < int(time.time() * 1000)} for x in raw]


def cached_candles(symbol, interval, cache, history_limit=120, refresh_limit=5,
                   interval_seconds=900):
    """Refresh only recent candles after the first full history request."""
    existing = cache.get(symbol, [])
    if len(existing) >= 70:
        elapsed_bars = max(0, int((time.time() * 1000 - int(existing[-1]["t"]))
                                  // (interval_seconds * 1000)))
        fetch_limit = min(history_limit, max(refresh_limit, elapsed_bars + 2))
    else:
        fetch_limit = history_limit
    fresh = candles(symbol, interval, fetch_limit)
    merged = {int(x["t"]): x for x in existing}
    merged.update({int(x["t"]): x for x in fresh})
    result = [merged[key] for key in sorted(merged)][-history_limit:]
    cache[symbol] = result
    return result


def ordered_direction(bars, fast=12, slow=25):
    bars = [x for x in bars if x["closed"]]
    if len(bars) < 70:
        return None
    closes = [x["c"] for x in bars]
    ef, es = ema(closes, fast), ema(closes, slow)
    now = closes[-1]
    gap = abs(ef[-1] - es[-1]) / now
    crossings = sum((closes[i] - ef[i]) * (closes[i - 1] - ef[i - 1]) < 0 for i in range(-12, 0))
    recent_high, older_high = max(x["h"] for x in bars[-12:]), max(x["h"] for x in bars[-24:-12])
    recent_low, older_low = min(x["l"] for x in bars[-12:]), min(x["l"] for x in bars[-24:-12])
    if gap < 0.001 or crossings >= 5:
        return None
    if now > ef[-1] > es[-1] and ef[-1] > ef[-4] and es[-1] > es[-4] and recent_high > older_high and recent_low >= older_low:
        return "LONG"
    if now < ef[-1] < es[-1] and ef[-1] < ef[-4] and es[-1] < es[-4] and recent_high <= older_high and recent_low < older_low:
        return "SHORT"
    return None


def execution_signal(bars, direction, fast=12, slow=25):
    bars = [x for x in bars if x["closed"]]
    if len(bars) < 70:
        return None
    closes = [x["c"] for x in bars]
    ef, es = ema(closes, fast), ema(closes, slow)
    i = len(bars) - 1
    b, prev = bars[i], bars[i - 1]
    gap = abs(ef[i] - es[i]) / b["c"]
    crossings = sum((closes[j] - ef[j]) * (closes[j - 1] - ef[j - 1]) < 0 for j in range(i - 11, i + 1))
    if gap < 0.0008 or crossings >= 5:
        return None
    touch_long = any(bars[j]["l"] <= max(ef[j], es[j]) * 1.002 and bars[j]["c"] >= min(ef[j], es[j]) * 0.997 for j in range(i - 4, i))
    touch_short = any(bars[j]["h"] >= min(ef[j], es[j]) * 0.998 and bars[j]["c"] <= max(ef[j], es[j]) * 1.003 for j in range(i - 4, i))
    prior_high = max(x["h"] for x in bars[i - 20:i])
    prior_low = min(x["l"] for x in bars[i - 20:i])
    vol_avg = sum(x["v"] for x in bars[i - 20:i]) / 20
    if direction == "LONG" and b["c"] > ef[i] > es[i] and touch_long and b["c"] > prev["h"] and b["c"] > b["o"]:
        return {"side": "做多", "price": b["c"], "time": b["t"], "advance": b["c"] > prior_high,
                "volume_confirm": b["v"] >= vol_avg, "volume_ratio": b["v"] / vol_avg,
                "prior_level": prior_high, "invalid": min(x["l"] for x in bars[i - 4:i + 1])}
    if direction == "SHORT" and b["c"] < ef[i] < es[i] and touch_short and b["c"] < prev["l"] and b["c"] < b["o"]:
        return {"side": "做空", "price": b["c"], "time": b["t"], "advance": b["c"] < prior_low,
                "volume_confirm": b["v"] >= vol_avg, "volume_ratio": b["v"] / vol_avg,
                "prior_level": prior_low, "invalid": max(x["h"] for x in bars[i - 4:i + 1])}
    return None


def telegram(config, text):
    token = config["telegram_bot_token"]
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": str(config["telegram_chat_id"]), "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(url, data=data, headers={"User-Agent": "QingyunScanner/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        result = json.loads(resp.read().decode())
    if not result.get("ok"):
        raise RuntimeError(result.get("description", "Telegram error"))


def bark(text, title="青云 Binance 信号", url=None):
    private = read_json(BARK_CONFIG, {})
    endpoint = str(private.get("push_endpoint", "")).strip().rstrip("/")
    if not endpoint or "PASTE_" in endpoint:
        raise RuntimeError("Bark 私密推送地址尚未配置")
    parsed = urllib.parse.urlparse(endpoint)
    parts = [x for x in parsed.path.split("/") if x]
    if not parsed.scheme or not parsed.netloc or not parts:
        raise RuntimeError("Bark 推送地址格式无效")
    push_api = f"{parsed.scheme}://{parsed.netloc}/push"
    payload = {"device_key": parts[0], "title": title, "body": text,
               "group": "Qingyun", "level": "timeSensitive"}
    if url:
        payload["url"] = url
    req = urllib.request.Request(push_api, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "User-Agent": "QingyunScanner/1.0"})
    with urllib.request.urlopen(req, timeout=25) as resp:
        result = json.loads(resp.read().decode("utf-8"))
    if result.get("code") not in (200, None):
        raise RuntimeError(result.get("message", "Bark push error"))


def universe(settings):
    info = get_json("/fapi/v1/exchangeInfo")
    symbols = [x["symbol"] for x in info["symbols"] if x.get("status") == "TRADING" and
               x.get("contractType") == "PERPETUAL" and x.get("quoteAsset") == "USDT"]
    minimum = float(settings.get("minimum_quote_volume_usdt", 0))
    if minimum:
        tickers = {x["symbol"]: float(x.get("quoteVolume", 0)) for x in get_json("/fapi/v1/ticker/24hr")}
        symbols = [s for s in symbols if tickers.get(s, 0) >= minimum]
    return symbols


def dominant_direction(symbol, settings):
    dominant = candles(symbol, settings["dominant_interval"])
    return ordered_direction(dominant, settings["ema_fast"], settings["ema_slow"])


def analyze(symbol, settings, direction=None, execution_cache=None):
    if direction is None:
        direction = dominant_direction(symbol, settings)
    if not direction:
        return symbol, None
    execution = (cached_candles(symbol, settings["execution_interval"], execution_cache,
                                int(settings.get("execution_history_limit", 120)),
                                int(settings.get("execution_refresh_limit", 5)),
                                int(settings.get("execution_interval_seconds", 900)))
                 if execution_cache is not None else candles(symbol, settings["execution_interval"]))
    signal = execution_signal(execution, direction, settings["ema_fast"], settings["ema_slow"])
    if not signal:
        return symbol, None
    # Only surface executable setups: a fresh structural advance, confirmed
    # volume, and a volatility-normalized structure-invalidating stop. Do not
    # force market risk into a fixed percentage range.
    if settings.get("actionable_require_structural_advance", True) and not signal["advance"]:
        return symbol, None
    if settings.get("actionable_require_volume_confirm", True) and not signal["volume_confirm"]:
        return symbol, None
    execution_atr = atr(execution, int(settings.get("atr_length", 14)))
    if not execution_atr or execution_atr <= 0:
        return symbol, None
    buffer_atr = float(settings.get("structure_stop_atr_buffer", 0.4))
    if signal["side"] == "做多":
        signal["invalid"] -= execution_atr * buffer_atr
    else:
        signal["invalid"] += execution_atr * buffer_atr
    stop_distance_abs = abs(signal["price"] - signal["invalid"])
    stop_atr = stop_distance_abs / execution_atr
    min_stop_atr = float(settings.get("actionable_min_stop_atr", 0.6))
    max_stop_atr = float(settings.get("actionable_max_stop_atr", 3.0))
    if not min_stop_atr <= stop_atr <= max_stop_atr:
        return symbol, None
    signal["atr"] = execution_atr
    signal["stop_atr"] = stop_atr
    signal["stop_distance_pct"] = stop_distance_abs / signal["price"]
    advance_atr = abs(signal["price"] - signal["prior_level"]) / execution_atr
    volume_score = min(signal["volume_ratio"], 3.0) / 3.0 * 40.0
    structure_score = min(advance_atr, 1.0) * 35.0
    risk_score = max(0.0, 1.0 - abs(stop_atr - 1.5) / 1.5) * 25.0
    signal["advance_atr"] = advance_atr
    signal["quality_score"] = volume_score + structure_score + risk_score
    return symbol, signal


def scan_once(config, settings, state, send=True):
    symbols = universe(settings)
    logging.info("开始扫描 %d 个 Binance USDT 永续合约", len(symbols))
    now = int(time.time())
    dominant_seconds = int(settings.get("dominant_interval_seconds", 14400))
    expected_dominant_bar = (now // dominant_seconds - 1) * dominant_seconds * 1000
    direction_cache = state.setdefault("direction_cache", {})
    execution_cache = read_json(EXECUTION_CACHE_FILE, {})
    cached_directions = {}
    stale_symbols = []
    for symbol in symbols:
        item = direction_cache.get(symbol, {})
        if int(item.get("bar_time", 0)) == expected_dominant_bar:
            cached_directions[symbol] = item.get("direction")
        else:
            stale_symbols.append(symbol)

    # Refresh the slow 4H layer only when its cache expires. The fast 15m
    # layer is requested solely for symbols with a confirmed 4H direction.
    if stale_symbols:
        with ThreadPoolExecutor(max_workers=int(settings.get("workers", 8))) as pool:
            futures = {pool.submit(dominant_direction, s, settings): s for s in stale_symbols}
            for future in as_completed(futures):
                symbol = futures[future]
                try:
                    direction = future.result()
                    cached_directions[symbol] = direction
                    direction_cache[symbol] = {"direction": direction, "bar_time": expected_dominant_bar,
                                               "updated_at": now}
                except Exception as exc:
                    logging.warning("%s 4H方向刷新失败: %s", symbol, exc)
    active = [(s, cached_directions.get(s)) for s in symbols if cached_directions.get(s)]
    logging.info("4H缓存命中=%d 刷新=%d，进入15m层=%d", len(symbols) - len(stale_symbols),
                 len(stale_symbols), len(active))
    found = []
    with ThreadPoolExecutor(max_workers=int(settings.get("workers", 8))) as pool:
        futures = {pool.submit(analyze, s, settings, direction, execution_cache): s
                   for s, direction in active}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, sig = future.result()
                if sig:
                    found.append((symbol, sig))
            except Exception as exc:
                logging.warning("%s 扫描失败: %s", symbol, exc)
    for symbol, sig in sorted(found):
        key = f"{symbol}:{sig['side']}"
        previous = state.setdefault("signals", {}).get(key)
        if previous and int(previous.get("bar_time", 0)) >= sig["time"]:
            continue
        tracked = {str(x).split(":", 1)[0] for x in settings.get("tracked_positions", [])}
        phase = "加仓候选" if symbol in tracked and previous and sig["advance"] else "入场候选"
        if previous and phase == "加仓候选" and int(previous.get("bar_time", 0)) >= sig["time"]:
            continue
        message = (f"🔎 青云系统 {phase}\n\n{symbol}｜{sig['side']}\n"
                   f"4H 定方向 + 15m 回踩确认\n触发价：{sig['price']:.10g}\n"
                   f"结构失效参考：{sig['invalid']:.10g}\n"
                   f"15m ATR：{sig['atr']:.10g}｜止损距离：{sig['stop_distance_pct']:.2%}\n"
                   f"质量评分：{sig['quality_score']:.1f}/100\n"
                   f"成交量确认：{'是' if sig['volume_confirm'] else '否'}\n\n"
                   "仅为规则扫描提醒，不自动下单；请在 TradingView 复核结构与流动性。\n"
                   f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P")
        chart_url = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol}.P"
        if send and settings.get("telegram_enabled", True):
            telegram(config, message)
        if send and settings.get("bark_enabled", False):
            bark(message, f"{symbol} {sig['side']} {phase}", chart_url)
        state["signals"][key] = {"bar_time": sig["time"], "price": sig["price"],
                                  "phase": phase, "suggested_stop": sig["invalid"],
                                  "stop_basis": "15m结构加ATR缓冲", "atr": sig["atr"],
                                  "stop_atr": sig["stop_atr"], "quality_score": sig["quality_score"],
                                  "volume_ratio": sig["volume_ratio"],
                                  "advance_atr": sig["advance_atr"], "alerted_at": now}
        if send:
            write_json(STATE_FILE, state)
        logging.info("%s %s %s 触发价=%s 建议止损=%s", symbol, sig["side"], phase,
                     sig["price"], sig["invalid"])
    state["last_scan"] = int(time.time())
    state["last_universe_count"] = len(symbols)
    state["last_candidates"] = len(found)
    if send:
        write_json(EXECUTION_CACHE_FILE, execution_cache)
        write_json(STATE_FILE, state)
    logging.info("扫描完成：%d 个合约，%d 个候选", len(symbols), len(found))
    return len(symbols), len(found)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
    config = read_json(CONFIG)
    settings = read_json(SETTINGS)
    if not config or not settings:
        raise SystemExit("缺少 config.json 或 trading_scanner_config.json")
    state = read_json(STATE_FILE, {"signals": {}}) if not args.dry_run else {"signals": {}}
    global STOP
    signal.signal(signal.SIGINT, lambda *_: globals().__setitem__("STOP", True))
    signal.signal(signal.SIGTERM, lambda *_: globals().__setitem__("STOP", True))
    while not STOP:
        try:
            scan_once(config, settings, state, send=not args.dry_run)
        except Exception:
            logging.exception("扫描轮次失败")
        if args.once:
            break
        for _ in range(int(settings.get("poll_interval_seconds", 300))):
            if STOP:
                break
            time.sleep(1)


if __name__ == "__main__":
    main()
