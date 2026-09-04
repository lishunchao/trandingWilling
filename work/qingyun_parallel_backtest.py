#!/usr/bin/env python3
"""Two-year Binance USD-M USDT perpetual comparison.

The baseline mechanically encodes only the video-explicit trend, EMA-band
pullback and confirmation concepts. The enhanced version adds the current
scanner's structural-advance, volume and ATR-normalized stop filters.

Research only. This file has no authenticated exchange endpoints and cannot
place orders.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
WORK = ROOT / "runtime"
OUT = ROOT / "outputs"
CACHE = WORK / "binance_vision_cache"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
MS15 = 15 * 60 * 1000
MS4H = 4 * 60 * 60 * 1000


@dataclass
class Trade:
    strategy: str
    symbol: str
    side: str
    signal_time: int
    entry_time: int
    exit_time: int
    entry: float
    stop: float
    target: float
    exit: float
    gross_r: float
    net_r: float
    reason: str
    bars_held: int
    volume_ratio: float
    advance_atr: float
    stop_atr: float


def http_bytes(url: str, timeout: int = 60, retries: int = 5) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "QingyunResearch/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(min(8, 0.5 * (2**attempt)))
    raise last


def s3_keys(prefix: str) -> list[str]:
    keys, token = [], None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        root = ET.fromstring(http_bytes(S3 + "?" + urllib.parse.urlencode(params)))
        ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
        keys.extend(x.text for x in root.findall("s:Contents/s:Key", ns) if x.text)
        truncated = (root.findtext("s:IsTruncated", default="false", namespaces=ns) == "true")
        if not truncated:
            return keys
        token = root.findtext("s:NextContinuationToken", namespaces=ns)


def historical_symbols() -> list[str]:
    # Delimiter listing includes active and delisted contract directories.
    params = {"list-type": "2", "prefix": "data/futures/um/monthly/klines/", "delimiter": "/", "max-keys": "1000"}
    root = ET.fromstring(http_bytes(S3 + "?" + urllib.parse.urlencode(params)))
    ns = {"s": "http://s3.amazonaws.com/doc/2006-03-01/"}
    symbols = []
    for node in root.findall("s:CommonPrefixes/s:Prefix", ns):
        symbol = node.text.rstrip("/").split("/")[-1]
        if symbol.endswith("USDT"):
            symbols.append(symbol)
    return sorted(set(symbols))


def key_month(key: str) -> str | None:
    name = key.rsplit("/", 1)[-1]
    if not name.endswith(".zip") or "CHECKSUM" in name:
        return None
    parts = name[:-4].split("-")
    if len(parts) < 3:
        return None
    return "-".join(parts[-2:])


def month_range(start: str, end: str) -> set[str]:
    sy, sm = map(int, start[:7].split("-"))
    ey, em = map(int, end[:7].split("-"))
    out = set()
    y, m = sy, sm
    while (y, m) < (ey, em):
        out.add(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def download_symbol(symbol: str, wanted: set[str]) -> tuple[str, list[Path], str | None]:
    target = CACHE / symbol
    target.mkdir(parents=True, exist_ok=True)
    prefix = f"data/futures/um/monthly/klines/{symbol}/15m/"
    try:
        selected = [(k, key_month(k)) for k in s3_keys(prefix)]
        selected = [(k, m) for k, m in selected if m in wanted]
        paths = []
        for key, month in selected:
            path = target / f"{symbol}-15m-{month}.zip"
            if not path.exists() or path.stat().st_size < 100:
                data = http_bytes("https://data.binance.vision/" + key)
                tmp = path.with_suffix(".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
            paths.append(path)
        return symbol, sorted(paths), None
    except Exception as exc:
        return symbol, [], repr(exc)


def load_bars(paths: list[Path], start_ms: int, end_ms: int) -> np.ndarray:
    rows = []
    for path in paths:
        try:
            with zipfile.ZipFile(path) as zf:
                name = next(n for n in zf.namelist() if n.endswith(".csv"))
                with zf.open(name) as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8")
                    for row in csv.reader(text):
                        if not row or not row[0].isdigit():
                            continue
                        ts = int(row[0])
                        if start_ms <= ts < end_ms:
                            rows.append((ts, float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
        except (zipfile.BadZipFile, StopIteration):
            continue
    if not rows:
        return np.empty((0, 6), dtype=np.float64)
    rows.sort(key=lambda x: x[0])
    arr = np.asarray(rows, dtype=np.float64)
    _, unique = np.unique(arr[:, 0], return_index=True)
    return arr[np.sort(unique)]


def ema(values: np.ndarray, length: int) -> np.ndarray:
    out = np.empty_like(values)
    out[0] = values[0]
    alpha = 2.0 / (length + 1.0)
    for i in range(1, len(values)):
        out[i] = values[i] * alpha + out[i - 1] * (1.0 - alpha)
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, length: int = 14) -> np.ndarray:
    prev = np.r_[close[0], close[:-1]]
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    out = np.full(len(close), np.nan)
    if len(close) >= length:
        seed = float(np.mean(tr[:length]))
        out[length - 1] = seed
        for i in range(length, len(close)):
            out[i] = (out[i - 1] * (length - 1) + tr[i]) / length
    return out


def resample_4h(bars: np.ndarray) -> np.ndarray:
    buckets = (bars[:, 0].astype(np.int64) // MS4H) * MS4H
    starts = np.r_[0, np.flatnonzero(np.diff(buckets)) + 1]
    ends = np.r_[starts[1:], len(bars)]
    out = []
    for a, b in zip(starts, ends):
        # Only complete 4h buckets (16 consecutive 15m candles).
        if b - a == 16 and int(bars[a, 0]) == int(buckets[a]):
            out.append((buckets[a], bars[a, 1], np.max(bars[a:b, 2]), np.min(bars[a:b, 3]), bars[b - 1, 4], np.sum(bars[a:b, 5])))
    return np.asarray(out, dtype=np.float64) if out else np.empty((0, 6), dtype=np.float64)


def direction_series(b4: np.ndarray) -> np.ndarray:
    n = len(b4)
    out = np.zeros(n, dtype=np.int8)
    if n < 70:
        return out
    c, h, l = b4[:, 4], b4[:, 2], b4[:, 3]
    ef, es = ema(c, 12), ema(c, 25)
    cross = ((c[1:] - ef[1:]) * (c[:-1] - ef[:-1]) < 0).astype(np.int8)
    for i in range(69, n):
        gap = abs(ef[i] - es[i]) / c[i]
        crossings = int(np.sum(cross[max(0, i - 11):i + 1]))
        if gap < 0.001 or crossings >= 5:
            continue
        rh, oh = np.max(h[i - 11:i + 1]), np.max(h[i - 23:i - 11])
        rl, ol = np.min(l[i - 11:i + 1]), np.min(l[i - 23:i - 11])
        if c[i] > ef[i] > es[i] and ef[i] > ef[i - 3] and es[i] > es[i - 3] and rh > oh and rl >= ol:
            out[i] = 1
        elif c[i] < ef[i] < es[i] and ef[i] < ef[i - 3] and es[i] < es[i - 3] and rh <= oh and rl < ol:
            out[i] = -1
    return out


def simulate(symbol: str, bars: np.ndarray, stop_lookback: int = 5) -> list[Trade]:
    if len(bars) < 5000:
        return []
    b4 = resample_4h(bars)
    if len(b4) < 70:
        return []
    d4 = direction_series(b4)
    t, o, h, l, c, v = (bars[:, i] for i in range(6))
    ef, es = ema(c, 12), ema(c, 25)
    a = atr(h, l, c, 14)
    cross = ((c[1:] - ef[1:]) * (c[:-1] - ef[:-1]) < 0).astype(np.int8)
    close4 = b4[:, 0].astype(np.int64) + MS4H
    trades: list[Trade] = []
    next_free = {"baseline": 70, "enhanced": 70}
    for i in range(70, len(bars) - 33):
        close_time = int(t[i]) + MS15
        j4 = int(np.searchsorted(close4, close_time, side="right") - 1)
        if j4 < 69 or d4[j4] == 0:
            continue
        direction = int(d4[j4])
        gap = abs(ef[i] - es[i]) / c[i]
        crossings = int(np.sum(cross[max(0, i - 11):i + 1]))
        if gap < 0.0008 or crossings >= 5:
            continue
        touch_long = any(l[j] <= max(ef[j], es[j]) * 1.002 and c[j] >= min(ef[j], es[j]) * 0.997 for j in range(i - 4, i))
        touch_short = any(h[j] >= min(ef[j], es[j]) * 0.998 and c[j] <= max(ef[j], es[j]) * 1.003 for j in range(i - 4, i))
        long_ok = direction == 1 and c[i] > ef[i] > es[i] and touch_long and c[i] > h[i - 1] and c[i] > o[i]
        short_ok = direction == -1 and c[i] < ef[i] < es[i] and touch_short and c[i] < l[i - 1] and c[i] < o[i]
        if not (long_ok or short_ok) or not np.isfinite(a[i]) or a[i] <= 0:
            continue
        side = 1 if long_ok else -1
        prior_level = np.max(h[i - 20:i]) if side == 1 else np.min(l[i - 20:i])
        advance = c[i] > prior_level if side == 1 else c[i] < prior_level
        vol_avg = float(np.mean(v[i - 20:i]))
        volume_ratio = float(v[i] / vol_avg) if vol_avg > 0 else 0.0
        # Keep enhanced entry eligibility identical to the live scanner's
        # five-bar structure. A wider stop lookback is execution-only so the
        # sensitivity run changes one variable rather than silently replacing
        # the signal population.
        filter_invalid = np.min(l[i - 4:i + 1]) if side == 1 else np.max(h[i - 4:i + 1])
        filter_stop = filter_invalid - 0.4 * a[i] if side == 1 else filter_invalid + 0.4 * a[i]
        stop_atr = abs(c[i] - filter_stop) / a[i]
        raw_invalid = np.min(l[i - stop_lookback + 1:i + 1]) if side == 1 else np.max(h[i - stop_lookback + 1:i + 1])
        stop = raw_invalid - 0.4 * a[i] if side == 1 else raw_invalid + 0.4 * a[i]
        advance_atr = abs(c[i] - prior_level) / a[i]
        eligible = {
            "baseline": True,
            "enhanced": bool(advance and volume_ratio >= 1.0 and 0.6 <= stop_atr <= 3.0),
        }
        for strategy in ("baseline", "enhanced"):
            if not eligible[strategy] or i + 1 < next_free[strategy]:
                continue
            entry_i = i + 1
            entry = float(o[entry_i]) * (1.0002 if side == 1 else 0.9998)
            risk = (entry - stop) if side == 1 else (stop - entry)
            if risk <= 0 or risk / entry > 0.15:
                continue
            target = entry + side * 2.0 * risk
            exit_i, exit_px, reason = min(entry_i + 32, len(bars) - 1), float(c[min(entry_i + 32, len(bars) - 1)]), "time"
            for k in range(entry_i, min(entry_i + 33, len(bars))):
                stop_hit = l[k] <= stop if side == 1 else h[k] >= stop
                target_hit = h[k] >= target if side == 1 else l[k] <= target
                if stop_hit:  # conservative ordering if both touched in one candle
                    exit_i, exit_px, reason = k, stop, "stop"
                    break
                if target_hit:
                    exit_i, exit_px, reason = k, target, "target"
                    break
            exit_fill = exit_px * (0.9998 if side == 1 else 1.0002)
            gross_r = side * (exit_px - entry) / risk
            # 5 bps taker commission each side plus 2 bps slippage each side.
            fees = (entry + exit_fill) * 0.0005
            net_pnl = side * (exit_fill - entry) - fees
            net_r = net_pnl / risk
            trades.append(Trade(strategy, symbol, "LONG" if side == 1 else "SHORT", int(t[i]), int(t[entry_i]), int(t[exit_i]), entry, stop, target, exit_fill, gross_r, net_r, reason, exit_i - entry_i + 1, volume_ratio, advance_atr, stop_atr))
            next_free[strategy] = exit_i + 1
    return trades


def summarize(trades: list[Trade]) -> dict:
    if not trades:
        return {"trades": 0}
    rs = [x.net_r for x in trades]
    wins = [x for x in trades if x.net_r > 0]
    losses = [x for x in trades if x.net_r <= 0]
    ordered = sorted(trades, key=lambda x: (x.exit_time, x.symbol))
    curve, peak, max_dd = 0.0, 0.0, 0.0
    for tr in ordered:
        curve += tr.net_r
        peak = max(peak, curve)
        max_dd = max(max_dd, peak - curve)
    by_symbol = len(set(x.symbol for x in trades))
    return {
        "trades": len(trades), "symbols": by_symbol,
        "win_rate": len(wins) / len(trades), "expectancy_r": statistics.mean(rs),
        "median_r": statistics.median(rs),
        "profit_factor": sum(max(0, x) for x in rs) / abs(sum(min(0, x) for x in rs)) if losses else None,
        "target_rate": sum(x.reason == "target" for x in trades) / len(trades),
        "stop_rate": sum(x.reason == "stop" for x in trades) / len(trades),
        "avg_bars_held": statistics.mean(x.bars_held for x in trades),
        "max_drawdown_r_sequential_exit_order": max_dd,
        "sum_r": sum(rs),
    }


def iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def write_results(trades: list[Trade], meta: dict, suffix: str = "") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"_{suffix}" if suffix else ""
    csv_path = OUT / f"qingyun_parallel_backtest_trades{tag}.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        fields = list(asdict(trades[0]).keys()) if trades else ["strategy", "symbol"]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for tr in trades:
            w.writerow(asdict(tr))
    summary = {**meta, "baseline": summarize([x for x in trades if x.strategy == "baseline"]), "enhanced": summarize([x for x in trades if x.strategy == "enhanced"])}
    (OUT / f"qingyun_parallel_backtest_summary{tag}.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    b, e = summary["baseline"], summary["enhanced"]
    report = f"""# 青云视频基础版 vs 当前增强版：两年全市场并行回测

生成时间：{datetime.now().astimezone().isoformat()}

## 数据与执行假设

- 数据：Binance Vision USD-M USDT 永续 15m，包含当前及期间已下架合约。
- 区间：{meta['start']} 至 {meta['end']}（结束时间不含）。
- 已处理：{meta['symbols_processed']} 个合约；跳过：{meta['symbols_skipped']} 个。
- 信号只使用已收盘K线；下一根15m开盘成交，杜绝同K线未来函数。
- 退出：结构外 0.4 ATR 止损、2R止盈、最长持有32根15m。
- 成本：单边0.05% taker手续费 + 单边0.02%滑点；未计资金费率。
- 一次仅持有同币种同版本的一笔仓位。统计为信号级研究，不是受账户并发仓位限制的资金曲线。

## 版本定义

- 基础版：4H趋势与高低点、避免均线反复穿插、15m回踩EMA12/25后方向确认。
- 增强版：基础版之上，增加20根结构突破、成交量不低于20根均量、止损距离0.6–3.0 ATR。
- 视频没有提供完全机械化的止盈止损，因此两版使用同一退出框架，主要比较入场过滤质量。

## 汇总

| 指标 | 视频基础版 | 当前增强版 |
|---|---:|---:|
| 交易数 | {b.get('trades', 0)} | {e.get('trades', 0)} |
| 覆盖合约 | {b.get('symbols', 0)} | {e.get('symbols', 0)} |
| 胜率 | {b.get('win_rate', 0):.2%} | {e.get('win_rate', 0):.2%} |
| 单笔期望 | {b.get('expectancy_r', 0):.4f}R | {e.get('expectancy_r', 0):.4f}R |
| 盈利因子 | {b.get('profit_factor') or 0:.3f} | {e.get('profit_factor') or 0:.3f} |
| 2R止盈率 | {b.get('target_rate', 0):.2%} | {e.get('target_rate', 0):.2%} |
| 止损率 | {b.get('stop_rate', 0):.2%} | {e.get('stop_rate', 0):.2%} |

## 解释边界

这不是原作者官方源码回测，而是“视频明确规则的机械化基础版”与“当前本机增强版”的可复现比较。结果必须结合时间切片、币种切片和模拟跟踪继续验证，不能直接视为未来收益承诺。
"""
    (OUT / f"qingyun_parallel_backtest_report{tag}.md").write_text(report, encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2024-09-01")
    ap.add_argument("--end", default="2026-09-01")
    ap.add_argument("--workers", type=int, default=12)
    ap.add_argument("--limit-symbols", type=int, default=0)
    ap.add_argument("--offline", action="store_true", help="reuse cached monthly zip files without S3 listing")
    ap.add_argument("--stop-lookback", type=int, default=5)
    ap.add_argument("--suffix", default="")
    args = ap.parse_args()
    start_ms = int(datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc).timestamp() * 1000)
    wanted = month_range(args.start, args.end)
    symbols = sorted(p.name for p in CACHE.iterdir() if p.is_dir()) if args.offline and CACHE.exists() else historical_symbols()
    if args.limit_symbols:
        symbols = symbols[:args.limit_symbols]
    print(f"symbols={len(symbols)} months={len(wanted)}", flush=True)
    downloads = {}
    errors = {}
    if args.offline:
        for symbol in symbols:
            downloads[symbol] = sorted((CACHE / symbol).glob(f"{symbol}-15m-*.zip"))
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(download_symbol, s, wanted): s for s in symbols}
            done = 0
            for fut in as_completed(futures):
                symbol, paths, error = fut.result()
                downloads[symbol] = paths
                if error:
                    errors[symbol] = error
                done += 1
                if done % 25 == 0 or done == len(symbols):
                    print(f"download_indexed={done}/{len(symbols)}", flush=True)
    all_trades, processed, skipped = [], 0, 0
    for idx, symbol in enumerate(symbols, 1):
        bars = load_bars(downloads.get(symbol, []), start_ms, end_ms)
        if len(bars) < 5000:
            skipped += 1
            continue
        all_trades.extend(simulate(symbol, bars, args.stop_lookback))
        processed += 1
        if processed % 10 == 0:
            print(f"backtested={processed} scanned={idx}/{len(symbols)} trades={len(all_trades)}", flush=True)
    meta = {"start": args.start, "end": args.end, "symbols_discovered": len(symbols), "symbols_processed": processed, "symbols_skipped": skipped, "download_errors": errors, "stop_lookback": args.stop_lookback, "generated_at": datetime.now(timezone.utc).isoformat()}
    write_results(all_trades, meta, args.suffix)
    print(json.dumps({"baseline": summarize([x for x in all_trades if x.strategy == "baseline"]), "enhanced": summarize([x for x in all_trades if x.strategy == "enhanced"]), "meta": meta}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
