"""SMTP and development delivery for account verification and recovery."""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode


@dataclass(frozen=True)
class DeliveryResult:
    sent: bool
    preview_url: str | None = None

    def public_payload(self) -> dict:
        payload = {"sent": self.sent}
        if self.preview_url:
            payload["preview_url"] = self.preview_url
        return payload


MAIL_COPY = {
    "ru": {
        "verify_subject": "Подтвердите email — GeoAI TKO",
        "verify_intro": "Подтвердите адрес электронной почты, чтобы защитить аккаунт GeoAI TKO.",
        "verify_action": "Подтвердить email",
        "verify_expiry": "Ссылка действует 24 часа.",
        "reset_subject": "Сброс пароля — GeoAI TKO",
        "reset_intro": "Мы получили запрос на сброс пароля аккаунта GeoAI TKO.",
        "reset_action": "Создать новый пароль",
        "reset_expiry": "Ссылка действует 1 час. Если вы не отправляли запрос, ничего не делайте.",
    },
    "kk": {
        "verify_subject": "Email мекенжайын растаңыз — GeoAI TKO",
        "verify_intro": "GeoAI TKO аккаунтын қорғау үшін электрондық пошта мекенжайын растаңыз.",
        "verify_action": "Email мекенжайын растау",
        "verify_expiry": "Сілтеме 24 сағат жарамды.",
        "reset_subject": "Құпиясөзді қалпына келтіру — GeoAI TKO",
        "reset_intro": "GeoAI TKO аккаунтының құпиясөзін қалпына келтіру сұрауын алдық.",
        "reset_action": "Жаңа құпиясөз жасау",
        "reset_expiry": "Сілтеме 1 сағат жарамды. Сұрауды сіз жібермесеңіз, ештеңе істемеңіз.",
    },
    "en": {
        "verify_subject": "Verify your email — GeoAI TKO",
        "verify_intro": "Verify your email address to protect your GeoAI TKO account.",
        "verify_action": "Verify email",
        "verify_expiry": "This link is valid for 24 hours.",
        "reset_subject": "Reset your password — GeoAI TKO",
        "reset_intro": "We received a request to reset your GeoAI TKO account password.",
        "reset_action": "Create a new password",
        "reset_expiry": "This link is valid for 1 hour. If you did not request it, no action is needed.",
    },
}


class AccountMailer:
    def __init__(
        self,
        *,
        public_app_url: str,
        from_address: str,
        smtp_host: str | None = None,
        smtp_port: int = 587,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_starttls: bool = True,
        development_mode: bool = False,
        outbox_dir: str | Path | None = None,
    ):
        self.public_app_url = public_app_url.rstrip("/")
        self.from_address = from_address
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.smtp_username = smtp_username
        self.smtp_password = smtp_password
        self.smtp_starttls = smtp_starttls
        self.development_mode = development_mode
        self.outbox_dir = Path(outbox_dir) if outbox_dir else None

    @classmethod
    def from_environment(cls, base_dir: str | Path) -> "AccountMailer":
        smtp_host = os.getenv("SMTP_HOST") or None
        development_mode = os.getenv(
            "ACCOUNT_DEV_EMAILS", "false" if smtp_host else "true"
        ).lower() in {"1", "true", "yes"}
        return cls(
            public_app_url=os.getenv("PUBLIC_APP_URL", "http://localhost:3000"),
            from_address=os.getenv("SMTP_FROM", "GeoAI TKO <no-reply@localhost>"),
            smtp_host=smtp_host,
            smtp_port=int(os.getenv("SMTP_PORT", "587")),
            smtp_username=os.getenv("SMTP_USERNAME") or None,
            smtp_password=os.getenv("SMTP_PASSWORD") or None,
            smtp_starttls=os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes"},
            development_mode=development_mode,
            outbox_dir=Path(base_dir) / "data" / "account_mailbox",
        )

    def _action_url(self, query_key: str, token: str) -> str:
        return f"{self.public_app_url}/?{urlencode({query_key: token})}"

    def send_verification(self, user: dict, token: str, locale: str = "ru") -> DeliveryResult:
        url = self._action_url("verify_email", token)
        return self._deliver(user, url, locale, "verify")

    def send_password_reset(self, user: dict, token: str, locale: str = "ru") -> DeliveryResult:
        url = self._action_url("reset_password", token)
        return self._deliver(user, url, locale, "reset")

    def _deliver(self, user: dict, url: str, locale: str, purpose: str) -> DeliveryResult:
        copy = MAIL_COPY.get(locale, MAIL_COPY["ru"])
        subject = copy[f"{purpose}_subject"]
        body = (
            f"{copy[f'{purpose}_intro']}\n\n"
            f"{copy[f'{purpose}_action']}:\n{url}\n\n"
            f"{copy[f'{purpose}_expiry']}\n"
        )

        if self.development_mode:
            if self.outbox_dir:
                self.outbox_dir.mkdir(parents=True, exist_ok=True)
                message_path = self.outbox_dir / f"{uuid.uuid4().hex}.json"
                message_path.write_text(
                    json.dumps(
                        {"to": user["email"], "subject": subject, "body": body, "action_url": url},
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            return DeliveryResult(sent=True, preview_url=url)

        if not self.smtp_host:
            return DeliveryResult(sent=False)

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_address
        message["To"] = user["email"]
        message.set_content(body)
        with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=15) as client:
            if self.smtp_starttls:
                client.starttls(context=ssl.create_default_context())
            if self.smtp_username:
                client.login(self.smtp_username, self.smtp_password or "")
            client.send_message(message)
        return DeliveryResult(sent=True)
