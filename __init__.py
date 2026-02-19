"""The InterQR integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import InterQRApiClient
from .const import CONF_BASE_URL, CONF_DEVICE_UUID, CONF_TOKEN, DOMAIN
from .coordinator import InterQRDataCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.LOCK]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up InterQR from a config entry."""
    session = async_get_clientsession(hass)

    api = InterQRApiClient(
        session=session,
        base_url=entry.data[CONF_BASE_URL],
        token=entry.data[CONF_TOKEN],
        device_uuid=entry.data[CONF_DEVICE_UUID],
    )

    coordinator = InterQRDataCoordinator(hass, api, entry)

    # Fetch initial data — will trigger reauth if token is expired
    await coordinator.async_config_entry_first_refresh()

    # Store API client and coordinator for platform setup
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    # Forward setup to the lock platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an InterQR config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)

        # Invalidate the token on the server (best-effort)
        if entry_data:
            api: InterQRApiClient = entry_data["api"]
            try:
                await api.logout()
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Failed to logout during unload (best-effort)")

        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN, None)

    return unload_ok
