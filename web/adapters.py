from __future__ import annotations

import json
import time
from pathlib import Path


class TrackerAdapter:
    """Read-only adapter over the existing paper tracker artifacts."""

    def __init__(self, root: Path):
        self.root = root
        self.status_path = root / "outputs" / "paper_tracker_status.json"
        self.config_path = root / "config" / "trading_scanner_config.json"
        self.log_path = root / "work" / "paper_tracker.log"

    @staticmethod
    def _read_json(path: Path, default):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default

    @staticmethod
    def _rr(item: dict) -> float | None:
        try:
            entry, stop, target = (float(item[k]) for k in ("entry", "stop", "target"))
            risk = abs(entry - stop)
            return round(abs(target - entry) / risk, 2) if risk else None
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    def _score(item: dict, cost_model: str | None = None) -> tuple[int, dict]:
        rr = TrackerAdapter._rr(item) or 0
        risk = float(item.get("risk_pct") or 0)
        confirmation = 30 if item.get("strategy") == "enhanced" else 18
        reward_risk = round(min(30, max(0, rr / 3 * 30)))
        if risk <= 0.015:
            risk_control = 30
        elif risk <= 0.03:
            risk_control = 24
        elif risk <= 0.06:
            risk_control = 16
        elif risk <= 0.10:
            risk_control = 8
        else:
            risk_control = 2
        cost_integrity = 10 if cost_model and cost_model != "unknown" else 0
        parts = {"confirmation": confirmation, "reward_risk": reward_risk,
                 "risk_control": risk_control, "cost_integrity": cost_integrity}
        return min(100, sum(parts.values())), parts

    @staticmethod
    def _grade(score: int) -> str:
        if score >= 85:
            return "A"
        if score >= 70:
            return "B"
        return "C"

    def snapshot(self) -> dict:
        raw = self._read_json(self.status_path, {})
        config = self._read_json(self.config_path, {})
        cost_model = raw.get("cost_model", "unknown")
        positions = [self._decorate(x, cost_model) for x in raw.get("positions", [])]
        signals = [self._decorate(x, cost_model) for x in raw.get("new_signals", [])]
        trades = raw.get("trades", [])
        closed = [x for x in trades if x.get("net_r") is not None]
        wins = [x for x in closed if float(x.get("net_r", 0)) > 0]
        expectancy = sum(float(x.get("net_r", 0)) for x in closed) / len(closed) if closed else None
        updated = float(raw.get("last_scan") or 0)
        age = max(0, int(time.time() - updated)) if updated else None
        log_mtime = self.log_path.stat().st_mtime if self.log_path.exists() else 0
        running = bool(updated and age is not None and age < 35 * 60)
        return {
            "generated_at": int(time.time()),
            "source": "outputs/paper_tracker_status.json",
            "mode": "paper_research_only",
            "system": {
                "status": "running" if running else ("stale" if updated else "waiting"),
                "last_scan": raw.get("last_scan_iso"),
                "age_seconds": age,
                "log_updated": int(log_mtime) if log_mtime else None,
                "universe": raw.get("universe", 0),
                "active_directions": raw.get("active_directions", 0),
                "cost_model": raw.get("cost_model", "unknown"),
            },
            "metrics": {
                "open_positions": len(positions),
                "new_signals": len(signals),
                "closed_trades": len(closed),
                "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
                "expectancy_r": round(expectancy, 3) if expectancy is not None else None,
            },
            "signals": sorted(signals, key=lambda x: (x["grade"], -int(x.get("opened_at") or 0))),
            "positions": sorted(positions, key=lambda x: -int(x.get("opened_at") or 0)),
            "telegram": {
                "enabled": bool(config.get("telegram_enabled", False)),
                "status": "disabled" if not config.get("telegram_enabled", False) else "configured",
                "policy": "仅推送参数完整、可执行的合格机会",
            },
            "modules": [
                {"id": "ema_12_25", "name": "EMA 12/25", "status": "active", "kind": "strategy"},
                {"id": "box_breakout", "name": "箱体突破融合", "status": "reserved", "kind": "strategy"},
                {"id": "macro_risk", "name": "宏观 / 国际事件风险", "status": "reserved", "kind": "risk"},
                {"id": "multi_alpha", "name": "多策略 Alpha", "status": "reserved", "kind": "strategy"},
                {"id": "backtest", "name": "回测分析", "status": "available", "kind": "research"},
                {"id": "unified_risk", "name": "统一评分与风控", "status": "foundation", "kind": "risk"},
            ],
        }

    def _decorate(self, item: dict, cost_model: str | None = None) -> dict:
        out = dict(item)
        out["reward_risk"] = self._rr(item)
        out["score"], out["score_parts"] = self._score(item, cost_model)
        out["grade"] = self._grade(out["score"])
        return out
