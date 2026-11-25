"""Vacuum each room before mopping it: the robot's per-room table, written through the core Roborock connection.

The robot keeps this order only per room and uses the room table only in its custom mode, so while it is on
every room gets the global settings and the robot is put in custom mode; off restores the global settings.
Writing the table during a run pauses the robot, so changes wait until it is docked or idle.
"""

import asyncio
import logging
from datetime import timedelta

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .logic import CUSTOM, then_mop_base, then_mop_on, then_mop_settings, then_mop_stale, then_mop_table

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = timedelta(minutes=5)
QUIET = ("docked", "idle")
GET, SET = "get_customize_clean_mode", "set_customize_clean_mode"
MODES = ("set_custom_mode", "set_water_box_custom_mode", "set_mop_mode")  # fan, water, route


class Busy(HomeAssistantError):
    """The robot is in a run; try again once it is docked."""


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([CleanThenMop(entry)], update_before_add=True)


class CleanThenMop(SwitchEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "clean_then_mop"
    _attr_should_poll = True

    def __init__(self, entry):
        self._robot = entry.runtime_data
        self._attr_unique_id = f"{entry.unique_id}_clean_then_mop"
        self.device_entry = self._robot.device()
        self._lock = asyncio.Lock()
        self._table: list[dict] = []
        self._rooms: list[int] = []
        self._attr_available = False
        self._failed = None  # settings a sync already failed for; no retry loop on every status update
        self._subscribed = None  # the coordinator we listen to; core adds and reloads it on its own schedule
        self._syncing = False
        self._unsub = None

    def _core(self):
        """The core Roborock coordinator of this robot (its connection, its status)."""
        ids = self.device_entry.identifiers if self.device_entry else set()
        for e in self.hass.config_entries.async_loaded_entries("roborock"):
            for c in getattr(e.runtime_data, "v1", []):
                if ("roborock", c.duid) in ids:
                    return c
        raise HomeAssistantError("the core Roborock integration has no connection to this robot")

    @staticmethod
    def _settings(core):
        s = core.properties_api.status
        return (s.fan_power, s.water_box_mode, s.mop_mode)

    async def _read(self, core):
        api = core.properties_api
        await api.rooms.refresh()
        self._rooms = sorted(r.segment_id for r in api.rooms.rooms or [])
        table = await api.command.send(GET)
        if not isinstance(table, list) or not all(isinstance(e, dict) for e in table):
            raise HomeAssistantError(f"unexpected {GET} answer")
        self._table = table
        self._attr_is_on = then_mop_on(self._rooms, table)
        self._attr_extra_state_attributes = self._names(api.status, then_mop_settings(self._rooms, table))
        self._attr_available = True

    @staticmethod
    def _names(status, codes) -> dict:
        """The rooms' fan, water and route by the names the core entities use (the robot itself reports custom)."""
        if not codes:
            return {}
        try:
            maps = (status.fan_speed_mapping, status.water_mode_mapping, {r.code: r.value for r in status.mop_route_options})
        except Exception:  # noqa: BLE001 - names are a convenience for dashboards
            return {}
        return {k: m.get(c) for k, m, c in zip(("fan_speed", "mop_intensity", "mop_mode"), maps, codes)}

    def _subscribe(self, core) -> None:
        if core is not self._subscribed:
            if self._unsub:
                self._unsub()
            self._unsub = core.async_add_listener(self._on_status)
            self._subscribed = core

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()

    async def async_update(self) -> None:
        await self._poll()
        self._on_status()  # catches a dock the coordinator reported before the vacuum state changed

    async def _poll(self) -> None:
        async with self._lock:
            try:
                core = self._core()
                self._subscribe(core)
                await self._read(core)
                self._failed = None  # a failed sync gets another try each poll
            except Exception as err:  # noqa: BLE001
                self._attr_available = False
                _LOGGER.debug("clean-then-mop read failed: %s", err)

    def _quiet(self, core) -> bool:
        """Docked or idle and no run in progress (the robot docks mid-run to wash the mop or recharge)."""
        state = self.hass.states.get(self._robot.vacuum)
        if state is None or state.state not in QUIET:
            return False
        return not core.properties_api.status.in_cleaning

    async def _apply(self, on: bool, only_if_stale: bool = False) -> None:
        async with self._lock:
            core = self._core()
            self._subscribe(core)
            await self._read(core)
            settings = self._settings(core)
            if only_if_stale and not then_mop_stale(self._rooms, self._table, settings):
                return
            if not self._quiet(core):
                raise Busy("the robot is cleaning; change this when it is docked")
            before = then_mop_settings(self._rooms, self._table)
            try:
                base = then_mop_base(settings, before) if on or before else None
                table = then_mop_table(self._rooms, self._table, base or settings, on)
            except ValueError as err:
                raise HomeAssistantError(str(err)) from err
            if on and not then_mop_on(self._rooms, self._table) and self._table:
                _LOGGER.info("clean-then-mop replaces the room table %s", self._table)
            await core.properties_api.command.send(SET, table)
            if on or (base and any(v == c for v, c in zip(settings, CUSTOM))):
                for cmd, value in zip(MODES, CUSTOM if on else base):
                    await core.properties_api.command.send(cmd, [value])
            await core.properties_api.status.refresh()
            await self._read(core)
            if self._attr_is_on != on or (on and tuple(self._settings(core)) != CUSTOM):
                raise HomeAssistantError("the robot did not keep the new room order")
        if self.entity_id:  # a sync can run from the first update, before the entity is added
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self._apply(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._apply(False)

    @callback
    def _on_status(self) -> None:
        """Global settings changed while on: rewrite the rooms once the robot is docked."""
        try:
            core = self._core()
            settings = self._settings(core)
            due = then_mop_stale(self._rooms, self._table, settings) and self._quiet(core)
        except Exception:  # noqa: BLE001 - a coordinator callback must not raise
            return
        if due and settings != self._failed and not self._syncing:
            self._syncing = True
            self.hass.async_create_task(self._sync(settings))

    async def _sync(self, settings) -> None:
        try:
            await self._apply(True, only_if_stale=True)
            self._failed = None
        except Busy:
            pass  # the next status update after the run tries again
        except Exception as err:  # noqa: BLE001
            self._failed = settings
            _LOGGER.warning("clean-then-mop could not follow the new settings: %s", err)
        finally:
            self._syncing = False
