"""Live camera from an RTSP go2rtc stream. Pictures are never pulled in the background: every pull turns the robot camera on."""

from homeassistant.components.camera import Camera, CameraEntityFeature

from .const import CONF_STREAM


async def async_setup_entry(hass, entry, async_add_entities):
    if entry.options.get(CONF_STREAM):
        async_add_entities([RobotCamera(entry)])


class RobotCamera(Camera):
    _attr_has_entity_name = True
    _attr_translation_key = "live"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry):
        super().__init__()
        self._url = entry.options[CONF_STREAM]
        self._attr_unique_id = f"{entry.unique_id}_camera"
        self.device_entry = entry.runtime_data.device()

    async def stream_source(self):
        return self._url

    async def async_camera_image(self, width=None, height=None):
        return None
