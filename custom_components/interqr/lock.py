"""Lock platform for the InterQR integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import InterQRApiClient
from .const import DOMAIN, RELOCK_DELAY
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
        self._cancel_relock: CALLBACK_TYPE | None = None
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

    # ── Auto-relock helper ────────────────────────────────────────────

    @callback
    def _async_relock(self, _now: Any) -> None:
        """Return the lock to 'locked' state after the relock delay."""
        self._cancel_relock = None
        self._attr_is_locked = True
        self.async_write_ha_state()
        _LOGGER.debug("Auto-relocked: %s", self._attr_name)

    @callback
    def _schedule_auto_relock(self) -> None:
        """Schedule the lock to return to 'locked' after RELOCK_DELAY."""
        # Cancel any pending relock so we don't stack timers
        if self._cancel_relock is not None:
            self._cancel_relock()
        self._cancel_relock = async_call_later(
            self.hass, RELOCK_DELAY, self._async_relock
        )

    # ── Lock / Unlock actions ────────────────────────────────────────

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the lock (normal unlock)."""
        _LOGGER.info("Unlocking InterQR lock: %s", self._attr_name)
        self._attr_is_unlocking = True
        self.async_write_ha_state()

        try:
            await self._api.unlock(self._lock_uuid)
            _LOGGER.info("Successfully unlocked: %s", self._attr_name)
            # Transition to unlocked so HomeKit sees the state change
            self._attr_is_locked = False
        except Exception:
            # On failure, stay locked
            self._attr_is_locked = True
            raise
        finally:
            self._attr_is_unlocking = False
            self.async_write_ha_state()

        # Auto-relock after delay (unlock-only system)
        self._schedule_auto_relock()

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
            self._attr_is_locked = False
        except Exception:
            self._attr_is_locked = True
            raise
        finally:
            self._attr_is_unlocking = False
            self.async_write_ha_state()

        self._schedule_auto_relock()

    async def async_lock(self, **kwargs: Any) -> None:
        """Confirm locked state (InterQR is unlock-only).

        The API does not support a lock command, but we confirm
        the locked state so that HomeKit gets proper feedback.
        """
        _LOGGER.debug(
            "Lock command received (unlock-only system, confirming locked)"
        )
        # Cancel any pending auto-relock since we are locking immediately
        if self._cancel_relock is not None:
            self._cancel_relock()
            self._cancel_relock = None
        self._attr_is_locked = True
        self.async_write_ha_state()
