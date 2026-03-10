"""Budget control for ClawGate.

Enforces daily/monthly spending limits by querying the requests table.
Uses a short in-memory cache (60s TTL) to avoid hitting the DB on every request.

Configuration via config/models.yaml:
    budget:
      daily_limit: 5.0
      monthly_limit: 100.0
      alert_threshold: 0.8
      action: "reject"        # reject | warn_only

Or override via environment variables:
    CLAWGATE_BUDGET_DAILY=5.0
    CLAWGATE_BUDGET_MONTHLY=100.0
"""

import os
import time
import logging
from typing import Optional, Dict
from pathlib import Path

import yaml

logger = logging.getLogger("clawgate.api.budget")


class BudgetChecker:
    """Checks daily/monthly spend against configured limits."""

    def __init__(self, db_store, config_path: str = "config/models.yaml"):
        self.db_store = db_store

        # Load budget config from models.yaml
        budget_cfg: Dict = {}
        cfg_file = Path(config_path)
        if cfg_file.exists():
            with open(cfg_file) as f:
                full_cfg = yaml.safe_load(f) or {}
                budget_cfg = full_cfg.get("budget", {})

        # Environment overrides take precedence
        self.daily_limit: float = float(
            os.getenv("CLAWGATE_BUDGET_DAILY", budget_cfg.get("daily_limit", 0))
        )
        self.monthly_limit: float = float(
            os.getenv("CLAWGATE_BUDGET_MONTHLY", budget_cfg.get("monthly_limit", 0))
        )
        self.alert_threshold: float = float(budget_cfg.get("alert_threshold", 0.8))
        self.action: str = budget_cfg.get("action", "reject")  # reject | warn_only

        # In-memory cache (avoids querying DB on every request)
        self._cache_ttl = 60  # seconds
        self._daily_cache: Optional[float] = None
        self._daily_cache_ts: float = 0
        self._monthly_cache: Optional[float] = None
        self._monthly_cache_ts: float = 0

        logger.info(
            f"[Budget] daily_limit=${self.daily_limit} monthly_limit=${self.monthly_limit} "
            f"alert={self.alert_threshold} action={self.action}"
        )

    @property
    def enabled(self) -> bool:
        """Budget enforcement is enabled if at least one limit > 0."""
        return self.daily_limit > 0 or self.monthly_limit > 0

    def get_daily_spend(self) -> float:
        """Cached daily spend lookup."""
        now = time.time()
        if self._daily_cache is not None and (now - self._daily_cache_ts) < self._cache_ttl:
            return self._daily_cache
        self._daily_cache = self.db_store.get_daily_spend()
        self._daily_cache_ts = now
        return self._daily_cache

    def get_monthly_spend(self) -> float:
        """Cached monthly spend lookup."""
        now = time.time()
        if self._monthly_cache is not None and (now - self._monthly_cache_ts) < self._cache_ttl:
            return self._monthly_cache
        self._monthly_cache = self.db_store.get_monthly_spend()
        self._monthly_cache_ts = now
        return self._monthly_cache

    def invalidate_cache(self):
        """Force refresh on next check (call after recording a cost)."""
        self._daily_cache = None
        self._monthly_cache = None

    def check(self) -> Dict:
        """Check current spend against limits.

        Returns:
            {
                "allowed": bool,
                "daily_spend": float,
                "daily_limit": float,
                "daily_pct": float,         # 0-1
                "monthly_spend": float,
                "monthly_limit": float,
                "monthly_pct": float,        # 0-1
                "reason": str | None,        # set when not allowed
                "warning": str | None,       # set when above alert threshold
            }
        """
        if not self.enabled:
            return {"allowed": True, "reason": None, "warning": None}

        daily_spend = self.get_daily_spend()
        monthly_spend = self.get_monthly_spend()

        daily_pct = (daily_spend / self.daily_limit) if self.daily_limit > 0 else 0
        monthly_pct = (monthly_spend / self.monthly_limit) if self.monthly_limit > 0 else 0

        result = {
            "allowed": True,
            "daily_spend": round(daily_spend, 6),
            "daily_limit": self.daily_limit,
            "daily_pct": round(daily_pct, 4),
            "monthly_spend": round(monthly_spend, 6),
            "monthly_limit": self.monthly_limit,
            "monthly_pct": round(monthly_pct, 4),
            "reason": None,
            "warning": None,
        }

        # Check daily limit
        if self.daily_limit > 0 and daily_spend >= self.daily_limit:
            msg = f"Daily budget exceeded: ${daily_spend:.4f} / ${self.daily_limit:.2f}"
            if self.action == "reject":
                result["allowed"] = False
                result["reason"] = msg
            else:
                result["warning"] = msg
            logger.warning(f"[Budget] {msg}")

        # Check monthly limit
        if self.monthly_limit > 0 and monthly_spend >= self.monthly_limit:
            msg = f"Monthly budget exceeded: ${monthly_spend:.4f} / ${self.monthly_limit:.2f}"
            if self.action == "reject":
                result["allowed"] = False
                result["reason"] = msg
            else:
                result["warning"] = msg
            logger.warning(f"[Budget] {msg}")

        # Alert threshold warnings
        if result["allowed"] and result["reason"] is None:
            if daily_pct >= self.alert_threshold and self.daily_limit > 0:
                result["warning"] = (
                    f"Daily spend at {daily_pct:.0%}: ${daily_spend:.4f} / ${self.daily_limit:.2f}"
                )
                logger.info(f"[Budget] ⚠️ {result['warning']}")
            elif monthly_pct >= self.alert_threshold and self.monthly_limit > 0:
                result["warning"] = (
                    f"Monthly spend at {monthly_pct:.0%}: ${monthly_spend:.4f} / ${self.monthly_limit:.2f}"
                )
                logger.info(f"[Budget] ⚠️ {result['warning']}")

        return result

    def get_budget_info(self) -> Dict:
        """Return budget status for dashboard display (no enforcement)."""
        daily_spend = self.get_daily_spend()
        monthly_spend = self.get_monthly_spend()
        return {
            "daily_spend": round(daily_spend, 6),
            "daily_limit": self.daily_limit,
            "daily_pct": round((daily_spend / self.daily_limit), 4) if self.daily_limit > 0 else 0,
            "monthly_spend": round(monthly_spend, 6),
            "monthly_limit": self.monthly_limit,
            "monthly_pct": round((monthly_spend / self.monthly_limit), 4) if self.monthly_limit > 0 else 0,
            "alert_threshold": self.alert_threshold,
            "action": self.action,
            "enabled": self.enabled,
        }
