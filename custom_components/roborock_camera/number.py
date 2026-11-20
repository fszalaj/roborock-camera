"""Speaker volume for talking through the robot. The robot has no read-back, so the last value set is restored."""

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([TalkVolume(entry)])


class TalkVolume(RestoreNumber):
    _attr_has_entity_name = True
    _attr_translation_key = "talk_volume"
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 10
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER

    def __init__(self, entry):
        self._robot = entry.runtime_data
        self._attr_unique_id = f"{entry.unique_id}_talk_volume"
        self.device_entry = self._robot.device()
        self._attr_native_value = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_number_data()) is not None:
            self._attr_native_value = last.native_value

    async def async_set_native_value(self, value: float) -> None:
        await self._robot.send("set_voice_chat_volume", {"volume": int(value)}, self._context)
        self._attr_native_value = value
        self.async_write_ha_state()
