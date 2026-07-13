"""FastAPI routes for personal accounts and account-owned saved zones."""

from __future__ import annotations

import re
import smtplib
import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, field_validator

try:
    from .account_mailer import AccountMailer, DeliveryResult
    from .account_store import (
        AccountStore,
        DuplicateUserError,
        DuplicateZoneError,
        EMAIL_VERIFICATION_TTL_SECONDS,
        PASSWORD_RESET_TTL_SECONDS,
        SESSION_TTL_SECONDS,
    )
except ImportError:  # pragma: no cover - supports `python backend/main.py`
    from account_mailer import AccountMailer, DeliveryResult
    from account_store import (
        AccountStore,
        DuplicateUserError,
        DuplicateZoneError,
        EMAIL_VERIFICATION_TTL_SECONDS,
        PASSWORD_RESET_TTL_SECONDS,
        SESSION_TTL_SECONDS,
    )


MUTATION_HEADER = "X-Requested-With"
MUTATION_HEADER_VALUE = "GeoAI-TKO"
COMMON_PASSWORDS = {
    "123456789012345",
    "passwordpassword",
    "qwertyqwertyqwerty",
    "adminadminadmin",
    "letmeinletmeinletmein",
}


def _clean_email(value: str) -> str:
    email = value.strip()
    if len(email) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Введите корректный адрес электронной почты")
    return email


def _validate_password(value: str) -> str:
    if len(value) < 15:
        raise ValueError("Пароль должен содержать минимум 15 символов")
    if len(value) > 128:
        raise ValueError("Пароль не должен превышать 128 символов")
    if value.casefold() in COMMON_PASSWORDS:
        raise ValueError("Выберите менее распространённый пароль")
    return value


class RegisterRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)
    email: str
    password: str
    locale: Literal["ru", "kk", "en"] = "ru"

    @field_validator("display_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if len(cleaned) < 2:
            raise ValueError("Укажите имя")
        return cleaned

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _clean_email(value)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password(value)


class LoginRequest(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _clean_email(value)


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=2, max_length=80)

    @field_validator("display_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return " ".join(value.split())


class PreferencesRequest(BaseModel):
    locale: Literal["ru", "kk", "en"] = "ru"
    timezone: Literal["Asia/Qyzylorda", "Asia/Almaty", "UTC"] = "Asia/Qyzylorda"
    default_layer: Literal["satellite", "ndvi", "ndwi", "ndre", "ndmi", "bsi", "savi", "nbr"] = "ndvi"
    default_period: str = Field(pattern=r"^\d{4}_[a-z][a-z0-9_]*$", max_length=40)
    default_opacity: float = Field(ge=0, le=1)
    left_panel_open: bool = True
    right_panel_open: bool = False


class ZonePayload(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=80)
    geometry: dict
    createdAt: str | None = Field(default=None, max_length=40)
    updatedAt: str | None = Field(default=None, max_length=40)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Укажите название зоны")
        return cleaned


class ZoneUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    geometry: dict | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value is not None else None


class ZoneImportRequest(BaseModel):
    zones: list[ZonePayload] = Field(max_length=100)


class DeleteAccountRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: str
    locale: Literal["ru", "kk", "en"] = "ru"

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _clean_email(value)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class PasswordResetRequest(TokenRequest):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        return _validate_password(value)


class AttemptLimiter:
    """Small in-process login throttle suitable for the current deployment."""

    def __init__(self, limit: int = 5, window_seconds: int = 15 * 60):
        self.limit = limit
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._attempts[key]
            while attempts and attempts[0] <= now - self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                raise HTTPException(
                    status_code=429,
                    detail="Слишком много попыток входа. Повторите позже.",
                    headers={"Retry-After": str(self.window_seconds)},
                )

    def failure(self, key: str) -> None:
        with self._lock:
            self._attempts[key].append(time.monotonic())

    def success(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)


def create_account_router(
    store: AccountStore,
    *,
    allowed_origins: list[str],
    validate_geometry: Callable[[dict], object],
    secure_cookie: bool = False,
    mailer: AccountMailer | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/account", tags=["account"])
    login_limiter = AttemptLimiter()
    cookie_name = "__Host-geoai_session" if secure_cookie else "geoai_session"

    def set_session_cookie(response: Response, token: str) -> None:
        response.set_cookie(
            cookie_name,
            token,
            max_age=SESSION_TTL_SECONDS,
            path="/",
            secure=secure_cookie,
            httponly=True,
            samesite="strict",
        )

    def clear_session_cookie(response: Response) -> None:
        response.delete_cookie(
            cookie_name,
            path="/",
            secure=secure_cookie,
            httponly=True,
            samesite="strict",
        )

    def require_mutation_request(request: Request) -> None:
        if request.headers.get(MUTATION_HEADER) != MUTATION_HEADER_VALUE:
            raise HTTPException(status_code=403, detail="Запрос отклонён проверкой безопасности")
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in {item.rstrip("/") for item in allowed_origins}:
            raise HTTPException(status_code=403, detail="Источник запроса не разрешён")

    def current_user(request: Request) -> dict:
        user = store.user_for_session(request.cookies.get(cookie_name))
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Требуется вход в аккаунт")
        return user

    def account_payload(user: dict) -> dict:
        return {"user": user, "preferences": store.get_preferences(user["id"])}

    def deliver_verification(user: dict, locale: str) -> dict:
        token = store.create_account_token(
            user["id"], "verify_email", EMAIL_VERIFICATION_TTL_SECONDS
        )
        if mailer is None:
            return DeliveryResult(sent=False).public_payload()
        try:
            return mailer.send_verification(user, token, locale).public_payload()
        except (OSError, smtplib.SMTPException):
            return DeliveryResult(sent=False).public_payload()

    def deliver_password_reset(user: dict, locale: str) -> dict:
        token = store.create_account_token(
            user["id"], "reset_password", PASSWORD_RESET_TTL_SECONDS
        )
        if mailer is None:
            return DeliveryResult(sent=False).public_payload()
        try:
            return mailer.send_password_reset(user, token, locale).public_payload()
        except (OSError, smtplib.SMTPException):
            return DeliveryResult(sent=False).public_payload()

    def validate_zone_geometry(geometry: dict) -> None:
        validate_geometry(geometry)

    @router.post("/register", status_code=status.HTTP_201_CREATED)
    def register(
        payload: RegisterRequest,
        request: Request,
        response: Response,
        _: None = Depends(require_mutation_request),
    ):
        client_key = f"register:{request.client.host if request.client else 'unknown'}"
        login_limiter.check(client_key)
        try:
            user = store.create_user(payload.email, payload.display_name, payload.password)
        except DuplicateUserError:
            login_limiter.failure(client_key)
            raise HTTPException(status_code=409, detail="Аккаунт с таким email уже существует")
        login_limiter.success(client_key)
        set_session_cookie(response, store.create_session(user["id"]))
        result = account_payload(user)
        result["verification_delivery"] = deliver_verification(user, payload.locale)
        return result

    @router.post("/login")
    def login(
        payload: LoginRequest,
        request: Request,
        response: Response,
        _: None = Depends(require_mutation_request),
    ):
        client = request.client.host if request.client else "unknown"
        key = f"login:{client}:{payload.email.casefold()}"
        login_limiter.check(key)
        user = store.authenticate(payload.email, payload.password)
        if not user:
            login_limiter.failure(key)
            raise HTTPException(status_code=401, detail="Неверный email или пароль")
        login_limiter.success(key)
        set_session_cookie(response, store.create_session(user["id"]))
        return account_payload(user)

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(
        request: Request,
        response: Response,
        _: None = Depends(require_mutation_request),
    ):
        store.revoke_session(request.cookies.get(cookie_name))
        clear_session_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.post("/verification/resend")
    def resend_verification(
        request: Request,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        if user["email_verified"]:
            return {"already_verified": True, "delivery": {"sent": True}}
        key = f"verify:{request.client.host if request.client else 'unknown'}:{user['id']}"
        login_limiter.check(key)
        login_limiter.failure(key)
        locale = store.get_preferences(user["id"]).get("locale", "ru")
        return {"already_verified": False, "delivery": deliver_verification(user, locale)}

    @router.post("/verification/confirm")
    def confirm_verification(
        payload: TokenRequest,
        response: Response,
        _: None = Depends(require_mutation_request),
    ):
        user = store.verify_email_with_token(payload.token)
        if not user:
            raise HTTPException(status_code=400, detail="Ссылка подтверждения недействительна или устарела")
        set_session_cookie(response, store.create_session(user["id"]))
        return account_payload(user)

    @router.post("/password/forgot")
    def forgot_password(
        payload: ForgotPasswordRequest,
        request: Request,
        _: None = Depends(require_mutation_request),
    ):
        key = f"forgot:{request.client.host if request.client else 'unknown'}:{payload.email.casefold()}"
        login_limiter.check(key)
        login_limiter.failure(key)
        user = store.get_user_by_email(payload.email)
        delivery = None
        if user:
            delivery = deliver_password_reset(user, payload.locale)
        result = {"accepted": True}
        if delivery and delivery.get("preview_url"):
            result["delivery"] = delivery
        return result

    @router.post("/password/reset")
    def reset_password(
        payload: PasswordResetRequest,
        response: Response,
        _: None = Depends(require_mutation_request),
    ):
        user = store.reset_password_with_token(payload.token, payload.password)
        if not user:
            raise HTTPException(status_code=400, detail="Ссылка сброса недействительна или устарела")
        set_session_cookie(response, store.create_session(user["id"]))
        return account_payload(user)

    @router.get("/me")
    def me(user: dict = Depends(current_user)):
        return account_payload(user)

    @router.patch("/profile")
    def update_profile(
        payload: ProfileUpdateRequest,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        updated = store.update_profile(user["id"], payload.display_name)
        return account_payload(updated)

    @router.put("/preferences")
    def update_preferences(
        payload: PreferencesRequest,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        preferences = store.update_preferences(user["id"], payload.model_dump())
        return {"user": store.get_user(user["id"]), "preferences": preferences}

    @router.get("/export")
    def export_account(user: dict = Depends(current_user)):
        return store.export_account(user["id"])

    @router.delete("", status_code=status.HTTP_204_NO_CONTENT)
    def delete_account(
        payload: DeleteAccountRequest,
        response: Response,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        if not store.verify_user_password(user["id"], payload.password):
            raise HTTPException(status_code=401, detail="Неверный пароль")
        store.delete_account(user["id"])
        clear_session_cookie(response)
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @router.get("/zones")
    def list_zones(user: dict = Depends(current_user)):
        return {"zones": store.list_zones(user["id"])}

    @router.post("/zones", status_code=status.HTTP_201_CREATED)
    def create_zone(
        payload: ZonePayload,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        validate_zone_geometry(payload.geometry)
        zone = payload.model_dump(exclude_none=True)
        try:
            return store.create_zone(user["id"], zone)
        except DuplicateZoneError:
            raise HTTPException(status_code=409, detail="Зона с таким идентификатором уже существует")

    @router.patch("/zones/{zone_id}")
    def update_zone(
        zone_id: str,
        payload: ZoneUpdateRequest,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        if len(zone_id) > 120:
            raise HTTPException(status_code=404, detail="Зона не найдена")
        if payload.name is None and payload.geometry is None:
            raise HTTPException(status_code=400, detail="Нет изменений для сохранения")
        if payload.geometry is not None:
            validate_zone_geometry(payload.geometry)
        zone = store.update_zone(
            user["id"], zone_id, name=payload.name, geometry=payload.geometry
        )
        if not zone:
            raise HTTPException(status_code=404, detail="Зона не найдена")
        return zone

    @router.delete("/zones/{zone_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_zone(
        zone_id: str,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        if len(zone_id) > 120 or not store.delete_zone(user["id"], zone_id):
            raise HTTPException(status_code=404, detail="Зона не найдена")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/zones/import")
    def import_zones(
        payload: ZoneImportRequest,
        _: None = Depends(require_mutation_request),
        user: dict = Depends(current_user),
    ):
        zones = [zone.model_dump(exclude_none=True) for zone in payload.zones]
        for zone in zones:
            if not zone.get("id"):
                raise HTTPException(status_code=400, detail="Импортируемая зона не содержит идентификатор")
            validate_zone_geometry(zone["geometry"])
        imported_count = store.import_zones(user["id"], zones)
        return {"imported_count": imported_count, "zones": store.list_zones(user["id"])}

    return router
