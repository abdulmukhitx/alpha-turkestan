"""Persistent personal accounts, sessions, preferences, and saved zones.

The store deliberately uses only Python's standard library. SQLite is a good
fit for the current single-instance deployment and keeps the API surface easy
to migrate to PostgreSQL later without coupling the React client to a database.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
import threading
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator


PBKDF2_ITERATIONS = 600_000
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
EMAIL_VERIFICATION_TTL_SECONDS = 60 * 60 * 24
PASSWORD_RESET_TTL_SECONDS = 60 * 60

DEFAULT_PREFERENCES = {
    "locale": "ru",
    "timezone": "Asia/Qyzylorda",
    "default_layer": "ndvi",
    "default_basemap": "satellite",
    "default_period": "2025_summer",
    "default_opacity": 0.85,
    "left_panel_open": True,
    "right_panel_open": False,
    "threshold_alerts": [],
}


class DuplicateUserError(ValueError):
    pass


class ExternalIdentityConflictError(ValueError):
    pass


class DuplicateZoneError(ValueError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_email(value: str) -> str:
    return unicodedata.normalize("NFKC", value.strip()).casefold()


def hash_password(password: str, *, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_text, salt_hex, expected_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_text)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(expected_hex)
    except (TypeError, ValueError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def _describe_user_agent(user_agent: str) -> str:
    """Return a short, stable device label without exposing the full UA string."""
    value = user_agent.casefold()
    if "edg/" in value:
        browser = "Edge"
    elif "firefox/" in value:
        browser = "Firefox"
    elif "chrome/" in value or "crios/" in value:
        browser = "Chrome"
    elif "safari/" in value:
        browser = "Safari"
    else:
        browser = "Browser"

    if "android" in value:
        platform = "Android"
    elif "iphone" in value or "ipad" in value:
        platform = "iOS"
    elif "windows" in value:
        platform = "Windows"
    elif "macintosh" in value or "mac os" in value:
        platform = "macOS"
    elif "linux" in value:
        platform = "Linux"
    else:
        platform = "Unknown device"
    return f"{browser} · {platform}"


class AccountStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._schema_ready = False
        self._schema_lock = threading.Lock()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_schema()
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _ensure_schema(self) -> None:
        if self._schema_ready:
            return
        with self._schema_lock:
            if self._schema_ready:
                return
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.db_path, timeout=10)
            try:
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        display_name TEXT NOT NULL,
                        password_hash TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_login_at TEXT
                    );

                    CREATE TABLE IF NOT EXISTS user_preferences (
                        user_id TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                        payload TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS external_identities (
                        provider TEXT NOT NULL,
                        subject TEXT NOT NULL,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        provider_email TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (provider, subject),
                        UNIQUE (provider, user_id)
                    );
                    CREATE INDEX IF NOT EXISTS external_identities_user_idx
                        ON external_identities(user_id);

                    CREATE TABLE IF NOT EXISTS sessions (
                        token_hash TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        expires_at INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        user_agent TEXT NOT NULL DEFAULT '',
                        ip_address TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS sessions_user_idx ON sessions(user_id);
                    CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);

                    CREATE TABLE IF NOT EXISTS account_tokens (
                        token_hash TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        purpose TEXT NOT NULL CHECK (purpose IN ('verify_email', 'reset_password')),
                        expires_at INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        consumed_at TEXT
                    );
                    CREATE INDEX IF NOT EXISTS account_tokens_user_purpose_idx
                        ON account_tokens(user_id, purpose);
                    CREATE INDEX IF NOT EXISTS account_tokens_expiry_idx
                        ON account_tokens(expires_at);

                    CREATE TABLE IF NOT EXISTS zones (
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        geometry_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, id)
                    );
                    CREATE INDEX IF NOT EXISTS zones_user_updated_idx
                        ON zones(user_id, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS saved_analyses (
                        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        title TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (user_id, id)
                    );
                    CREATE INDEX IF NOT EXISTS saved_analyses_user_created_idx
                        ON saved_analyses(user_id, created_at DESC);
                    """
                )
                user_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(users)").fetchall()
                }
                if "email_verified_at" not in user_columns:
                    connection.execute("ALTER TABLE users ADD COLUMN email_verified_at TEXT")
                if "email_verified_via" not in user_columns:
                    connection.execute("ALTER TABLE users ADD COLUMN email_verified_via TEXT")
                if "last_login_at" not in user_columns:
                    connection.execute("ALTER TABLE users ADD COLUMN last_login_at TEXT")
                # Google used to mark passwordless accounts as application-verified.
                # Requiring our own confirmation email means those legacy Google-only
                # accounts must verify once. Accounts confirmed through our token are
                # tagged below and will not be reset on later startups.
                connection.execute(
                    "UPDATE users SET email_verified_at = NULL "
                    "WHERE email_verified_at IS NOT NULL AND email_verified_via IS NULL "
                    "AND password_hash LIKE 'disabled$%' "
                    "AND EXISTS(SELECT 1 FROM external_identities "
                    "WHERE external_identities.user_id = users.id AND provider = 'google')"
                )
                session_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
                }
                if "session_id" not in session_columns:
                    connection.execute("ALTER TABLE sessions ADD COLUMN session_id TEXT")
                if "user_agent" not in session_columns:
                    connection.execute(
                        "ALTER TABLE sessions ADD COLUMN user_agent TEXT NOT NULL DEFAULT ''"
                    )
                if "ip_address" not in session_columns:
                    connection.execute(
                        "ALTER TABLE sessions ADD COLUMN ip_address TEXT NOT NULL DEFAULT ''"
                    )
                connection.execute(
                    "UPDATE sessions SET session_id = lower(hex(randomblob(16))) "
                    "WHERE session_id IS NULL OR session_id = ''"
                )
                connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS sessions_id_idx ON sessions(session_id)"
                )
                connection.commit()
                self._schema_ready = True
            finally:
                connection.close()

    @staticmethod
    def _public_user(row: sqlite3.Row) -> dict:
        has_password = not row["password_hash"].startswith("disabled$")
        google_linked = bool(row["google_linked"]) if "google_linked" in row.keys() else False
        auth_methods = (["password"] if has_password else []) + (["google"] if google_linked else [])
        return {
            "id": row["id"],
            "email": row["email"],
            "display_name": row["display_name"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "last_login_at": row["last_login_at"],
            "email_verified": bool(row["email_verified_at"]),
            "has_password": has_password,
            "auth_methods": auth_methods,
        }

    @staticmethod
    def _public_zone(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "geometry": json.loads(row["geometry_json"]),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }

    @staticmethod
    def _public_analysis(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "kind": row["kind"],
            "title": row["title"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
        }

    def create_user(self, email: str, display_name: str, password: str) -> dict:
        user_id = str(uuid.uuid4())
        now = utc_now()
        preferences = json.dumps(DEFAULT_PREFERENCES, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO users (id, email, display_name, password_hash, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, normalize_email(email), display_name.strip(), hash_password(password), now, now),
                )
                connection.execute(
                    "INSERT INTO user_preferences (user_id, payload, updated_at) VALUES (?, ?, ?)",
                    (user_id, preferences, now),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateUserError("account already exists") from exc
        return self.get_user(user_id)

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT users.*, EXISTS(SELECT 1 FROM external_identities "
                "WHERE external_identities.user_id = users.id AND provider = 'google') AS google_linked "
                "FROM users WHERE users.id = ?",
                (user_id,),
            ).fetchone()
        return self._public_user(row) if row else None

    def get_user_by_email(self, email: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT users.*, EXISTS(SELECT 1 FROM external_identities "
                "WHERE external_identities.user_id = users.id AND provider = 'google') AS google_linked "
                "FROM users WHERE users.email = ? COLLATE NOCASE",
                (normalize_email(email),),
            ).fetchone()
        return self._public_user(row) if row else None

    def authenticate(self, email: str, password: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT users.*, EXISTS(SELECT 1 FROM external_identities "
                "WHERE external_identities.user_id = users.id AND provider = 'google') AS google_linked "
                "FROM users WHERE users.email = ? COLLATE NOCASE",
                (normalize_email(email),),
            ).fetchone()
        if row is None:
            # Keep unknown-account requests computationally expensive too.
            hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), b"geoai-tko-dummy", PBKDF2_ITERATIONS)
            return None
        return self._public_user(row) if verify_password(password, row["password_hash"]) else None

    def get_user_by_external_identity(self, provider: str, subject: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT users.*, EXISTS(SELECT 1 FROM external_identities AS linked "
                "WHERE linked.user_id = users.id AND linked.provider = 'google') AS google_linked "
                "FROM external_identities JOIN users ON users.id = external_identities.user_id "
                "WHERE external_identities.provider = ? AND external_identities.subject = ?",
                (provider, subject),
            ).fetchone()
        return self._public_user(row) if row else None

    def create_external_user(
        self,
        *,
        provider: str,
        subject: str,
        email: str,
        display_name: str,
        locale: str = "ru",
    ) -> dict:
        user_id = str(uuid.uuid4())
        now = utc_now()
        preferences = {**DEFAULT_PREFERENCES, "locale": locale}
        preferences_json = json.dumps(preferences, ensure_ascii=False, separators=(",", ":"))
        disabled_password = f"disabled${secrets.token_urlsafe(32)}"
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO users "
                    "(id, email, display_name, password_hash, created_at, updated_at, email_verified_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        normalize_email(email),
                        display_name.strip(),
                        disabled_password,
                        now,
                        now,
                        None,
                    ),
                )
                connection.execute(
                    "INSERT INTO user_preferences (user_id, payload, updated_at) VALUES (?, ?, ?)",
                    (user_id, preferences_json, now),
                )
                connection.execute(
                    "INSERT INTO external_identities "
                    "(provider, subject, user_id, provider_email, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (provider, subject, user_id, normalize_email(email), now, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ExternalIdentityConflictError("external identity or email already exists") from exc
        return self.get_user(user_id)

    def link_external_identity(
        self, user_id: str, *, provider: str, subject: str, provider_email: str
    ) -> dict:
        now = utc_now()
        normalized_provider_email = normalize_email(provider_email)
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT user_id, subject FROM external_identities WHERE provider = ? "
                "AND (subject = ? OR user_id = ?)",
                (provider, subject, user_id),
            ).fetchall()
            if existing:
                if len(existing) == 1 and existing[0]["user_id"] == user_id and existing[0]["subject"] == subject:
                    connection.execute(
                        "UPDATE external_identities SET provider_email = ?, updated_at = ? "
                        "WHERE provider = ? AND subject = ?",
                        (normalized_provider_email, now, provider, subject),
                    )
                else:
                    raise ExternalIdentityConflictError("external identity is already linked")
            else:
                try:
                    connection.execute(
                        "INSERT INTO external_identities "
                        "(provider, subject, user_id, provider_email, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (provider, subject, user_id, normalized_provider_email, now, now),
                    )
                except sqlite3.IntegrityError as exc:
                    raise ExternalIdentityConflictError("external identity is already linked") from exc
        return self.get_user(user_id)

    def update_external_identity_email(
        self, provider: str, subject: str, provider_email: str
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE external_identities SET provider_email = ?, updated_at = ? "
                "WHERE provider = ? AND subject = ?",
                (normalize_email(provider_email), utc_now(), provider, subject),
            )

    def verify_user_password(self, user_id: str, password: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return bool(row and verify_password(password, row["password_hash"]))

    def update_profile(self, user_id: str, display_name: str) -> dict:
        now = utc_now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE users SET display_name = ?, updated_at = ? WHERE id = ?",
                (display_name.strip(), now, user_id),
            )
        return self.get_user(user_id)

    def get_preferences(self, user_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM user_preferences WHERE user_id = ?", (user_id,)
            ).fetchone()
        stored = json.loads(row["payload"]) if row else {}
        return {**DEFAULT_PREFERENCES, **stored}

    def update_preferences(self, user_id: str, preferences: dict) -> dict:
        current = self.get_preferences(user_id)
        current.update(preferences)
        now = utc_now()
        payload = json.dumps(current, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO user_preferences (user_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (user_id, payload, now),
            )
        return current

    def create_session(
        self,
        user_id: str,
        ttl_seconds: int = SESSION_TTL_SECONDS,
        *,
        user_agent: str = "",
        ip_address: str = "",
    ) -> str:
        token = secrets.token_urlsafe(32)
        session_id = str(uuid.uuid4())
        now = utc_now()
        expires_at = int(datetime.now(timezone.utc).timestamp()) + ttl_seconds
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (int(datetime.now(timezone.utc).timestamp()),))
            connection.execute(
                "INSERT INTO sessions "
                "(token_hash, session_id, user_id, expires_at, created_at, last_seen_at, user_agent, ip_address) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _token_digest(token), session_id, user_id, expires_at, now, now,
                    user_agent[:512], ip_address[:64],
                ),
            )
            connection.execute(
                "UPDATE users SET last_login_at = ? WHERE id = ?", (now, user_id)
            )
        return token

    def user_for_session(self, token: str | None) -> dict | None:
        if not token:
            return None
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        digest = _token_digest(token)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT users.*, EXISTS(SELECT 1 FROM external_identities "
                "WHERE external_identities.user_id = users.id AND provider = 'google') AS google_linked "
                "FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE sessions.token_hash = ? AND sessions.expires_at > ?",
                (digest, now_epoch),
            ).fetchone()
            if row:
                connection.execute(
                    "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?", (utc_now(), digest)
                )
        return self._public_user(row) if row else None

    def revoke_session(self, token: str | None) -> None:
        if not token:
            return
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_digest(token),))

    def list_sessions(self, user_id: str, current_token: str | None) -> list[dict]:
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        current_digest = _token_digest(current_token) if current_token else ""
        with self._connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_epoch,))
            rows = connection.execute(
                "SELECT * FROM sessions WHERE user_id = ? ORDER BY last_seen_at DESC",
                (user_id,),
            ).fetchall()
        return [
            {
                "id": row["session_id"],
                "device": _describe_user_agent(row["user_agent"]),
                "ip_address": row["ip_address"],
                "created_at": row["created_at"],
                "last_seen_at": row["last_seen_at"],
                "expires_at": datetime.fromtimestamp(
                    row["expires_at"], timezone.utc
                ).isoformat(timespec="seconds"),
                "current": bool(current_digest) and hmac.compare_digest(
                    row["token_hash"], current_digest
                ),
            }
            for row in rows
        ]

    def revoke_user_session(
        self, user_id: str, session_id: str, current_token: str | None
    ) -> dict:
        current_digest = _token_digest(current_token) if current_token else ""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            ).fetchone()
            if not row:
                return {"revoked": False, "current": False}
            is_current = bool(current_digest) and hmac.compare_digest(
                row["token_hash"], current_digest
            )
            connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND session_id = ?",
                (user_id, session_id),
            )
        return {"revoked": True, "current": is_current}

    def revoke_other_sessions(self, user_id: str, current_token: str | None) -> int:
        if not current_token:
            return 0
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user_id, _token_digest(current_token)),
            )
        return cursor.rowcount

    def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
        current_token: str | None,
    ) -> bool:
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM users WHERE id = ?", (user_id,)
            ).fetchone()
            if not row or not verify_password(current_password, row["password_hash"]):
                return False
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(new_password), now, user_id),
            )
            connection.execute(
                "DELETE FROM account_tokens WHERE user_id = ? AND purpose = 'reset_password'",
                (user_id,),
            )
            if current_token:
                connection.execute(
                    "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                    (user_id, _token_digest(current_token)),
                )
            else:
                connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        return True

    def health_status(self) -> dict:
        """Cheap readiness check for the account database."""
        try:
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
            return {
                "ok": True,
                "engine": "sqlite",
            }
        except (OSError, sqlite3.Error) as exc:
            return {
                "ok": False,
                "engine": "sqlite",
                "error": type(exc).__name__,
            }

    def create_account_token(self, user_id: str, purpose: str, ttl_seconds: int) -> str:
        if purpose not in {"verify_email", "reset_password"}:
            raise ValueError("unsupported account token purpose")
        token = secrets.token_urlsafe(32)
        now = utc_now()
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        with self._connect() as connection:
            connection.execute("DELETE FROM account_tokens WHERE expires_at <= ?", (now_epoch,))
            connection.execute(
                "DELETE FROM account_tokens WHERE user_id = ? AND purpose = ?",
                (user_id, purpose),
            )
            connection.execute(
                "INSERT INTO account_tokens "
                "(token_hash, user_id, purpose, expires_at, created_at, consumed_at) "
                "VALUES (?, ?, ?, ?, ?, NULL)",
                (_token_digest(token), user_id, purpose, now_epoch + ttl_seconds, now),
            )
        return token

    def verify_email_with_token(self, token: str) -> dict | None:
        digest = _token_digest(token)
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM account_tokens "
                "WHERE token_hash = ? AND purpose = 'verify_email' "
                "AND consumed_at IS NULL AND expires_at > ?",
                (digest, now_epoch),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE account_tokens SET consumed_at = ? WHERE token_hash = ?",
                (now, digest),
            )
            connection.execute(
                "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?), "
                "email_verified_via = 'email', updated_at = ? "
                "WHERE id = ?",
                (now, now, row["user_id"]),
            )
            user = connection.execute(
                "SELECT * FROM users WHERE id = ?", (row["user_id"],)
            ).fetchone()
        return self._public_user(user) if user else None

    def reset_password_with_token(self, token: str, password: str) -> dict | None:
        digest = _token_digest(token)
        now_epoch = int(datetime.now(timezone.utc).timestamp())
        now = utc_now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT user_id FROM account_tokens "
                "WHERE token_hash = ? AND purpose = 'reset_password' "
                "AND consumed_at IS NULL AND expires_at > ?",
                (digest, now_epoch),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                "UPDATE account_tokens SET consumed_at = ? WHERE token_hash = ?",
                (now, digest),
            )
            connection.execute(
                "UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?",
                (hash_password(password), now, row["user_id"]),
            )
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (row["user_id"],))
            user = connection.execute(
                "SELECT * FROM users WHERE id = ?", (row["user_id"],)
            ).fetchone()
        return self._public_user(user) if user else None

    def list_zones(self, user_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM zones WHERE user_id = ? ORDER BY updated_at DESC", (user_id,)
            ).fetchall()
        return [self._public_zone(row) for row in rows]

    def create_zone(self, user_id: str, zone: dict) -> dict:
        zone_id = zone.get("id") or str(uuid.uuid4())
        now = utc_now()
        created_at = zone.get("createdAt") or now
        updated_at = zone.get("updatedAt") or now
        geometry_json = json.dumps(zone["geometry"], ensure_ascii=False, separators=(",", ":"))
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO zones (user_id, id, name, geometry_json, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, zone_id, zone["name"].strip(), geometry_json, created_at, updated_at),
                )
            except sqlite3.IntegrityError as exc:
                raise DuplicateZoneError("zone already exists") from exc
            row = connection.execute(
                "SELECT * FROM zones WHERE user_id = ? AND id = ?", (user_id, zone_id)
            ).fetchone()
        return self._public_zone(row)

    def update_zone(self, user_id: str, zone_id: str, *, name: str | None, geometry: dict | None) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM zones WHERE user_id = ? AND id = ?", (user_id, zone_id)
            ).fetchone()
            if not row:
                return None
            next_name = name.strip() if name is not None else row["name"]
            next_geometry = (
                json.dumps(geometry, ensure_ascii=False, separators=(",", ":"))
                if geometry is not None else row["geometry_json"]
            )
            connection.execute(
                "UPDATE zones SET name = ?, geometry_json = ?, updated_at = ? "
                "WHERE user_id = ? AND id = ?",
                (next_name, next_geometry, utc_now(), user_id, zone_id),
            )
            updated = connection.execute(
                "SELECT * FROM zones WHERE user_id = ? AND id = ?", (user_id, zone_id)
            ).fetchone()
        return self._public_zone(updated)

    def delete_zone(self, user_id: str, zone_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM zones WHERE user_id = ? AND id = ?", (user_id, zone_id)
            )
        return result.rowcount > 0

    def import_zones(self, user_id: str, zones: list[dict]) -> int:
        imported = 0
        with self._connect() as connection:
            for zone in zones:
                now = utc_now()
                result = connection.execute(
                    "INSERT OR IGNORE INTO zones "
                    "(user_id, id, name, geometry_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        user_id,
                        zone["id"],
                        zone["name"].strip(),
                        json.dumps(zone["geometry"], ensure_ascii=False, separators=(",", ":")),
                        zone.get("createdAt") or now,
                        zone.get("updatedAt") or now,
                    ),
                )
                imported += result.rowcount
        return imported

    def list_analyses(self, user_id: str, limit: int = 100) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM saved_analyses WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, max(1, min(limit, 100))),
            ).fetchall()
        return [self._public_analysis(row) for row in rows]

    def create_analysis(self, user_id: str, analysis: dict) -> dict:
        analysis_id = str(uuid.uuid4())
        created_at = utc_now()
        payload_json = json.dumps(
            analysis["payload"], ensure_ascii=False, separators=(",", ":")
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO saved_analyses "
                "(user_id, id, kind, title, payload_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    user_id,
                    analysis_id,
                    analysis["kind"],
                    analysis["title"].strip(),
                    payload_json,
                    created_at,
                ),
            )
            connection.execute(
                "DELETE FROM saved_analyses WHERE user_id = ? AND id IN ("
                "SELECT id FROM saved_analyses WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT -1 OFFSET 100)",
                (user_id, user_id),
            )
            row = connection.execute(
                "SELECT * FROM saved_analyses WHERE user_id = ? AND id = ?",
                (user_id, analysis_id),
            ).fetchone()
        return self._public_analysis(row)

    def delete_analysis(self, user_id: str, analysis_id: str) -> bool:
        with self._connect() as connection:
            result = connection.execute(
                "DELETE FROM saved_analyses WHERE user_id = ? AND id = ?",
                (user_id, analysis_id),
            )
        return result.rowcount > 0

    def export_account(self, user_id: str) -> dict:
        return {
            "exported_at": utc_now(),
            "user": self.get_user(user_id),
            "preferences": self.get_preferences(user_id),
            "zones": self.list_zones(user_id),
            "analyses": self.list_analyses(user_id),
        }

    def delete_account(self, user_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
