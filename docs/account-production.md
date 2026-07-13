# Personal account production checklist

The current account store is SQLite and is intended for one running backend instance. It supports personal profiles, password and Google sign-in, 30-day sessions, email verification, password recovery, saved zones, and account export/deletion. Organization roles are intentionally not part of this phase.

## HTTPS and cookies

Set these values in the production environment:

```dotenv
PUBLIC_APP_URL=https://maps.example.kz
CORS_ORIGINS=https://maps.example.kz
SESSION_COOKIE_SECURE=true
ACCOUNT_DEV_EMAILS=false
```

Terminate TLS at the reverse proxy and forward requests only to the private FastAPI service. `SESSION_COOKIE_SECURE=true` changes the session cookie to the `__Host-` form and requires HTTPS.

## Google sign-in

1. In [Google Auth Platform](https://console.cloud.google.com/auth/overview), configure the application branding and audience.
2. Create an OAuth 2.0 client with application type **Web application**.
3. Add every exact frontend origin under **Authorized JavaScript origins**. Google requires both `http://localhost` and `http://localhost:3000` for local development; also add the HTTPS production origin. Do not add paths or trailing slashes.
4. Put the public web client ID in the backend environment:

```dotenv
GOOGLE_CLIENT_ID=000000000000-example.apps.googleusercontent.com
```

Install the updated Python requirements before restarting the backend. When the OAuth app is in testing mode, add the intended Google accounts as test users. Production deployments should publish the consent configuration and use an authorized HTTPS origin.

The frontend uses the official Google Identity Services button and sends its short-lived ID token to the backend. The backend verifies the signature, issuer, audience, expiry, and verified-email claim before creating the normal GeoAI session cookie. Only Google's stable subject identifier and provider email are stored; Google access tokens and refresh tokens are not requested or retained. The client ID is intentionally public—do not create or expose a Google client secret for this flow.

If the deployment sets a Content Security Policy, allow the Google Identity Services resources documented in Google's [CSP integration guide](https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid#content_security_policy).

## Email delivery

Password registrations receive a single-use verification link that expires after 24 hours. Until the link is opened, the user can manage the profile and request another email, but cloud zones, saved analyses, imports, and account export are blocked. Google accounts do not receive a second verification email: the backend accepts them only when Google's signed ID token contains `email_verified=true`.

Configure an SMTP account dedicated to transactional email:

```dotenv
PUBLIC_APP_URL=https://your-frontend.example.kz
ACCOUNT_DEV_EMAILS=false
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=geoai-mailer
SMTP_PASSWORD=replace-with-a-secret-from-the-deployment-secret-store
SMTP_FROM=GeoAI TKO <no-reply@example.kz>
SMTP_STARTTLS=true
```

Do not commit or paste the real SMTP password into chat; store it in the deployment secret store. In local development, `ACCOUNT_DEV_EMAILS=true` stores previews under `data/account_mailbox/` and the UI exposes a test-only action link instead of sending a real email.

## Backups

Create a consistent backup while the API is running:

```powershell
.\.venv\Scripts\python.exe backend\backup_accounts.py --keep 14
```

Schedule this command daily, copy the resulting `backups/accounts/` files to encrypted off-host storage, and periodically test a restore. The command uses SQLite's online backup API and runs an integrity check before retaining the file.

After scheduling it, enable backup freshness in service health checks:

```dotenv
ACCOUNT_BACKUP_DIR=backups/accounts
BACKUP_MAX_AGE_HOURS=36
BACKUP_HEALTH_REQUIRED=true
```

Each successful backup atomically updates `backups/accounts/latest.json`. `/health` reports whether the latest file exists and is fresh. A required missing or stale backup changes the service status to `degraded`.

For Windows Task Scheduler, run the command from the repository directory with the production virtual environment. Use a service account that can read the SQLite database and write only to the backup directory. Do not run the task as an administrator unless the deployment specifically requires it.

## Monitoring and logs

The backend emits one-line JSON request events containing a request ID, route, status, duration, and client address. Tile and static-data traffic is excluded to keep normal map navigation from flooding logs. Failed verification and recovery email delivery is also recorded without logging addresses, tokens, passwords, or request bodies.

Set `LOG_LEVEL=INFO` and collect standard output with the process supervisor. Monitor:

- `/health` status and the nested account database, email, and backup fields;
- HTTP 5xx counts and slow `duration_ms` values;
- `account_email_failed` events;
- scheduled-backup exit status and off-host copy status.

The `X-Request-ID` response header can be used to correlate a user-visible failure with backend logs.

## When to move to PostgreSQL

Keep SQLite while there is one backend instance and moderate account traffic. Move to PostgreSQL before running multiple API replicas, needing high write concurrency, or requiring managed point-in-time recovery. The HTTP API and frontend do not depend on SQLite-specific behavior, so that migration can be isolated to the account store.
