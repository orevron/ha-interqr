"""Lock platform for the InterQR integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import InterQRApiClient
from .const import DOMAIN
from .coordinator import InterQRDataCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up InterQR lock entities from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: InterQRDataCoordinator = data["coordinator"]
    api: InterQRApiClient = data["api"]

    locks_data = coordinator.data.get("locks", [])
    entities: list[InterQRLock] = []

    for lock_data in locks_data:
        lock_uuid = lock_data.get("lock_uuid")
        if not lock_uuid:
            continue
        entities.append(InterQRLock(coordinator, api, lock_data))

    _LOGGER.info("Setting up %d InterQR lock entit(ies)", len(entities))
    async_add_entities(entities)


class InterQRLock(CoordinatorEntity[InterQRDataCoordinator], LockEntity):
    """Representation of an InterQR lock."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: InterQRDataCoordinator,
        api: InterQRApiClient,
        lock_data: dict[str, Any],
    ) -> None:
        """Initialize the lock entity."""
        super().__init__(coordinator)
        self._api = api
        self._lock_uuid: str = lock_data["lock_uuid"]
        self._lock_data = lock_data

        # ── Entity identity ──────────────────────────────────────────
        self._attr_unique_id = f"interqr_{self._lock_uuid}"

        # ── Name: prefer custom description, fall back to lock_description
        custom_name = lock_data.get("description")
        lock_desc = lock_data.get("lock_description", "Lock")
        self._attr_name = custom_name if custom_name else lock_desc

        # ── Lock is always locked (API is unlock-only) ───────────────
        self._attr_is_locked = True
        self._attr_is_locking = False
        self._attr_is_unlocking = False

        # ── Supported features ───────────────────────────────────────
        allow_long = lock_data.get("allow_long_unlock")
        self._allow_long_unlock = allow_long in ("1", "true", True)
        if self._allow_long_unlock:
            self._attr_supported_features = LockEntityFeature.OPEN
        else:
            self._attr_supported_features = LockEntityFeature(0)

        # ── Device info ──────────────────────────────────────────────
        building_desc = lock_data.get("building_description", "InterQR Building")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, self._lock_uuid)},
            name=self._attr_name,
            manufacturer="InterQR",
            model=lock_desc,
            suggested_area=building_desc,
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional lock attributes.

        Only user-facing descriptive attributes are exposed;
        internal UUIDs are excluded to reduce information leakage.
        """
        return {
            "building_description": self._lock_data.get("building_description"),
            "is_palgate_lock": self._lock_data.get("is_palgate_lock"),
            "allow_long_unlock": self._allow_long_unlock,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Update lock data when coordinator refreshes."""
        locks = self.coordinator.data.get("locks", [])
        for lock in locks:
            if lock.get("lock_uuid") == self._lock_uuid:
                self._lock_data = lock
                # Update name if custom name changed
                custom_name = lock.get("description")
                lock_desc = lock.get("lock_description", "Lock")
                self._attr_name = custom_name if custom_name else lock_desc
                break
        self.async_write_ha_state()

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock (normal unlock)."""
        _LOGGER.info("Unlocking InterQR lock: %s", self._attr_name)
        self._attr_is_unlocking = True
        self.async_write_ha_state()

        try:
            await self._api.unlock(self._lock_uuid)
            _LOGGER.info("Successfully unlocked: %s", self._attr_name)
        finally:
            self._attr_is_unlocking = False
            # Lock returns to locked state (unlock-only system)
            self._attr_is_locked = True
            self.async_write_ha_state()

    async def async_open(self, **kwargs: Any) -> None:
        """Long unlock the lock (extended duration)."""
        if not self._allow_long_unlock:
            _LOGGER.warning(
                "Long unlock not supported for %s", self._attr_name
            )
            return

        _LOGGER.info("Long-unlocking InterQR lock: %s", self._attr_name)
        self._attr_is_unlocking = True
        self.async_write_ha_state()

        try:
            await self._api.unlock_long(self._lock_uuid)
            _LOGGER.info("Successfully long-unlocked: %s", self._attr_name)
        finally:
            self._attr_is_unlocking = False
            self._attr_is_locked = True
            self.async_write_ha_state()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock is not supported — the InterQR API is unlock-only."""
        _LOGGER.debug(
            "Lock action is not supported by InterQR (unlock-only system)"
        )
