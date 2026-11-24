"""Roborock camera and control on top of Home Assistant's Roborock integration.

Commands go through the core vacuum entity (vacuum.send_command), so there is no second cloud session.
"""

from __future__ import annotations

import asyncio
import math
import logging
from functools import partial

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import Context, HassJob, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized, UnknownUser
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later

from .const import CONF_VACUUM, DOMAIN
from .logic import MAX_ROTATION, MAX_VELOCITY, RemoteDrive

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BUTTON, Platform.CAMERA, Platform.NUMBER, Platform.SWITCH]
IDLE_STOP_S = 1.5  # the robot stops when no move arrives for this long after the last one ends
RETRY_STOP_S = 2


class Robot:
    """One configured robot: sends commands and keeps the remote-drive session safe."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.drive = RemoteDrive()
        self.closing = False
        self._lock = asyncio.Lock()
        self._stop_timer = None
        self._gen = 0
        self._epoch = 0  # bumped by every stop request; moves queued before it are dropped
        self._broken = False  # a send failed during a move: no more moves until the robot confirms a stop

    @property
    def vacuum(self) -> str:
        """Current entity id of the vacuum; follows renames through the registry id."""
        entity_id = er.async_resolve_entity_id(er.async_get(self.hass), self.entry.data[CONF_VACUUM])
        if entity_id is None:
            raise HomeAssistantError("the Roborock vacuum of this entry was removed")
        return entity_id

    async def send(self, command: str, params: list | dict | None = None, context: Context | None = None) -> None:
        entity_id = self.vacuum
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            # HA would skip an unavailable entity silently; a stop must not count as sent.
            raise HomeAssistantError(f"{entity_id} is unavailable")
        data = {"entity_id": entity_id, "command": command}
        if params is not None:
            data["params"] = params
        await self.hass.services.async_call("vacuum", "send_command", data, blocking=True, context=context)

    def _arm_stop(self, delay: float) -> None:
        if self._stop_timer:
            self._stop_timer()
        self._gen += 1
        gen = self._gen
        self._stop_timer = async_call_later(self.hass, delay, partial(self._idle_stop, gen))

    def cancel_timer(self) -> None:
        if self._stop_timer:
            self._stop_timer()
            self._stop_timer = None

    async def move(self, velocity: float, rotation: float, duration_ms: int, context: Context | None = None) -> None:
        epoch, asked = self._epoch, self.hass.loop.time()
        async with self._lock:
            if self.closing:
                raise HomeAssistantError("Roborock Camera is unloading")
            if self._broken:
                raise HomeAssistantError("remote drive is stopping after an error; try again")
            if epoch != self._epoch or self.hass.loop.time() - asked > duration_ms / 1000:
                return  # a stop came in, or this move expired while it waited
            # Armed before sending: a failed send still ends in a stop.
            self._arm_stop(duration_ms / 1000 + IDLE_STOP_S)
            cmds = self.drive.move(velocity, rotation, duration_ms, self.hass.loop.time())
            if not cmds:
                return  # too soon after the last session; the caller's next move starts it
            for command, params in cmds:
                if command == "app_rc_move" and (
                    epoch != self._epoch or self.hass.loop.time() - asked > duration_ms / 1000
                ):
                    return  # a stop came in or the move expired while app_rc_start was in flight; the timer ends it
                try:
                    await self.send(command, params, context)
                except BaseException:
                    self._broken = True  # the armed timer still sends the stop
                    raise

    async def stop(self, context: Context | None = None, gen: int | None = None) -> None:
        if gen is None:
            if context is not None and context.user_id:
                await _check_control(self.hass, context, self.vacuum)
            self._epoch += 1
        async with self._lock:
            if gen is not None and gen != self._gen:
                return  # a newer move re-armed the timer while this one waited for the lock
            self.cancel_timer()
            if not self.drive.active:
                return
            try:
                await self.send("app_rc_end", [], context)
            except BaseException:
                self._broken = True  # no new moves while the stop is unconfirmed
                if not self.closing:
                    self._arm_stop(RETRY_STOP_S)  # keep trying until the robot confirms, also after a cancel
                raise
            self.drive.ended(self.hass.loop.time())
            self._broken = False

    async def _idle_stop(self, gen: int, _now) -> None:
        try:
            await self.stop(gen=gen)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("remote drive stop failed, retrying in %s s", RETRY_STOP_S)

    def device(self) -> dr.DeviceEntry | None:
        """The core Roborock device; our entities join it instead of creating a second device."""
        entity = er.async_get(self.hass).async_get(self.entry.data[CONF_VACUUM])
        return dr.async_get(self.hass).async_get(entity.device_id) if entity and entity.device_id else None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    entry.runtime_data = Robot(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_reload))

    async def _on_shutdown() -> None:
        # runs before the stop event, while the Roborock connection is still open
        robot: Robot = entry.runtime_data
        robot.closing = True
        try:
            await robot.stop()
        except Exception:  # noqa: BLE001
            _LOGGER.warning("remote drive stop failed at shutdown")

    entry.async_on_unload(hass.async_add_shutdown_job(HassJob(_on_shutdown)))
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    robot: Robot = entry.runtime_data
    robot.closing = True
    for attempt in range(3):
        try:
            await robot.stop()
            break
        except asyncio.CancelledError:
            robot.closing = False
            robot._arm_stop(RETRY_STOP_S)
            raise
        except Exception:  # noqa: BLE001
            if attempt == 2:
                # Keep the entry (and its stop retry) until the robot confirms the stop.
                robot.closing = False
                robot._arm_stop(RETRY_STOP_S)
                _LOGGER.warning("remote drive stop not confirmed, unload refused")
                return False
            await asyncio.sleep(RETRY_STOP_S)
    robot.cancel_timer()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def _check_control(hass: HomeAssistant, context: Context | None, entity_id: str) -> None:
    """The caller (if a user) may control the vacuum."""
    if context is None or not context.user_id:
        return
    user = await hass.auth.async_get_user(context.user_id)
    if user is None:
        raise UnknownUser(context=context)
    if not user.permissions.check_entity(entity_id, POLICY_CONTROL):
        raise Unauthorized(context=context, entity_id=entity_id, permission=POLICY_CONTROL)


async def _robot(hass: HomeAssistant, call: ServiceCall) -> Robot:
    """The robot for the call's vacuum, after checking that the caller may control that vacuum."""
    entity_id = call.data["entity_id"]
    await _check_control(hass, call.context, entity_id)
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        robot: Robot = entry.runtime_data
        try:
            if robot.vacuum == entity_id:
                return robot
        except HomeAssistantError:
            continue
    raise HomeAssistantError(f"{entity_id} is not set up in Roborock Camera")


ENTITY = {vol.Required("entity_id"): cv.entity_id}


def _map_index(v):
    n = float(v)
    if not n.is_integer() or not 0 <= n <= 3:
        raise vol.Invalid("map_index must be a whole number from 0 to 3")
    return int(n)


def _coord(v):
    n = float(v)
    if not math.isfinite(n):
        raise vol.Invalid("coordinates must be finite numbers")
    return round(n)  # the robot refuses non-integer map coordinates


@callback
def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, "move"):
        return

    async def move(call: ServiceCall) -> None:
        robot = await _robot(hass, call)
        await robot.move(call.data["velocity"], call.data["rotation"], call.data["duration"], call.context)

    async def stop(call: ServiceCall) -> None:
        await (await _robot(hass, call)).stop(call.context)

    async def patrol(call: ServiceCall) -> None:
        robot = await _robot(hass, call)
        await robot.send(
            "app_start_patrol", {"map_index": call.data["map_index"], "points": call.data["points"]}, call.context
        )

    hass.services.async_register(
        DOMAIN,
        "move",
        move,
        vol.Schema(
            {
                **ENTITY,
                vol.Required("velocity"): vol.All(vol.Coerce(float), vol.Range(-MAX_VELOCITY, MAX_VELOCITY)),
                vol.Required("rotation"): vol.All(vol.Coerce(float), vol.Range(-MAX_ROTATION, MAX_ROTATION)),
                vol.Optional("duration", default=500): vol.All(vol.Coerce(int), vol.Range(100, 3000)),
            }
        ),
    )
    hass.services.async_register(DOMAIN, "stop_moving", stop, vol.Schema(ENTITY))
    hass.services.async_register(
        DOMAIN,
        "patrol",
        patrol,
        vol.Schema(
            {
                **ENTITY,
                vol.Optional("map_index", default=0): _map_index,
                vol.Required("points"): vol.All([vol.All([_coord], vol.Length(min=2, max=2))], vol.Length(min=1, max=50)),
            }
        ),
    )
