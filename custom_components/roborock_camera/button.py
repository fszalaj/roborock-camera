"""One-press robot actions."""

from homeassistant.components.button import ButtonEntity

ACTIONS = {
    "pet_search": ("app_start_pet_patrol", {"map_index": 0}),
    "resume_patrol": ("app_resume_patrol", []),
    "stop_moving": (None, None),
}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(RobotButton(entry, key) for key in ACTIONS)


class RobotButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, entry, key):
        self._robot = entry.runtime_data
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self.device_entry = self._robot.device()

    async def async_press(self) -> None:
        command, params = ACTIONS[self._key]
        if command is None:
            await self._robot.stop(self._context)
        else:
            await self._robot.send(command, params, self._context)
