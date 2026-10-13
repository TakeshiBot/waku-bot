"""Bounded process diagnostics, containing no identities or conversation data."""

import sys
from collections import Counter
from datetime import UTC, datetime


class BusinessDiagnostics:
    def __init__(self):
        self.counts = Counter()
        self.last_failure = None

    def record(self, outcome, stage, http_status=None):
        if outcome not in {"delivered", "failed", "cancelled", "skipped"}:
            raise ValueError("Unknown Business outcome")
        if stage not in {"context", "assessment", "answer", "delivery", "history"}:
            raise ValueError("Unknown Business stage")
        self.counts[(outcome, stage)] += 1
        if outcome == "failed":
            self.last_failure = {
                "stage": stage,
                "http_status": http_status
                if type(http_status) is int and 100 <= http_status <= 599
                else None,
                "time": datetime.now(UTC).isoformat(),
            }
        elif outcome == "delivered":
            self.last_failure = None

    def snapshot(self):
        return {
            "counts": {
                outcome: sum(
                    n for (kind, _), n in self.counts.items() if kind == outcome
                )
                for outcome in ("delivered", "failed", "cancelled", "skipped")
            },
            "failures_by_stage": {
                stage: self.counts[("failed", stage)]
                for stage in ("context", "assessment", "answer", "delivery", "history")
            },
            "last_failure": dict(self.last_failure) if self.last_failure else None,
            "scope": "current_process",
        }


business_diagnostics = BusinessDiagnostics()


def service_snapshot(telegram_client):
    from waku.config import app_config

    telegram = (
        "ready" if telegram_client.is_connected and telegram_client.me else "offline"
    )
    discord = "disabled"
    if app_config.discord_enabled:
        state = sys.modules.get("waku.discordbot.state")
        discord = state.runtime_status() if state is not None else "stopped"
        discord = {"running": "ready", "starting": "connecting"}.get(discord, discord)
    enabled = bool(
        app_config.business_chat_enabled
        and app_config.agent
        and app_config.agent_chat_model
    )
    business = business_diagnostics.snapshot()
    business["status"] = (
        "disabled"
        if not enabled
        else "degraded"
        if business["last_failure"]
        else "enabled"
    )
    return {
        "telegram": {"status": telegram},
        "discord": {"status": discord},
        "business": business,
    }


def public_service_status(telegram_client):
    return {
        name: value["status"]
        for name, value in service_snapshot(telegram_client).items()
    }
