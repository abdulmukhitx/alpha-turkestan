import tempfile
import unittest
from pathlib import Path

from backend.account_store import AccountStore, EMAIL_VERIFICATION_TTL_SECONDS
from backend.monitoring import MonitoringService


PASSWORD = "correct horse battery staple"
POLYGON = {
    "type": "Polygon",
    "coordinates": [[[68.0, 43.0], [68.1, 43.0], [68.1, 43.1], [68.0, 43.0]]],
}


class FakeDelivery:
    sent = True


class FakeMailer:
    def __init__(self):
        self.alerts = []

    def send_monitoring_alert(self, user, zone, alert, locale="ru"):
        self.alerts.append((user, zone, alert, locale))
        return FakeDelivery()


class MonitoringServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = AccountStore(Path(self.temp_dir.name) / "monitoring.sqlite3")
        user = self.store.create_user("monitor@example.com", "Monitor User", PASSWORD)
        token = self.store.create_account_token(
            user["id"], "verify_email", EMAIL_VERIFICATION_TTL_SECONDS,
        )
        self.user = self.store.verify_email_with_token(token)
        self.zone = self.store.create_zone(self.user["id"], {
            "id": "field-1", "name": "Field 1", "geometry": POLYGON,
        })
        preferences = self.store.get_preferences(self.user["id"])
        preferences["threshold_alerts"] = [{
            "id": "low-ndvi", "index": "ndvi", "operator": "below", "value": 0.2,
        }]
        self.store.update_preferences(self.user["id"], preferences)
        self.mailer = FakeMailer()
        self.data_version = "v1"
        self.ndvi = 0.1
        self.calculate_calls = 0

        def calculate(_geometry, _period):
            self.calculate_calls += 1
            return {"area_ha": 10, "indices": {"ndvi": {"mean": self.ndvi}}, "lulc": {}}

        self.service = MonitoringService(
            self.store,
            self.mailer,
            latest_period=lambda: ("2025_summer", self.data_version),
            calculate_stats=calculate,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_alert_is_persisted_delivered_and_deduplicated(self):
        first = self.service.run(self.user["id"])
        self.assertEqual(first["alerts_created"], 1)
        self.assertEqual(first["zones_checked"], 1)
        self.assertEqual(len(self.mailer.alerts), 1)
        self.assertEqual(self.store.list_alerts(self.user["id"])[0]["delivery_status"], "sent")

        second = self.service.run(self.user["id"])
        self.assertEqual(second["alerts_created"], 0)
        self.assertEqual(len(self.mailer.alerts), 1)
        self.assertEqual(self.calculate_calls, 1, "same data version should reuse the observation cache")

    def test_alert_resolves_when_a_new_observation_returns_to_normal(self):
        self.service.run(self.user["id"])
        self.data_version = "v2"
        self.ndvi = 0.35
        result = self.service.run(self.user["id"])

        self.assertEqual(result["alerts_resolved"], 1)
        alert = self.store.list_alerts(self.user["id"])[0]
        self.assertEqual(alert["status"], "resolved")
        self.assertIsNotNone(alert["resolved_at"])

    def test_open_alert_can_be_acknowledged_without_closing_condition(self):
        self.service.run(self.user["id"])
        alert = self.store.list_alerts(self.user["id"])[0]
        acknowledged = self.store.acknowledge_alert(self.user["id"], alert["id"])
        self.assertEqual(acknowledged["status"], "acknowledged")
        self.assertIsNotNone(self.store.active_alert(self.user["id"], self.zone["id"], "low-ndvi"))

    def test_removing_a_rule_resolves_its_active_alert(self):
        self.service.run(self.user["id"])
        preferences = self.store.get_preferences(self.user["id"])
        preferences["threshold_alerts"] = []
        self.store.update_preferences(self.user["id"], preferences)

        result = self.service.run(self.user["id"])

        self.assertEqual(result["alerts_resolved"], 1)
        alert = self.store.list_alerts(self.user["id"])[0]
        self.assertEqual(alert["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
