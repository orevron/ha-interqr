"""API client for the InterQR lock system."""

from __future__ import annotations

import json
import logging
import re
import uuid as uuid_mod
from typing import Any

import aiohttp

from .const import (
    API_TIMEOUT_SECONDS,
    APP_VERSION,
    DEVICE_MANUFACTURER,
    DEVICE_MODEL,
    DEVICE_PLATFORM,
    ENDPOINT_INIT,
    ENDPOINT_LOGIN,
    ENDPOINT_LOGOUT,
    ENDPOINT_TWOFA_START,
    ENDPOINT_TWOFA_VERIFY,
    ENDPOINT_UNLOCK,
    ENDPOINT_UNLOCK_LONG,
    ENDPOINT_USER_DETAILS,
    MAX_RESPONSE_BYTES,
)

_LOGGER = logging.getLogger(__name__)

# Compiled pattern for lock/device identifier validation.
# The InterQR API uses identifiers that are NOT necessarily UUID v4
# (e.g. "abc-123"), so we use a permissive pattern that still
# prevents path-traversal and injection attacks.
_SAFE_ID_PATTERN = re.compile(r"^[0-9a-zA-Z\-]+$")

_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=API_TIMEOUT_SECONDS)


class InterQRAuthError(Exception):
    """Raised when authentication fails."""


class InterQRConnectionError(Exception):
    """Raised when the API is unreachable."""


def _validate_uuid(value: str, label: str = "UUID") -> str:
    """Validate that a string is a safe identifier to prevent path injection.

    Accepts any non-empty string composed of alphanumeric characters and hyphens.
    Raises ValueError if the format is invalid.
    """
    if not value or not _SAFE_ID_PATTERN.match(value):
        raise ValueError(f"Invalid {label} format: expected alphanumeric identifier")
    return value


class InterQRApiClient:
    """Async client for the InterQR REST API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str | None = None,
        device_uuid: str | None = None,
    ) -> None:
        """Initialize the API client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._device_uuid = device_uuid

    @property
    def token(self) -> str | None:
        """Return the current auth token."""
        return self._token

    @token.setter
    def token(self, value: str | None) -> None:
        """Set the auth token."""
        self._token = value

    @property
    def device_uuid(self) -> str | None:
        """Return the device UUID."""
        return self._device_uuid

    # ── Private helpers ──────────────────────────────────────────────

    def _url(self, endpoint: str) -> str:
        """Build a full URL from an endpoint path."""
        return f"{self._base_url}{endpoint}"

    def _auth_headers(self) -> dict[str, str]:
        """Return headers with Bearer auth token."""
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        json_data: dict | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        """Make an API request and return parsed JSON.

        Security measures:
        - Enforces a request timeout to prevent indefinite hangs.
        - Limits response body size to prevent memory exhaustion.
        - Validates response Content-Type before JSON parsing.
        """
        url = self._url(endpoint)
        headers = self._auth_headers() if authenticated else {"Content-Type": "application/json"}

        try:
            async with self._session.request(
                method, url, json=json_data, headers=headers, timeout=_REQUEST_TIMEOUT
            ) as response:
                # ── Validate content-type ──
                content_type = response.headers.get("Content-Type", "")
                if "application/json" not in content_type:
                    _LOGGER.error(
                        "Unexpected Content-Type from %s: %s",
                        endpoint,
                        content_type,
                    )
                    raise InterQRConnectionError(
                        f"Unexpected response type from server: {content_type}"
                    )

                # ── Enforce response size limit ──
                raw_body = await response.content.read(MAX_RESPONSE_BYTES + 1)
                if len(raw_body) > MAX_RESPONSE_BYTES:
                    raise InterQRConnectionError(
                        "Response body exceeds maximum allowed size"
                    )

                try:
                    data: dict[str, Any] = json.loads(raw_body)
                except (json.JSONDecodeError, ValueError) as err:
                    raise InterQRConnectionError(
                        "Invalid JSON in server response"
                    ) from err

                if response.status == 401:
                    raise InterQRAuthError("Authentication failed")

                if response.status >= 400:
                    # Log only status code — never log response body contents
                    _LOGGER.error(
                        "InterQR API error: HTTP %s on %s", response.status, endpoint
                    )
                    raise InterQRConnectionError(
                        f"API error (HTTP {response.status})"
                    )

                return data

        except aiohttp.ClientError as err:
            raise InterQRConnectionError(
                f"Error communicating with InterQR API: {err}"
            ) from err

    # ── Auth Flow ────────────────────────────────────────────────────

    async def init_device(self, device_uuid: str | None = None) -> dict[str, Any]:
        """Register a new device with the InterQR server.

        POST /api/init
        """
        if device_uuid is None:
            device_uuid = str(uuid_mod.uuid4())
        self._device_uuid = device_uuid

        payload = {
            "device_uuid": device_uuid,
            "manufacturer": DEVICE_MANUFACTURER,
            "model": DEVICE_MODEL,
            "platform": DEVICE_PLATFORM,
            "os_version": "1.0",
            "app_version": APP_VERSION,
        }

        result = await self._request("POST", ENDPOINT_INIT, json_data=payload)
        _LOGGER.debug("Device init completed successfully")

        # Capture device UUID from server if returned
        resp_uuid = (result.get("data") or {}).get("device_uuid")
        if resp_uuid:
            self._device_uuid = resp_uuid

        return result

    async def start_2fa(self, phone_number: str, device_uuid: str) -> dict[str, Any]:
        """Start 2FA by sending an SMS verification code.

        POST /api/twofa/start
        """
        payload = {
            "number": phone_number,
            "device_uuid": device_uuid,
        }
        result = await self._request("POST", ENDPOINT_TWOFA_START, json_data=payload)
        _LOGGER.debug("2FA SMS sent successfully")
        return result

    async def verify_2fa(
        self,
        phone_number: str,
        code: str,
        device_uuid: str,
        second_auth_token: str | None = None,
    ) -> dict[str, Any]:
        """Verify the 2FA code and obtain auth token.

        POST /api/twofa/verify
        Returns: {data: {token, uuid, ...}}
        """
        payload: dict[str, Any] = {
            "number": phone_number,
            "code": code,
            "device_uuid": device_uuid,
        }
        if second_auth_token:
            payload["second_auth_token"] = second_auth_token

        result = await self._request("POST", ENDPOINT_TWOFA_VERIFY, json_data=payload)

        # Extract token from response
        data = result.get("data") or {}
        token = data.get("token")
        if token:
            self._token = token
            _LOGGER.debug("2FA verification succeeded; token acquired")
        else:
            raise InterQRAuthError("No token in verify response")

        return result

    async def login(self, device_uuid: str | None = None) -> dict[str, Any]:
        """Re-authenticate using an existing device UUID.

        POST /api/login
        """
        uuid_to_use = device_uuid or self._device_uuid
        if not uuid_to_use:
            raise InterQRAuthError("No device_uuid available for login")

        payload = {"device_uuid": uuid_to_use}
        result = await self._request("POST", ENDPOINT_LOGIN, json_data=payload)

        # Extract new token
        data = result.get("data") or {}
        token = data.get("token")
        if token:
            self._token = token
            _LOGGER.debug("Login succeeded; token refreshed")

        return result

    async def logout(self) -> None:
        """Invalidate the current session token on the server.

        POST /api/logout
        """
        if not self._token:
            return

        try:
            await self._request("POST", ENDPOINT_LOGOUT, authenticated=True)
            _LOGGER.debug("Logout succeeded; token invalidated")
        except (InterQRAuthError, InterQRConnectionError):
            # Best-effort — don't block unload if logout fails
            _LOGGER.debug("Logout request failed (best-effort, ignoring)")
        finally:
            self._token = None

    # ── User Data ────────────────────────────────────────────────────

    async def get_user_details(self) -> dict[str, Any]:
        """Fetch all user data including locks.

        GET /api/resource/user/details
        Returns: {data: {locks: [...], apartments: [...], name, ...}}
        """
        return await self._request(
            "GET", ENDPOINT_USER_DETAILS, authenticated=True
        )

    # ── Lock Control ─────────────────────────────────────────────────

    async def unlock(self, lock_uuid: str) -> dict[str, Any]:
        """Send unlock command to a lock.

        POST /api/locks/{uuid}/unlock
        """
        _validate_uuid(lock_uuid, "lock_uuid")
        endpoint = ENDPOINT_UNLOCK.format(uuid=lock_uuid)
        result = await self._request("POST", endpoint, authenticated=True)
        _LOGGER.info("Unlock command sent successfully")
        return result

    async def unlock_long(self, lock_uuid: str) -> dict[str, Any]:
        """Send long-duration unlock command to a lock.

        POST /api/locks/{uuid}/unlock-long
        """
        _validate_uuid(lock_uuid, "lock_uuid")
        endpoint = ENDPOINT_UNLOCK_LONG.format(uuid=lock_uuid)
        result = await self._request("POST", endpoint, authenticated=True)
        _LOGGER.info("Long-unlock command sent successfully")
        return result
