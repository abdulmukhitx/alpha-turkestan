"""Persisted saved-zone monitoring and threshold notification workflow."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable


class MonitoringBusyError(RuntimeError):
    pass


class MonitoringService:
    def __init__(
        self,
        store,
        mailer,
        *,
        latest_period: Callable[[], tuple[str, str] | None],
        calculate_stats: Callable[[dict, str], dict],
        enabled: bool = False,
        interval_seconds: int = 6 * 60 * 60,
        logger: logging.Logger | None = None,
    ):
        self.store = store
        self.mailer = mailer
        self.latest_period = latest_period
        self.calculate_stats = calculate_stats
        self.enabled = enabled
        self.interval_seconds = max(60, interval_seconds)
        self.logger = logger or logging.getLogger("geoai_tko.monitoring")
        self._run_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def rule_matches(rule: dict, value: float) -> bool:
        return value < float(rule["value"]) if rule["operator"] == "below" else value > float(rule["value"])

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "running": self._run_lock.locked(),
            "interval_seconds": self.interval_seconds,
        }

    def run(self, user_id: str | None = None) -> dict:
        if not self._run_lock.acquire(blocking=False):
            raise MonitoringBusyError("a monitoring run is already in progress")

        run_id = self.store.create_monitoring_run(user_id)
        zones_checked = 0
        alerts_created = 0
        alerts_resolved = 0
        zone_errors = 0
        try:
            source = self.latest_period()
            if source is None:
                raise RuntimeError("no monitoring-ready raster period is available")
            period_id, data_version = source
            targets = self.store.monitoring_targets(user_id)
            for target in targets:
                user = target["user"]
                preferences = target["preferences"]
                rules = preferences.get("threshold_alerts") or []
                for zone in target["zones"]:
                    try:
                        rule_ids = {rule["id"] for rule in rules}
                        for stale_alert in self.store.active_alerts_for_zone(user["id"], zone["id"]):
                            if stale_alert["rule_id"] not in rule_ids:
                                self.store.resolve_alert(stale_alert["id"])
                                alerts_resolved += 1

                        observation = self.store.get_zone_observation(
                            user["id"], zone["id"], period_id, data_version,
                        )
                        if observation is None:
                            stats = self.calculate_stats(zone["geometry"], period_id)
                            observation = self.store.save_zone_observation(
                                user["id"], zone["id"], period_id, data_version, stats,
                            )
                        else:
                            stats = observation["stats"]
                        zones_checked += 1

                        for rule in rules:
                            index_stats = (stats.get("indices") or {}).get(rule["index"]) or {}
                            value = index_stats.get("mean")
                            if not isinstance(value, (int, float)):
                                continue
                            active = self.store.active_alert(user["id"], zone["id"], rule["id"])
                            if self.rule_matches(rule, float(value)):
                                if active:
                                    self.store.touch_alert(active["id"], value, period_id, data_version)
                                    continue
                                alert = self.store.create_alert(
                                    user["id"], zone["id"], rule, value, period_id, data_version,
                                )
                                alerts_created += 1
                                try:
                                    delivery = self.mailer.send_monitoring_alert(
                                        user, zone, alert, preferences.get("locale", "ru")
                                    )
                                    self.store.set_alert_delivery(
                                        alert["id"], "sent" if delivery.sent else "not_configured",
                                    )
                                except Exception as exc:
                                    self.store.set_alert_delivery(alert["id"], "failed")
                                    self.logger.warning(
                                        "monitoring alert delivery failed: %s", type(exc).__name__
                                    )
                            elif active:
                                self.store.resolve_alert(active["id"])
                                alerts_resolved += 1
                    except Exception as exc:
                        zone_errors += 1
                        self.logger.exception(
                            "monitoring zone failed user=%s zone=%s error=%s",
                            user["id"], zone["id"], type(exc).__name__,
                        )

            status = "partial" if zone_errors else "complete"
            self.store.finish_monitoring_run(
                run_id,
                status=status,
                zones_checked=zones_checked,
                alerts_created=alerts_created,
                alerts_resolved=alerts_resolved,
                error=f"{zone_errors} zone(s) failed" if zone_errors else None,
            )
            return {
                "run_id": run_id,
                "status": status,
                "period_id": period_id,
                "data_version": data_version,
                "zones_checked": zones_checked,
                "alerts_created": alerts_created,
                "alerts_resolved": alerts_resolved,
                "zone_errors": zone_errors,
            }
        except Exception as exc:
            self.store.finish_monitoring_run(
                run_id,
                status="failed",
                zones_checked=zones_checked,
                alerts_created=alerts_created,
                alerts_resolved=alerts_resolved,
                error=str(exc)[:500],
            )
            raise
        finally:
            self._run_lock.release()

    def start(self) -> bool:
        if not self.enabled or (self._thread and self._thread.is_alive()):
            return False
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._scheduler_loop, daemon=True, name="geoai-monitoring")
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def _scheduler_loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            try:
                self.run()
            except MonitoringBusyError:
                continue
            except Exception as exc:
                self.logger.error("scheduled monitoring failed: %s", type(exc).__name__)
