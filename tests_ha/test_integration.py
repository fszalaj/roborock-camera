"""The integration inside Home Assistant, with vacuum.send_command recorded instead of a real robot."""

import asyncio
from datetime import timedelta

import pytest
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant.common import MockConfigEntry, async_fire_time_changed

from custom_components.roborock_camera.const import CONF_STREAM, CONF_VACUUM, DOMAIN


class Calls(list):
    fail: dict


@pytest.fixture
def sent(hass):
    calls = Calls()
    fail = {"on": set()}

    async def send_command(call):
        if call.data["command"] in fail["on"]:
            raise HomeAssistantError("robot did not answer")
        calls.append((call.data["entity_id"], call.data["command"], call.data.get("params")))

    hass.services.async_register("vacuum", "send_command", send_command)
    calls.fail = fail
    return calls


@pytest.fixture
async def vacuum(hass):
    roborock = MockConfigEntry(domain="roborock")
    roborock.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=roborock.entry_id, identifiers={("roborock", "duid123")}, name="Robo"
    )
    reg = er.async_get(hass)
    entry = reg.async_get_or_create(
        "vacuum", "roborock", "duid123", suggested_object_id="robo", device_id=device.id,
        config_entry=roborock,
    )
    hass.states.async_set("vacuum.robo", "docked", {"friendly_name": "Robo"})
    return entry


@pytest.fixture
async def setup(hass, vacuum, sent):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Robo",
                unique_id=vacuum.unique_id,
        data={CONF_VACUUM: vacuum.id},
        options={CONF_STREAM: "rtsp://go2rtc:8554/robo"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_flow_stores_registry_id(hass, vacuum, sent):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: "vacuum.robo", CONF_STREAM: "http://x"}
    )
    assert result["errors"] == {CONF_STREAM: "bad_stream"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_VACUUM: "vacuum.robo", CONF_STREAM: ""}
    )
    assert result["type"] == "create_entry"
    assert result["data"] == {CONF_VACUUM: vacuum.id}


async def test_move_then_idle_stop(hass, setup, sent):
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0, "duration": 500}, blocking=True
    )
    assert [c[1] for c in sent] == ["app_rc_start", "app_rc_move"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert [c[1] for c in sent][-1] == "app_rc_end"


async def test_failed_end_keeps_retrying(hass, setup, sent):
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
    )
    sent.fail["on"] = {"app_rc_end"}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "stop_moving", {"entity_id": "vacuum.robo"}, blocking=True)
    assert setup.runtime_data.drive.active
    sent.fail["on"] = set()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert [c[1] for c in sent][-1] == "app_rc_end"
    assert not setup.runtime_data.drive.active


async def test_failed_move_still_stops(hass, setup, sent):
    sent.fail["on"] = {"app_rc_move"}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
        )
    sent.fail["on"] = set()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert [c[1] for c in sent] == ["app_rc_start", "app_rc_end"]


async def test_read_only_user_cannot_drive(hass, setup, sent, hass_read_only_user):
    from homeassistant.core import Context

    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "move",
            {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    assert sent == []


async def test_follows_rename(hass, setup, sent, vacuum):
    er.async_get(hass).async_update_entity("vacuum.robo", new_entity_id="vacuum.robot")
    hass.states.async_set("vacuum.robot", "docked")
    await hass.async_block_till_done()
    await hass.services.async_call(DOMAIN, "stop_moving", {"entity_id": "vacuum.robot"}, blocking=True)
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robot", "velocity": 0, "rotation": 10}, blocking=True
    )
    assert {c[0] for c in sent} == {"vacuum.robot"}


async def test_unload_stops_and_refuses_moves(hass, setup, sent):
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
    )
    robot = setup.runtime_data
    assert await hass.config_entries.async_unload(setup.entry_id)
    assert [c[1] for c in sent][-1] == "app_rc_end"
    with pytest.raises(HomeAssistantError):
        await robot.move(0.1, 0, 500)


async def test_entities_and_buttons(hass, setup, sent):
    assert hass.states.get("camera.robo_live_view") is not None
    await hass.services.async_call("button", "press", {"entity_id": "button.robo_search_for_pet"}, blocking=True)
    assert sent[-1] == ("vacuum.robo", "app_start_pet_patrol", {"map_index": 0})
    await hass.services.async_call(
        "number", "set_value", {"entity_id": "number.robo_talk_volume", "value": 60}, blocking=True
    )
    assert sent[-1] == ("vacuum.robo", "set_voice_chat_volume", {"volume": 60})



async def test_unavailable_vacuum_keeps_stop_pending(hass, setup, sent):
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
    )
    hass.states.async_set("vacuum.robo", "unavailable")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "stop_moving", {"entity_id": "vacuum.robo"}, blocking=True)
    assert setup.runtime_data.drive.active
    hass.states.async_set("vacuum.robo", "idle")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert [c[1] for c in sent][-1] == "app_rc_end"


async def test_stale_timer_does_not_stop_newer_move(hass, setup, sent):
    robot = setup.runtime_data
    await robot.move(0.2, 0, 500)
    old = robot._gen
    await robot.move(0.2, 0, 3000)
    await robot.stop(gen=old)
    assert robot.drive.active and [c[1] for c in sent][-1] == "app_rc_move"


async def test_stop_drops_queued_moves(hass, setup, sent):
    robot = setup.runtime_data
    await robot._lock.acquire()
    queued = hass.async_create_task(robot.move(0.2, 0, 3000))
    await asyncio.sleep(0)
    stopper = hass.async_create_task(robot.stop())
    robot._lock.release()
    await queued
    await stopper
    assert "app_rc_move" not in [c[1] for c in sent]


async def test_unload_refused_until_stop_confirmed(hass, setup, sent):
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
    )
    sent.fail["on"] = {"app_rc_end"}
    assert not await hass.config_entries.async_unload(setup.entry_id)
    sent.fail["on"] = set()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert [c[1] for c in sent][-1] == "app_rc_end"


async def test_entities_join_the_roborock_device(hass, setup, vacuum):
    ent = er.async_get(hass).async_get("button.robo_search_for_pet")
    assert ent.device_id == vacuum.device_id


async def test_stop_during_rc_start_skips_the_move(hass, setup, sent):
    robot = setup.runtime_data
    real = robot.send

    async def slow(command, params=None, context=None):
        if command == "app_rc_start":
            robot._epoch += 1  # a stop request lands while app_rc_start is in flight
        await real(command, params, context)

    robot.send = slow
    await robot.move(0.2, 0, 500)
    assert [c[1] for c in sent] == ["app_rc_start"]


async def test_stop_needs_control_permission(hass, setup, sent, hass_read_only_user):
    from homeassistant.core import Context

    robot = setup.runtime_data
    epoch = robot._epoch
    with pytest.raises(Unauthorized):
        await robot.stop(Context(user_id=hass_read_only_user.id))
    assert robot._epoch == epoch


async def test_failed_start_blocks_moves_until_stop(hass, setup, sent):
    sent.fail["on"] = {"app_rc_move"}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
        )
    sent.fail["on"] = set()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
        )
    assert [c[1] for c in sent] == ["app_rc_start"]
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    move = {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}
    await hass.services.async_call(DOMAIN, "move", move, blocking=True)
    assert [c[1] for c in sent] == ["app_rc_start", "app_rc_end"], "no restart right after the end"
    setup.runtime_data.drive.ended_at -= 10  # the restart gap has passed
    await hass.services.async_call(DOMAIN, "move", move, blocking=True)
    assert [c[1] for c in sent] == ["app_rc_start", "app_rc_end", "app_rc_start", "app_rc_move"]


async def test_patrol_validates_points(hass, setup, sent):
    import voluptuous as vol

    for bad in (
        {"points": [[1]]},
        {"points": [["a", 2]]},
        {"points": [[1, 2]], "map_index": 7},
        {"points": [[1, 2]], "map_index": 3.9},
        {"points": [["nan", 2]]},
        {"points": [["inf", 2]]},
    ):
        with pytest.raises(vol.Invalid):
            await hass.services.async_call(DOMAIN, "patrol", {"entity_id": "vacuum.robo", **bad}, blocking=True)
    await hass.services.async_call(DOMAIN, "patrol", {"entity_id": "vacuum.robo", "points": [[1, 2.6]]}, blocking=True)
    assert sent[-1] == ("vacuum.robo", "app_start_patrol", {"map_index": 0, "points": [[1, 3]]})
    assert all(type(c) is int for c in sent[-1][2]["points"][0])


async def test_stream_url_without_secrets(hass, vacuum, sent):
    for bad in ("rtsp://u:p@go2rtc:8554/v", "rtsp://go2rtc:8554/v?pin=1", "rtsp://go2rtc:8554/v#x", "http://go2rtc/v",
                "rtsp://[::1:8554/v", "rtsp://go2rtc:abc/v"):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_VACUUM: "vacuum.robo", CONF_STREAM: bad}
        )
        assert result["errors"] == {CONF_STREAM: "bad_stream"}, bad


async def test_unconfirmed_stop_blocks_moves(hass, setup, sent):
    await hass.services.async_call(
        DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
    )
    sent.fail["on"] = {"app_rc_end"}
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, "stop_moving", {"entity_id": "vacuum.robo"}, blocking=True)
    sent.fail["on"] = set()
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN, "move", {"entity_id": "vacuum.robo", "velocity": 0.2, "rotation": 0}, blocking=True
        )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=3))
    await hass.async_block_till_done()
    assert [c[1] for c in sent][-1] == "app_rc_end"



class FakeRobot:
    """Core Roborock coordinator stand-in: status settings, rooms and the per-room table."""

    def __init__(self):
        from types import SimpleNamespace as NS

        self.duid = "duid123"
        self.table, self.sets, self.listeners = [], [], []
        self.status = NS(fan_power=102, water_box_mode=235, mop_mode=303, in_cleaning=0)
        robot = self

        class Rooms:
            rooms = [NS(segment_id=1), NS(segment_id=2)]

            async def refresh(self):
                pass

        class Command:
            async def send(self, command, params=None):
                if command == "get_customize_clean_mode":
                    return [dict(e) for e in robot.table]
                robot.sets.append(params)
                robot.table = [dict(e) for e in params]

        self.properties_api = NS(status=self.status, rooms=Rooms(), command=Command())

    def async_add_listener(self, cb):
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)


@pytest.fixture
async def then_mop(hass, vacuum, sent):
    from types import SimpleNamespace as NS
    from homeassistant.config_entries import ConfigEntryState

    core = FakeRobot()
    roborock = hass.config_entries.async_entries("roborock")[0]
    roborock.runtime_data = NS(v1=[core])
    roborock.mock_state(hass, ConfigEntryState.LOADED)
    entry = MockConfigEntry(domain=DOMAIN, title="Robo", unique_id=vacuum.unique_id, data={CONF_VACUUM: vacuum.id})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield core
    await hass.config_entries.async_unload(entry.entry_id)
    roborock.mock_state(hass, ConfigEntryState.NOT_LOADED)  # the core integration is not installed in tests


async def test_clean_then_mop_on_off(hass, then_mop):
    sw = "switch.robo_vacuum_then_mop"
    assert hass.states.get(sw).state == "off"
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    assert hass.states.get(sw).state == "on"
    assert [e["seq_type"] for e in then_mop.table] == [1, 1]
    assert {e["mop_mode"] for e in then_mop.table} == {303}
    await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    assert hass.states.get(sw).state == "off" and then_mop.table == []


async def test_clean_then_mop_waits_for_dock_and_follows_settings(hass, then_mop):
    sw = "switch.robo_vacuum_then_mop"
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    hass.states.async_set("vacuum.robo", "cleaning")
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_off", {"entity_id": sw}, blocking=True)
    then_mop.status.fan_power = 104
    for cb in then_mop.listeners:
        cb()
    await hass.async_block_till_done()
    assert len(then_mop.sets) == 1, "no table write during a run"
    hass.states.async_set("vacuum.robo", "docked")
    for cb in then_mop.listeners:
        cb()
    await hass.async_block_till_done()
    assert len(then_mop.sets) == 2 and {e["fan_power"] for e in then_mop.table} == {104}
    for cb in then_mop.listeners:
        cb()
    await hass.async_block_till_done()
    assert len(then_mop.sets) == 2, "in sync: nothing more is written"


async def test_clean_then_mop_not_during_a_dock_visit_and_sync_rereads(hass, then_mop):
    sw = "switch.robo_vacuum_then_mop"
    then_mop.status.in_cleaning = 1  # docked to wash the mop mid-run
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    assert then_mop.sets == []
    then_mop.status.in_cleaning = 0
    await hass.services.async_call("switch", "turn_on", {"entity_id": sw}, blocking=True)
    then_mop.table = []  # cleared in the vendor app; the cached table still says on
    then_mop.status.fan_power = 104
    for cb in then_mop.listeners:
        cb()
    await hass.async_block_till_done()
    assert then_mop.table == [] and len(then_mop.sets) == 1, "a sync re-reads and does not turn the order back on"
