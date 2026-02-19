"""Data coordinator for the InterQR integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import InterQRApiClient, InterQRAuthError, InterQRConnectionError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)


class InterQRDataCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to poll InterQR user/lock data."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        api: InterQRApiClient,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
            config_entry=config_entry,
        )
        self.api = api

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch user details including locks from the InterQR API.

        Returns only lock-related data to minimize PII exposure.
        """
        try:
            response = await self.api.get_user_details()
        except InterQRAuthError as err:
            # Token expired — trigger re-auth flow
            raise ConfigEntryAuthFailed(
                "InterQR authentication failed, re-authentication required"
            ) from err
        except InterQRConnectionError as err:
            raise UpdateFailed(
                f"Error fetching InterQR data: {err}"
            ) from err

        data = response.get("data")
        if data is None:
            raise UpdateFailed("InterQR API returned no user data")

        locks = data.get("locks", [])
        _LOGGER.debug(
            "InterQR data update: %d lock(s) found",
            len(locks),
        )

        # Return only the data needed by entities — no PII
        return {"locks": locks}
