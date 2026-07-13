# Personal account production checklist

The current account store is SQLite and is intended for one running backend instance. It supports personal profiles, 30-day sessions, email verification, password recovery, saved zones, and account export/deletion. Organization roles are intentionally not part of this phase.

## HTTPS and cookies

Set these values in the production environment:

```dotenv
PUBLIC_APP_URL=https://maps.example.kz
CORS_ORIGINS=https://maps.example.kz
SESSION_COOKIE_SECURE=true
ACCOUNT_DEV_EMAILS=false
```

Terminate TLS at the reverse proxy and forward requests only to the private FastAPI service. `SESSION_COOKIE_SECURE=true` changes the session cookie to the `__Host-` form and requires HTTPS.

## Email delivery

Configure an SMTP account dedicated to transactional email:

```dotenv
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=geoai-mailer
SMTP_PASSWORD=replace-with-a-secret-from-the-deployment-secret-store
SMTP_FROM=GeoAI TKO <no-reply@example.kz>
SMTP_STARTTLS=true
```

Do not commit the real password. In local development, `ACCOUNT_DEV_EMAILS=true` stores previews under `data/account_mailbox/` and the UI exposes a test-only action link.

## Backups

Create a consistent backup while the API is running:

```powershell
.\.venv\Scripts\python.exe backend\backup_accounts.py --keep 14
```

Schedule this command daily, copy the resulting `backups/accounts/` files to encrypted off-host storage, and periodically test a restore. The command uses SQLite's online backup API and runs an integrity check before retaining the file.

## When to move to PostgreSQL

Keep SQLite while there is one backend instance and moderate account traffic. Move to PostgreSQL before running multiple API replicas, needing high write concurrency, or requiring managed point-in-time recovery. The HTTP API and frontend do not depend on SQLite-specific behavior, so that migration can be isolated to the account store.
