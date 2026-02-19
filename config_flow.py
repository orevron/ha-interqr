"""Config flow for the InterQR integration."""

from __future__ import annotations

import ipaddress
import logging
import re
import uuid as uuid_mod
from typing import Any
from urllib.parse import urlparse

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InterQRApiClient, InterQRAuthError, InterQRConnectionError
from .const import (
    CONF_BASE_URL,
    CONF_DEVICE_UUID,
    CONF_PHONE,
    CONF_TOKEN,
    CONF_USER_UUID,
    DEFAULT_BASE_URL,
    DEV_BASE_URL,
    DOMAIN,
    MAX_2FA_ATTEMPTS,
    PHONE_PATTERN,
    SERVER_CUSTOM,
    SERVER_DEVELOPMENT,
    SERVER_PRODUCTION,
    SERVER_URLS,
    VERIFICATION_CODE_PATTERN,
)

_LOGGER = logging.getLogger(__name__)

SERVER_OPTIONS = {
    SERVER_PRODUCTION: "Production (interqr.com)",
    SERVER_DEVELOPMENT: "Development (dev.interqr.com)",
    SERVER_CUSTOM: "Custom URL",
}

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("server", default=SERVER_PRODUCTION): vol.In(SERVER_OPTIONS),
        vol.Required("phone"): str,
        vol.Optional("custom_url", default=""): str,
    }
)

STEP_VERIFY_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("code"): str,
    }
)

# ── Private IP / reserved ranges for SSRF prevention ─────────────
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _mask_phone(phone: str) -> str:
    """Mask a phone number, showing only the last 4 digits."""
    if len(phone) <= 4:
        return "****"
    return "*" * (len(phone) - 4) + phone[-4:]


def _validate_phone(phone: str) -> bool:
    """Validate phone number matches E.164 format."""
    return bool(re.match(PHONE_PATTERN, phone))


def _validate_code(code: str) -> bool:
    """Validate verification code is 4-8 digits."""
    return bool(re.match(VERIFICATION_CODE_PATTERN, code))


def _validate_custom_url(url: str) -> str | None:
    """Validate a custom URL for security.

    Returns an error key if invalid, None if valid.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return "invalid_url"

    # Must use HTTPS
    if parsed.scheme != "https":
        return "https_required"

    # Must have a hostname
    if not parsed.hostname:
        return "invalid_url"

    # Block private / reserved IP ranges (SSRF prevention)
    try:
        addr = ipaddress.ip_address(parsed.hostname)
        for network in _PRIVATE_NETWORKS:
            if addr in network:
                return "private_url_blocked"
    except ValueError:
        # hostname is a domain name, not an IP — OK
        pass

    return None


class InterQRConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the InterQR config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._base_url: str = DEFAULT_BASE_URL
        self._phone: str = ""
        self._device_uuid: str = ""
        self._second_auth_token: str | None = None
        self._api: InterQRApiClient | None = None
        self._2fa_attempts: int = 0

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: Collect server and phone number, then start 2FA."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Determine base URL
            server = user_input["server"]
            if server == SERVER_CUSTOM:
                custom_url = user_input.get("custom_url", "").strip()
                if not custom_url:
                    errors["custom_url"] = "cannot_connect"
                else:
                    url_error = _validate_custom_url(custom_url)
                    if url_error:
                        errors["custom_url"] = url_error
                    else:
                        self._base_url = custom_url.rstrip("/")
            else:
                self._base_url = SERVER_URLS[server]

            self._phone = user_input["phone"].strip()

            # Validate phone number format
            if not errors and not _validate_phone(self._phone):
                errors["phone"] = "invalid_phone"

            if not errors:
                # Generate device UUID
                self._device_uuid = str(uuid_mod.uuid4())

                # Create API client
                session = async_get_clientsession(self.hass)
                self._api = InterQRApiClient(
                    session=session,
                    base_url=self._base_url,
                )

                try:
                    # Step 1a: Init device
                    await self._api.init_device(self._device_uuid)
                except InterQRConnectionError:
                    errors["base"] = "cannot_connect"
                except InterQRAuthError:
                    errors["base"] = "init_failed"

                if not errors:
                    try:
                        # Step 1b: Start 2FA — sends SMS
                        twofa_result = await self._api.start_2fa(
                            self._phone, self._device_uuid
                        )
                        # Capture second_auth_token if present
                        data = twofa_result.get("data") or {}
                        self._second_auth_token = data.get("second_auth_token")
                    except InterQRConnectionError:
                        errors["base"] = "cannot_connect"
                    except InterQRAuthError:
                        errors["base"] = "twofa_failed"

                if not errors:
                    # Reset 2FA attempt counter for a new flow
                    self._2fa_attempts = 0
                    # Proceed to verification step
                    return await self.async_step_verify()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: Verify the SMS code and complete authentication."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Enforce 2FA attempt limit
            self._2fa_attempts += 1
            if self._2fa_attempts > MAX_2FA_ATTEMPTS:
                return self.async_abort(reason="too_many_attempts")

            code = user_input["code"].strip()

            # Validate verification code format
            if not _validate_code(code):
                errors["code"] = "invalid_code"
            else:
                try:
                    verify_result = await self._api.verify_2fa(
                        phone_number=self._phone,
                        code=code,
                        device_uuid=self._device_uuid,
                        second_auth_token=self._second_auth_token,
                    )

                    data = verify_result.get("data") or {}
                    token = data.get("token")
                    user_uuid = data.get("uuid", "")

                    if not token:
                        errors["base"] = "invalid_auth"
                    else:
                        # Check if this account is already configured
                        await self.async_set_unique_id(user_uuid)
                        self._abort_if_unique_id_configured()

                        # Create the config entry
                        return self.async_create_entry(
                            title=f"InterQR ({_mask_phone(self._phone)})",
                            data={
                                CONF_BASE_URL: self._base_url,
                                CONF_TOKEN: token,
                                CONF_DEVICE_UUID: self._device_uuid,
                                CONF_USER_UUID: user_uuid,
                                CONF_PHONE: self._phone,
                            },
                        )

                except InterQRAuthError:
                    errors["base"] = "invalid_auth"
                except InterQRConnectionError:
                    errors["base"] = "cannot_connect"

        return self.async_show_form(
            step_id="verify",
            data_schema=STEP_VERIFY_DATA_SCHEMA,
            errors=errors,
            description_placeholders={"phone": _mask_phone(self._phone)},
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Handle re-authentication when token expires."""
        self._base_url = entry_data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
        self._device_uuid = entry_data.get(CONF_DEVICE_UUID, "")
        self._phone = entry_data.get(CONF_PHONE, "")

        # Try quick re-login with existing device UUID first
        session = async_get_clientsession(self.hass)
        self._api = InterQRApiClient(
            session=session,
            base_url=self._base_url,
        )

        try:
            login_result = await self._api.login(self._device_uuid)
            data = login_result.get("data") or {}
            token = data.get("token")
            if token:
                # Token refreshed — update the config entry
                entry = self.hass.config_entries.async_get_entry(
                    self.context["entry_id"]
                )
                if entry:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={
                            **entry.data,
                            CONF_TOKEN: token,
                        },
                    )
                return self.async_abort(reason="reauth_successful")
        except (InterQRAuthError, InterQRConnectionError):
            _LOGGER.debug("Quick re-login failed, falling back to full 2FA")

        # Fall back to full 2FA flow
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-auth confirmation step — starts 2FA again."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._phone = user_input.get("phone", self._phone).strip()

            # Validate phone number format
            if not _validate_phone(self._phone):
                errors["phone"] = "invalid_phone"
            else:
                try:
                    self._device_uuid = str(uuid_mod.uuid4())
                    await self._api.init_device(self._device_uuid)
                    await self._api.start_2fa(self._phone, self._device_uuid)
                    self._2fa_attempts = 0
                    return await self.async_step_verify()
                except InterQRConnectionError:
                    errors["base"] = "cannot_connect"
                except InterQRAuthError:
                    errors["base"] = "twofa_failed"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required("phone", default=self._phone): str,
                }
            ),
            errors=errors,
        )
