import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.account_api import create_account_router
from backend.account_mailer import DeliveryResult
from backend.account_store import (
    AccountStore, DuplicateUserError, EMAIL_VERIFICATION_TTL_SECONDS, PASSWORD_RESET_TTL_SECONDS,
)
from backend.backup_accounts import backup_database


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

    def test_online_backup_is_consistent(self):
        self.store.create_user("backup@example.com", "Backup User", PASSWORD)
        backup_dir = Path(self.temp_dir.name) / "backups"
        backup = backup_database(self.store.db_path, backup_dir, keep=2)
        restored = AccountStore(backup)
        self.assertIsNotNone(restored.authenticate("backup@example.com", PASSWORD))


class AccountApiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        store = AccountStore(Path(self.temp_dir.name) / "api.sqlite3")
        app = FastAPI()
        self.mailer = FakeMailer()
        app.include_router(create_account_router(
            store,
            allowed_origins=["http://testserver"],
            validate_geometry=validate_polygon,
            secure_cookie=False,
            mailer=self.mailer,
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

    def test_register_zone_export_and_logout_flow(self):
        response = self.client.post("/api/account/register", headers=self.headers, json={
            "display_name": "Test User", "email": "test@example.com", "password": PASSWORD,
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["user"]["email"], "test@example.com")
        self.assertIn("geoai_session", self.client.cookies)

        self.assertEqual(self.client.get("/api/account/me").status_code, 200)

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


if __name__ == "__main__":
    unittest.main()
