import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.account_api import create_account_router
from backend.account_mailer import DeliveryResult
from backend.account_store import (
    AccountStore, DuplicateUserError, EMAIL_VERIFICATION_TTL_SECONDS,
    ExternalIdentityConflictError, PASSWORD_RESET_TTL_SECONDS,
)
from backend.backup_accounts import backup_database, backup_status


PASSWORD = "correct horse battery staple"
POLYGON = {
    "type": "Polygon",
    "coordinates": [[[68.0, 43.0], [68.1, 43.0], [68.1, 43.1], [68.0, 43.0]]],
}


def validate_polygon(geometry):
    if geometry.get("type") != "Polygon":
        raise HTTPException(status_code=400, detail="Polygon required")


class FakeMailer:
    def __init__(self):
        self.verification_url = None
        self.reset_url = None

    def send_verification(self, user, token, locale="ru"):
        self.verification_url = f"http://testserver/?verify_email={token}"
        return DeliveryResult(sent=True, preview_url=self.verification_url)

    def send_password_reset(self, user, token, locale="ru"):
        self.reset_url = f"http://testserver/?reset_password={token}"
        return DeliveryResult(sent=True, preview_url=self.reset_url)


class AccountStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = AccountStore(Path(self.temp_dir.name) / "accounts.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_account_session_preferences_and_zone_lifecycle(self):
        user = self.store.create_user("Analyst@Example.com", "Test Analyst", PASSWORD)

        self.assertEqual(user["email"], "analyst@example.com")
        self.assertEqual(self.store.authenticate("ANALYST@example.com", PASSWORD)["id"], user["id"])
        self.assertIsNone(self.store.authenticate("analyst@example.com", "wrong password"))

        token = self.store.create_session(user["id"])
        self.assertEqual(self.store.user_for_session(token)["id"], user["id"])
        self.store.revoke_session(token)
        self.assertIsNone(self.store.user_for_session(token))

        preferences = self.store.update_preferences(user["id"], {"default_layer": "ndmi"})
        self.assertEqual(preferences["default_layer"], "ndmi")
        self.assertEqual(preferences["timezone"], "Asia/Qyzylorda")

        zone = self.store.create_zone(user["id"], {"id": "zone-1", "name": "Field 1", "geometry": POLYGON})
        self.assertEqual(zone["name"], "Field 1")
        updated = self.store.update_zone(user["id"], "zone-1", name="Field A", geometry=None)
        self.assertEqual(updated["name"], "Field A")
        self.assertEqual(len(self.store.list_zones(user["id"])), 1)

        imported = self.store.import_zones(user["id"], [{"id": "zone-1", "name": "Duplicate", "geometry": POLYGON}])
        self.assertEqual(imported, 0)
        self.assertTrue(self.store.delete_zone(user["id"], "zone-1"))
        self.assertEqual(self.store.list_zones(user["id"]), [])

    def test_duplicate_email_is_rejected_case_insensitively(self):
        self.store.create_user("owner@example.com", "Owner", PASSWORD)
        with self.assertRaises(DuplicateUserError):
            self.store.create_user("OWNER@example.com", "Other", PASSWORD)

    def test_external_identity_creation_and_explicit_linking(self):
        google_user = self.store.create_external_user(
            provider="google",
            subject="google-subject-1",
            email="google@example.com",
            display_name="Google User",
            locale="kk",
        )
        self.assertFalse(google_user["email_verified"])
        self.assertFalse(google_user["has_password"])
        self.assertEqual(google_user["auth_methods"], ["google"])
        self.assertIsNone(self.store.authenticate("google@example.com", PASSWORD))
        self.assertEqual(
            self.store.get_user_by_external_identity("google", "google-subject-1")["id"],
            google_user["id"],
        )
        self.assertEqual(self.store.get_preferences(google_user["id"])["locale"], "kk")

        local_user = self.store.create_user("local@example.com", "Local User", PASSWORD)
        linked = self.store.link_external_identity(
            local_user["id"],
            provider="google",
            subject="google-subject-2",
            provider_email="local@example.com",
        )
        self.assertEqual(linked["auth_methods"], ["password", "google"])
        self.assertFalse(linked["email_verified"])
        with self.assertRaises(ExternalIdentityConflictError):
            self.store.link_external_identity(
                local_user["id"],
                provider="google",
                subject="google-subject-1",
                provider_email="google@example.com",
            )

    def test_verification_and_password_reset_tokens_are_single_use(self):
        user = self.store.create_user("secure@example.com", "Secure User", PASSWORD)
        session = self.store.create_session(user["id"])

        verification = self.store.create_account_token(
            user["id"], "verify_email", EMAIL_VERIFICATION_TTL_SECONDS
        )
        verified = self.store.verify_email_with_token(verification)
        self.assertTrue(verified["email_verified"])
        self.assertIsNone(self.store.verify_email_with_token(verification))

        reset = self.store.create_account_token(
            user["id"], "reset_password", PASSWORD_RESET_TTL_SECONDS
        )
        updated = self.store.reset_password_with_token(reset, "a completely new secure password")
        self.assertEqual(updated["id"], user["id"])
        self.assertIsNone(self.store.reset_password_with_token(reset, PASSWORD))
        self.assertIsNone(self.store.user_for_session(session))
        self.assertIsNotNone(self.store.authenticate("secure@example.com", "a completely new secure password"))

    def test_legacy_google_verification_is_reset_until_email_link_is_used(self):
        user = self.store.create_external_user(
            provider="google",
            subject="legacy-google-subject",
            email="legacy.google@example.com",
            display_name="Legacy Google User",
            locale="en",
        )
        with closing(sqlite3.connect(self.store.db_path)) as connection:
            connection.execute(
                "UPDATE users SET email_verified_at = ?, email_verified_via = NULL WHERE id = ?",
                ("2026-01-01T00:00:00+00:00", user["id"]),
            )
            connection.commit()

        migrated = AccountStore(self.store.db_path)
        self.assertFalse(migrated.get_user(user["id"])["email_verified"])

        token = migrated.create_account_token(
            user["id"], "verify_email", EMAIL_VERIFICATION_TTL_SECONDS
        )
        self.assertTrue(migrated.verify_email_with_token(token)["email_verified"])
        reopened = AccountStore(self.store.db_path)
        self.assertTrue(reopened.get_user(user["id"])["email_verified"])

    def test_online_backup_is_consistent(self):
        self.store.create_user("backup@example.com", "Backup User", PASSWORD)
        backup_dir = Path(self.temp_dir.name) / "backups"
        backup = backup_database(self.store.db_path, backup_dir, keep=2)
        restored = AccountStore(backup)
        self.assertIsNotNone(restored.authenticate("backup@example.com", PASSWORD))
        status = backup_status(backup_dir, max_age_hours=1)
        self.assertTrue(status["exists"])
        self.assertTrue(status["fresh"])
        self.assertGreater(status["size_bytes"], 0)

    def test_password_change_and_session_management(self):
        user = self.store.create_user("sessions@example.com", "Session User", PASSWORD)
        current = self.store.create_session(
            user["id"], user_agent="Chrome/126 Windows NT 10.0", ip_address="127.0.0.1"
        )
        other = self.store.create_session(
            user["id"], user_agent="Firefox/128 Linux", ip_address="10.0.0.8"
        )

        sessions = self.store.list_sessions(user["id"], current)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sum(item["current"] for item in sessions), 1)
        self.assertIn("Chrome", next(item["device"] for item in sessions if item["current"]))

        self.assertFalse(self.store.change_password(
            user["id"], "wrong password", "a new secure password phrase", current
        ))
        self.assertTrue(self.store.change_password(
            user["id"], PASSWORD, "a new secure password phrase", current
        ))
        self.assertIsNotNone(self.store.user_for_session(current))
        self.assertIsNone(self.store.user_for_session(other))
        self.assertIsNone(self.store.authenticate("sessions@example.com", PASSWORD))
        self.assertIsNotNone(self.store.authenticate(
            "sessions@example.com", "a new secure password phrase"
        ))

    def test_saved_analysis_history_lifecycle(self):
        user = self.store.create_user("history@example.com", "History User", PASSWORD)
        saved = self.store.create_analysis(user["id"], {
            "kind": "point",
            "title": "NDVI point 2025",
            "payload": {"period": "2025_summer", "result": {"ndvi": 0.42}},
        })
        self.assertEqual(saved["kind"], "point")
        self.assertEqual(saved["payload"]["result"]["ndvi"], 0.42)
        self.assertEqual(self.store.list_analyses(user["id"])[0]["id"], saved["id"])
        self.assertEqual(len(self.store.export_account(user["id"])["analyses"]), 1)
        self.assertTrue(self.store.delete_analysis(user["id"], saved["id"]))
        self.assertEqual(self.store.list_analyses(user["id"]), [])

    def test_field_case_lifecycle_keeps_aoi_evidence_snapshot(self):
        user = self.store.create_user("field-work@example.com", "Field Lead", PASSWORD)
        self.store.create_zone(user["id"], {
            "id": "zone-work", "name": "North field", "geometry": POLYGON,
        })
        created = self.store.create_case(user["id"], {
            "zone_id": "zone-work",
            "title": "Inspect dry area",
            "kind": "irrigation",
            "priority": "high",
            "due_date": "2026-07-20",
            "assignee": "Aruzhan",
            "description": "Confirm the moisture anomaly on site",
        })
        self.assertIsNotNone(created)
        self.assertEqual(created["zone_name"], "North field")
        self.assertEqual(created["zone_geometry"], POLYGON)
        self.assertEqual(created["status"], "open")
        self.assertEqual(created["updates"][0]["kind"], "created")

        updated = self.store.update_case(user["id"], created["id"], {
            "status": "in_progress",
            "finding": "Canal gate was blocked",
            "action": "Clear the gate and inspect again",
        })
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["finding"], "Canal gate was blocked")
        self.assertEqual(updated["updates"][0]["kind"], "status")

        log_entry = self.store.add_case_update(user["id"], created["id"], {
            "kind": "field_observation",
            "body": "Low surface moisture confirmed",
            "latitude": 43.05,
            "longitude": 68.05,
            "observed_at": "2026-07-16T09:30:00Z",
            "evidence": {"layer": "ndmi", "period": "2025_summer"},
        })
        self.assertEqual(log_entry["evidence"]["layer"], "ndmi")
        self.assertEqual(self.store.get_case(user["id"], created["id"])["update_count"], 3)

        alert = self.store.create_alert(
            user["id"], "zone-work",
            {"id": "ndmi-low", "index": "ndmi", "operator": "below", "value": 0.2},
            0.12, "2025_summer", "mosaic-v1",
        )
        alert_case = self.store.create_case(user["id"], {
            "zone_id": "zone-work",
            "source_alert_id": alert["id"],
            "title": "Investigate monitoring signal",
            "kind": "irrigation",
            "priority": "urgent",
        })
        duplicate = self.store.create_case(user["id"], {
            "zone_id": "zone-work",
            "source_alert_id": alert["id"],
            "title": "Duplicate work item",
            "kind": "irrigation",
            "priority": "normal",
        })
        self.assertEqual(duplicate["id"], alert_case["id"])
        self.assertEqual(alert_case["source_alert"]["observed_value"], 0.12)

        # A completed investigation remains auditable if its source AOI is later removed.
        self.assertTrue(self.store.delete_zone(user["id"], "zone-work"))
        preserved = self.store.get_case(user["id"], created["id"])
        self.assertEqual(preserved["zone_name"], "North field")
        self.assertEqual(preserved["zone_geometry"], POLYGON)
        self.assertEqual(len(self.store.export_account(user["id"])["field_cases"]), 2)
        self.assertTrue(self.store.delete_case(user["id"], created["id"]))
        self.assertIsNone(self.store.get_case(user["id"], created["id"]))


class AccountApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = AccountStore(Path(self.temp_dir.name) / "api.sqlite3")
        app = FastAPI()
        self.mailer = FakeMailer()
        self.google_identities = {}
        self.ground_truth_calls = []

        def verify_google(credential, client_id):
            self.assertEqual(client_id, "test-client.apps.googleusercontent.com")
            try:
                return self.google_identities[credential]
            except KeyError as exc:
                raise ValueError("invalid Google token") from exc

        def sample_ground_truth(latitude, longitude, period_id):
            self.ground_truth_calls.append((latitude, longitude, period_id))
            return {
                "lat": latitude,
                "lon": longitude,
                "ndvi": 0.61,
                "ndwi": -0.22,
                "ndre": 0.31,
                "ndmi": 0.18,
                "bsi": -0.08,
                "savi": 0.52,
                "nbr": 0.27,
                "ml_class": "agriculture",
                "ml_confidence": 0.86,
                "evidence": {
                    "kind": "derived_observation",
                    "period_id": period_id,
                    "data_version": "test-mosaic-v1",
                },
            }

        app.include_router(create_account_router(
            self.store,
            allowed_origins=["http://testserver"],
            validate_geometry=validate_polygon,
            secure_cookie=False,
            mailer=self.mailer,
            google_client_id="test-client.apps.googleusercontent.com",
            google_token_verifier=verify_google,
            ground_truth_sampler=sample_ground_truth,
        ))
        self.client = TestClient(app)
        self.headers = {"X-Requested-With": "GeoAI-TKO"}

    def tearDown(self):
        self.client.close()
        self.temp_dir.cleanup()

    def test_mutations_require_csrf_header(self):
        response = self.client.post("/api/account/register", json={
            "display_name": "Test User", "email": "test@example.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 403)

    def test_successful_registrations_are_rate_limited_per_client(self):
        for index in range(3):
            response = self.client.post(
                "/api/account/register",
                headers=self.headers,
                json={
                    "display_name": f"Test User {index}",
                    "email": f"register-{index}@example.com",
                    "password": PASSWORD,
                },
            )
            self.assertEqual(response.status_code, 201, response.text)

        rejected = self.client.post(
            "/api/account/register",
            headers=self.headers,
            json={
                "display_name": "Fourth User",
                "email": "register-4@example.com",
                "password": PASSWORD,
            },
        )
        self.assertEqual(rejected.status_code, 429, rejected.text)
        self.assertIn("Retry-After", rejected.headers)

    def test_google_sign_in_requires_app_email_confirmation(self):
        credential = "google-new-user-credential-0001"
        self.google_identities[credential] = {
            "sub": "google-subject-new-user",
            "email": "google.user@example.com",
            "email_verified": True,
            "name": "Google User",
        }

        config = self.client.get("/api/account/auth/config")
        self.assertEqual(config.status_code, 200, config.text)
        self.assertTrue(config.json()["google"]["enabled"])

        login = self.client.post(
            "/api/account/google/login",
            headers=self.headers,
            json={"credential": credential, "locale": "kk"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        user = login.json()["user"]
        self.assertFalse(user["email_verified"])
        self.assertFalse(user["has_password"])
        self.assertEqual(user["auth_methods"], ["google"])
        self.assertEqual(login.json()["preferences"]["locale"], "kk")
        self.assertTrue(login.json()["verification_delivery"]["sent"])
        verification_token = parse_qs(
            urlparse(self.mailer.verification_url).query
        )["verify_email"][0]
        self.assertIn("geoai_session", self.client.cookies)
        self.assertEqual(self.client.get("/api/account/zones").status_code, 403)

        confirmation = self.client.post(
            "/api/account/verification/confirm",
            headers=self.headers,
            json={"token": verification_token},
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.text)
        self.assertTrue(confirmation.json()["user"]["email_verified"])
        self.assertEqual(self.client.get("/api/account/zones").status_code, 200)

        self.client.post("/api/account/logout", headers=self.headers)
        repeated_login = self.client.post(
            "/api/account/google/login",
            headers=self.headers,
            json={"credential": credential, "locale": "kk"},
        )
        self.assertEqual(repeated_login.status_code, 200, repeated_login.text)
        self.assertTrue(repeated_login.json()["user"]["email_verified"])
        self.assertNotIn("verification_delivery", repeated_login.json())

        deleted = self.client.request(
            "DELETE", "/api/account", headers=self.headers, json={"password": None}
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)

    def test_existing_account_requires_explicit_google_link(self):
        registration = self.client.post("/api/account/register", headers=self.headers, json={
            "display_name": "Existing User", "email": "existing@example.com", "password": PASSWORD,
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        user_id = registration.json()["user"]["id"]
        self.client.post("/api/account/logout", headers=self.headers)

        credential = "google-existing-user-credential-01"
        self.google_identities[credential] = {
            "sub": "google-subject-existing-user",
            "email": "existing@example.com",
            "email_verified": True,
            "name": "Existing User",
        }
        collision = self.client.post(
            "/api/account/google/login",
            headers=self.headers,
            json={"credential": credential, "locale": "en"},
        )
        self.assertEqual(collision.status_code, 409, collision.text)
        self.assertEqual(collision.json()["detail"]["code"], "google_link_required")

        password_login = self.client.post("/api/account/login", headers=self.headers, json={
            "email": "existing@example.com", "password": PASSWORD,
        })
        self.assertEqual(password_login.status_code, 200, password_login.text)
        linked = self.client.post(
            "/api/account/google/link",
            headers=self.headers,
            json={"credential": credential, "locale": "en"},
        )
        self.assertEqual(linked.status_code, 200, linked.text)
        self.assertEqual(linked.json()["user"]["auth_methods"], ["password", "google"])
        self.assertFalse(linked.json()["user"]["email_verified"])

        self.client.post("/api/account/logout", headers=self.headers)
        google_login = self.client.post(
            "/api/account/google/login",
            headers=self.headers,
            json={"credential": credential, "locale": "en"},
        )
        self.assertEqual(google_login.status_code, 200, google_login.text)
        self.assertEqual(google_login.json()["user"]["id"], user_id)
        self.assertFalse(google_login.json()["user"]["email_verified"])
        self.assertTrue(google_login.json()["verification_delivery"]["sent"])

    def test_invalid_google_credential_is_rejected(self):
        response = self.client.post(
            "/api/account/google/login",
            headers=self.headers,
            json={"credential": "invalid-google-credential-00001", "locale": "ru"},
        )
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(response.json()["detail"]["code"], "google_token_invalid")

    def test_register_zone_export_and_logout_flow(self):
        response = self.client.post("/api/account/register", headers=self.headers, json={
            "display_name": "Test User", "email": "test@example.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["user"]["email"], "test@example.com")
        self.assertIn("geoai_session", self.client.cookies)

        self.assertEqual(self.client.get("/api/account/me").status_code, 200)

        blocked_zone = self.client.post("/api/account/zones", headers=self.headers, json={
            "id": "zone-api", "name": "API zone", "geometry": POLYGON,
        })
        self.assertEqual(blocked_zone.status_code, 403, blocked_zone.text)
        self.assertEqual(
            blocked_zone.json()["detail"]["code"], "email_verification_required"
        )
        blocked_export = self.client.get("/api/account/export")
        self.assertEqual(blocked_export.status_code, 403, blocked_export.text)

        verification_token = parse_qs(
            urlparse(self.mailer.verification_url).query
        )["verify_email"][0]
        confirmation = self.client.post(
            "/api/account/verification/confirm",
            headers=self.headers,
            json={"token": verification_token},
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.text)

        zone_response = self.client.post("/api/account/zones", headers=self.headers, json={
            "id": "zone-api", "name": "API zone", "geometry": POLYGON,
        })
        self.assertEqual(zone_response.status_code, 201, zone_response.text)
        self.assertEqual(self.client.get("/api/account/zones").json()["zones"][0]["id"], "zone-api")
        self.assertEqual(len(self.client.get("/api/account/export").json()["zones"]), 1)

        logout = self.client.post("/api/account/logout", headers=self.headers)
        self.assertEqual(logout.status_code, 204, logout.text)
        self.assertEqual(self.client.get("/api/account/me").status_code, 401)

    def test_email_verification_and_password_recovery_flow(self):
        registration = self.client.post("/api/account/register", headers=self.headers, json={
            "display_name": "Secure User", "email": "secure@example.com", "password": PASSWORD, "locale": "en",
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        self.assertFalse(registration.json()["user"]["email_verified"])
        verification_token = parse_qs(urlparse(self.mailer.verification_url).query)["verify_email"][0]

        confirmation = self.client.post(
            "/api/account/verification/confirm", headers=self.headers, json={"token": verification_token}
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.text)
        self.assertTrue(confirmation.json()["user"]["email_verified"])
        repeated = self.client.post(
            "/api/account/verification/confirm", headers=self.headers, json={"token": verification_token}
        )
        self.assertEqual(repeated.status_code, 400)

        forgot = self.client.post("/api/account/password/forgot", headers=self.headers, json={
            "email": "secure@example.com", "locale": "kk",
        })
        self.assertEqual(forgot.status_code, 200, forgot.text)
        reset_token = parse_qs(urlparse(self.mailer.reset_url).query)["reset_password"][0]
        new_password = "new recovery passphrase 2026"
        reset = self.client.post("/api/account/password/reset", headers=self.headers, json={
            "token": reset_token, "password": new_password,
        })
        self.assertEqual(reset.status_code, 200, reset.text)
        self.client.post("/api/account/logout", headers=self.headers)
        old_login = self.client.post("/api/account/login", headers=self.headers, json={
            "email": "secure@example.com", "password": PASSWORD,
        })
        self.assertEqual(old_login.status_code, 401)
        new_login = self.client.post("/api/account/login", headers=self.headers, json={
            "email": "secure@example.com", "password": new_password,
        })
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_change_password_and_revoke_other_sessions(self):
        registration = self.client.post(
            "/api/account/register",
            headers={**self.headers, "User-Agent": "Chrome/126 Windows NT 10.0"},
            json={
                "display_name": "Session User",
                "email": "sessions@example.com",
                "password": PASSWORD,
            },
        )
        self.assertEqual(registration.status_code, 201, registration.text)

        other_client = TestClient(self.client.app)
        try:
            other_login = other_client.post(
                "/api/account/login",
                headers={**self.headers, "User-Agent": "Firefox/128 Linux"},
                json={"email": "sessions@example.com", "password": PASSWORD},
            )
            self.assertEqual(other_login.status_code, 200, other_login.text)

            sessions = self.client.get("/api/account/sessions")
            self.assertEqual(sessions.status_code, 200, sessions.text)
            self.assertEqual(len(sessions.json()["sessions"]), 2)
            self.assertEqual(sum(item["current"] for item in sessions.json()["sessions"]), 1)

            wrong = self.client.post(
                "/api/account/password/change",
                headers=self.headers,
                json={
                    "current_password": "wrong password",
                    "new_password": "a replacement secure password",
                },
            )
            self.assertEqual(wrong.status_code, 401)

            changed = self.client.post(
                "/api/account/password/change",
                headers=self.headers,
                json={
                    "current_password": PASSWORD,
                    "new_password": "a replacement secure password",
                },
            )
            self.assertEqual(changed.status_code, 200, changed.text)
            self.assertTrue(changed.json()["other_sessions_revoked"])
            self.assertEqual(other_client.get("/api/account/me").status_code, 401)
            self.assertEqual(self.client.get("/api/account/me").status_code, 200)
            self.assertEqual(
                len(self.client.get("/api/account/sessions").json()["sessions"]), 1
            )
        finally:
            other_client.close()

    def test_saved_analysis_and_synchronized_alert_rules(self):
        registration = self.client.post("/api/account/register", headers=self.headers, json={
            "display_name": "History User", "email": "history@example.com", "password": PASSWORD,
        })
        self.assertEqual(registration.status_code, 201, registration.text)

        preferences = registration.json()["preferences"]
        preferences["threshold_alerts"] = [{
            "id": "ndvi-low", "index": "ndvi", "operator": "below", "value": 0.2,
        }]
        updated = self.client.put(
            "/api/account/preferences", headers=self.headers, json=preferences
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()["preferences"]["threshold_alerts"][0]["id"], "ndvi-low")

        verification_token = parse_qs(
            urlparse(self.mailer.verification_url).query
        )["verify_email"][0]
        confirmation = self.client.post(
            "/api/account/verification/confirm",
            headers=self.headers,
            json={"token": verification_token},
        )
        self.assertEqual(confirmation.status_code, 200, confirmation.text)

        saved = self.client.post("/api/account/analyses", headers=self.headers, json={
            "kind": "zone",
            "title": "Field history",
            "payload": {"period": "2025_summer", "result": {"area_ha": 10.5}},
        })
        self.assertEqual(saved.status_code, 201, saved.text)
        history = self.client.get("/api/account/analyses")
        self.assertEqual(history.status_code, 200, history.text)
        self.assertEqual(history.json()["analyses"][0]["title"], "Field history")
        deleted = self.client.delete(
            f"/api/account/analyses/{saved.json()['id']}", headers=self.headers
        )
        self.assertEqual(deleted.status_code, 204, deleted.text)

    def test_field_case_api_turns_a_zone_into_completed_work(self):
        registration = self.client.post("/api/account/register", headers=self.headers, json={
            "display_name": "Field Manager", "email": "work@example.com", "password": PASSWORD,
        })
        self.assertEqual(registration.status_code, 201, registration.text)
        verification_token = parse_qs(
            urlparse(self.mailer.verification_url).query
        )["verify_email"][0]
        self.assertEqual(self.client.post(
            "/api/account/verification/confirm",
            headers=self.headers,
            json={"token": verification_token},
        ).status_code, 200)
        self.assertEqual(self.client.post("/api/account/zones", headers=self.headers, json={
            "id": "zone-case", "name": "Irrigated block", "geometry": POLYGON,
        }).status_code, 201)
        alert = self.store.create_alert(
            registration.json()["user"]["id"], "zone-case",
            {"id": "ndmi-low", "index": "ndmi", "operator": "below", "value": 0.2},
            0.11, "2025_summer", "mosaic-v1",
        )

        created = self.client.post("/api/account/cases", headers=self.headers, json={
            "zone_id": "zone-case",
            "source_alert_id": alert["id"],
            "title": "Inspect irrigation anomaly",
            "kind": "irrigation",
            "priority": "high",
            "due_date": "2026-07-20",
            "assignee": "Field Manager",
            "description": "Verify the low-moisture signal",
        })
        self.assertEqual(created.status_code, 201, created.text)
        case_id = created.json()["id"]
        self.assertEqual(created.json()["status"], "open")
        self.assertEqual(created.json()["source_alert"]["index"], "ndmi")
        self.assertEqual(self.client.get("/api/account/cases").json()["cases"][0]["id"], case_id)

        duplicate = self.client.post("/api/account/cases", headers=self.headers, json={
            "zone_id": "zone-case",
            "source_alert_id": alert["id"],
            "title": "Duplicate alert task",
            "kind": "irrigation",
            "priority": "normal",
        })
        self.assertEqual(duplicate.status_code, 201, duplicate.text)
        self.assertEqual(duplicate.json()["id"], case_id)

        observation = self.client.post(
            f"/api/account/cases/{case_id}/updates",
            headers=self.headers,
            json={
                "kind": "field_observation",
                "body": "Blocked gate confirmed",
                "latitude": 43.05,
                "longitude": 68.05,
            },
        )
        self.assertEqual(observation.status_code, 201, observation.text)

        validation = self.client.post(
            f"/api/account/cases/{case_id}/validations",
            headers=self.headers,
            json={
                "latitude": 43.02,
                "longitude": 68.07,
                "observed_at": "2025-07-16T09:30:00Z",
                "period_id": "2025_summer",
                "observed_class": "agriculture",
                "observer_confidence": "high",
                "note": "Wheat cover confirmed in the field",
            },
        )
        self.assertEqual(validation.status_code, 201, validation.text)
        self.assertEqual(validation.json()["sample"]["comparison"], "match")
        self.assertEqual(validation.json()["sample"]["indices"]["ndvi"], 0.61)
        self.assertEqual(
            self.ground_truth_calls[-1], (43.02, 68.07, "2025_summer")
        )

        stale_validation = self.client.post(
            f"/api/account/cases/{case_id}/validations",
            headers=self.headers,
            json={
                "latitude": 43.02,
                "longitude": 68.07,
                "observed_at": "2026-07-16T09:30:00Z",
                "period_id": "2025_summer",
                "observed_class": "agriculture",
                "observer_confidence": "medium",
            },
        )
        self.assertEqual(stale_validation.status_code, 201, stale_validation.text)
        self.assertEqual(stale_validation.json()["sample"]["comparison"], "out_of_period")
        dataset = self.client.get("/api/account/ground-truth")
        self.assertEqual(dataset.status_code, 200, dataset.text)
        self.assertEqual(dataset.json()["summary"]["total"], 2)
        self.assertEqual(dataset.json()["summary"]["comparable"], 1)
        self.assertEqual(dataset.json()["summary"]["agreement_percent"], 100.0)

        rejected_close = self.client.patch(
            f"/api/account/cases/{case_id}",
            headers=self.headers,
            json={"status": "closed"},
        )
        self.assertEqual(rejected_close.status_code, 400, rejected_close.text)

        completed = self.client.patch(
            f"/api/account/cases/{case_id}",
            headers=self.headers,
            json={
                "status": "closed",
                "finding": "Irrigation gate was blocked",
                "action": "Gate cleared",
                "resolution": "Moisture restored and follow-up scheduled",
            },
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.assertEqual(completed.json()["status"], "closed")
        self.assertIsNotNone(completed.json()["closed_at"])
        closed_at = completed.json()["closed_at"]
        self.assertGreaterEqual(completed.json()["update_count"], 3)
        edited_closed = self.client.patch(
            f"/api/account/cases/{case_id}",
            headers=self.headers,
            json={"action": "Gate cleared and photographed"},
        )
        self.assertEqual(edited_closed.status_code, 200, edited_closed.text)
        self.assertEqual(edited_closed.json()["closed_at"], closed_at)
        rejected_resolution_removal = self.client.patch(
            f"/api/account/cases/{case_id}",
            headers=self.headers,
            json={"resolution": ""},
        )
        self.assertEqual(rejected_resolution_removal.status_code, 400)
        self.assertEqual(
            self.client.get("/api/account/export").json()["field_cases"][0]["id"],
            case_id,
        )


if __name__ == "__main__":
    unittest.main()
