#!/usr/bin/env python3
"""Local, unauthenticated paper tracker for baseline and enhanced rules.

It reads public Binance market data only. It has no order, account, API-key,
withdrawal, or transfer code.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
WORK = ROOT / "work"
STATE_PATH = WORK / "paper_tracker_state.json"
LOG_PATH = WORK / "paper_tracker.log"
STATUS_PATH = OUT / "paper_tracker_status.json"
SOURCE = WORK / "binance_qingyun_scanner.py"
SETTINGS_PATH = ROOT / "config" / "trading_scanner_config.json"
FUNDING_PROXY = 0.0001
COST_MODEL_ID = "taker5bps_slippage2bps_each_side_funding1bp"


def load_scanner():
    spec = importlib.util.spec_from_file_location("qingyun_scanner", SOURCE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def normalize_trade_costs(state):
    """Idempotently migrate closed reference trades to the research cost model."""
    migrated = 0
    for trade in state.get("trades", []):
        if trade.get("cost_model") == COST_MODEL_ID:
            continue
        risk = abs(float(trade.get("entry", 0)) - float(trade.get("stop", 0)))
        if risk <= 0 or trade.get("net_r") is None:
            continue
        trade["net_r"] = float(trade["net_r"]) - float(trade["entry"]) * FUNDING_PROXY / risk
        trade["cost_model"] = COST_MODEL_ID
        trade["funding_proxy"] = FUNDING_PROXY
        migrated += 1
    state["cost_model"] = COST_MODEL_ID
    return migrated


def enhanced_ok(sig, bars, scanner, settings):
    if not sig.get("advance") or not sig.get("volume_confirm"):
        return False, None
    atr_value = scanner.atr(bars, int(settings.get("atr_length", 14)))
    if not atr_value or atr_value <= 0:
        return False, None
    invalid = sig["invalid"] + (-1 if sig["side"] == "做多" else 1) * atr_value * float(settings.get("structure_stop_atr_buffer", 0.4))
    stop_atr = abs(sig["price"] - invalid) / atr_value
    ok = float(settings.get("actionable_min_stop_atr", 0.6)) <= stop_atr <= float(settings.get("actionable_max_stop_atr", 3.0))
    return ok, {"invalid": invalid, "atr": atr_value, "stop_atr": stop_atr}


def paper_fill(scanner, symbol, side, fallback):
    try:
        mark = float(scanner.get_json("/fapi/v1/premiumIndex", {"symbol": symbol})["markPrice"])
    except Exception:
        mark = fallback
    return mark * (1.0002 if side == "做多" else 0.9998)


def update_positions(state, now):
    still = []
    for p in state.get("positions", []):
        bars = state.get("execution_cache", {}).get(p["symbol"], [])
        fresh = [b for b in bars if b.get("closed") and int(b["t"]) > int(p["entry_bar_time"])]
        closed = None
        for b in fresh:
            stop_hit = b["l"] <= p["stop"] if p["side"] == "做多" else b["h"] >= p["stop"]
            target_hit = b["h"] >= p["target"] if p["side"] == "做多" else b["l"] <= p["target"]
            age = (int(b["t"]) - int(p["entry_bar_time"])) // 900000
            if stop_hit:
                closed = (p["stop"], "stop", b["t"])
            elif target_hit:
                closed = (p["target"], "target", b["t"])
            elif age >= 32:
                closed = (b["c"], "time", b["t"])
            if closed:
                break
        if closed:
            px, reason, exit_time = closed
            risk = abs(p["entry"] - p["stop"])
            sign = 1 if p["side"] == "做多" else -1
            exit_fill = px * (0.9998 if sign == 1 else 1.0002)
            net = sign * (exit_fill - p["entry"]) - (p["entry"] + exit_fill) * 0.0005 - p["entry"] * FUNDING_PROXY
            p.update({"exit": exit_fill, "exit_time": exit_time, "reason": reason, "net_r": net / risk,
                      "cost_model": COST_MODEL_ID, "funding_proxy": FUNDING_PROXY})
            state.setdefault("trades", []).append(p)
        else:
            still.append(p)
    state["positions"] = still


def scan_once(scanner, settings, state):
    now = int(time.time())
    symbols = scanner.universe(settings)
    dominant_seconds = int(settings.get("dominant_interval_seconds", 3600))
    expected_dominant_bar = (now // dominant_seconds) * dominant_seconds * 1000
    directions = state.setdefault("directions", {})
    stale = [s for s in symbols if int(directions.get(s, {}).get("bar_time", 0)) != expected_dominant_bar]
    with ThreadPoolExecutor(max_workers=int(settings.get("workers", 8))) as pool:
        futs = {pool.submit(scanner.dominant_direction, s, settings): s for s in stale}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                directions[s] = {"bar_time": expected_dominant_bar, "direction": fut.result()}
            except Exception as exc:
                logging.warning("%s 4h refresh failed: %s", s, exc)
    active = [(s, directions.get(s, {}).get("direction")) for s in symbols if directions.get(s, {}).get("direction")]
    cache = state.setdefault("execution_cache", {})
    results = []
    def analyze_one(item):
        symbol, direction = item
        bars = scanner.cached_candles(symbol, settings["execution_interval"], cache, 120, 5, 900)
        return symbol, bars, scanner.execution_signal(bars, direction, 12, 25)
    with ThreadPoolExecutor(max_workers=int(settings.get("workers", 8))) as pool:
        futs = {pool.submit(analyze_one, item): item[0] for item in active}
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as exc:
                logging.warning("%s 15m refresh failed: %s", futs[fut], exc)
    update_positions(state, now)
    open_keys = {(p["strategy"], p["symbol"]) for p in state.get("positions", [])}
    seen = state.setdefault("seen_signals", {})
    new_signals = []
    for symbol, bars, sig in results:
        if not sig:
            continue
        atr_value = scanner.atr(bars, 14)
        if not atr_value:
            continue
        base_invalid = sig["invalid"] + (-1 if sig["side"] == "做多" else 1) * atr_value * 0.4
        versions = [("baseline", True, base_invalid)]
        ok, extra = enhanced_ok(sig, bars, scanner, settings)
        versions.append(("enhanced", ok, extra["invalid"] if extra else base_invalid))
        for strategy, eligible, stop in versions:
            key = f"{strategy}:{symbol}:{sig['side']}"
            if not eligible or (strategy, symbol) in open_keys or int(seen.get(key, 0)) >= int(sig["time"]):
                continue
            entry = paper_fill(scanner, symbol, sig["side"], sig["price"])
            risk = entry - stop if sig["side"] == "做多" else stop - entry
            if risk <= 0:
                continue
            target = entry + (2 * risk if sig["side"] == "做多" else -2 * risk)
            pos = {"strategy": strategy, "symbol": symbol, "side": sig["side"], "signal_time": sig["time"], "entry_bar_time": sig["time"], "opened_at": now, "entry": entry, "stop": stop, "target": target, "risk_pct": risk / entry, "status": "OPEN"}
            state.setdefault("positions", []).append(pos)
            seen[key] = sig["time"]
            new_signals.append(pos)
    state.update({"last_scan": now, "last_scan_iso": datetime.now(timezone.utc).isoformat(), "universe": len(symbols), "active_directions": len(active), "new_signals": new_signals[-50:], "cost_model": COST_MODEL_ID})
    state["trades"] = state.get("trades", [])[-20000:]
    write_json(STATE_PATH, state)
    write_json(STATUS_PATH, {k: state.get(k) for k in ("last_scan", "last_scan_iso", "universe", "active_directions", "new_signals", "positions", "trades", "cost_model")})
    logging.info("scan complete universe=%d active=%d new=%d open=%d closed=%d", len(symbols), len(active), len(new_signals), len(state.get("positions", [])), len(state.get("trades", [])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH, encoding="utf-8"), logging.StreamHandler()])
    scanner = load_scanner()
    settings = read_json(SETTINGS_PATH, {})
    settings.update({
        "dominant_interval": "1h",
        "dominant_interval_seconds": 3600,
        "execution_interval": "15m",
        "execution_interval_seconds": 900,
        "structure_stop_atr_buffer": 0.4,
        "workers": min(8, int(settings.get("workers", 8))),
        "telegram_enabled": False,
        "bark_enabled": False,
    })
    state = read_json(STATE_PATH, {"positions": [], "trades": []})
    migrated = normalize_trade_costs(state)
    if migrated:
        write_json(STATE_PATH, state)
        logging.info("migrated %d closed trades to cost_model=%s", migrated, COST_MODEL_ID)
    while True:
        try:
            scan_once(scanner, settings, state)
        except Exception:
            logging.exception("paper scan failed")
        if args.once:
            return
        # Run about one minute after each 15m close.
        delay = 60 + 900 - (int(time.time()) % 900)
        time.sleep(delay)


if __name__ == "__main__":
    main()
