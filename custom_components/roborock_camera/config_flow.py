"""Pick a Roborock vacuum and the go2rtc RTSP stream that shows it."""

from urllib.parse import urlsplit

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_STREAM, CONF_VACUUM, DOMAIN

STREAM = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.URL))


def _options_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_STREAM, default=defaults.get(CONF_STREAM, "")): STREAM,
        }
    )


def _check(user_input: dict) -> dict:
    errors = {}
    stream = user_input.get(CONF_STREAM, "").strip()
    if stream:
        try:
            u = urlsplit(stream)
            u.port  # raises on a bad port
            # no credentials, query or fragment: the address is stored in plain entry options
            ok = u.scheme in ("rtsp", "rtsps") and u.hostname and not (u.username or u.password or u.query or u.fragment)
        except ValueError:
            ok = False
        if not ok:
            errors[CONF_STREAM] = "bad_stream"
    return errors


class RoborockCameraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return RoborockCameraOptionsFlow()

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            vacuum = user_input[CONF_VACUUM]
            entity = er.async_get(self.hass).async_get(vacuum)
            if entity is None or entity.platform != "roborock":
                errors[CONF_VACUUM] = "not_roborock"
            else:
                errors = _check(user_input)
            if not errors:
                await self.async_set_unique_id(entity.unique_id)
                self._abort_if_unique_id_configured()
                state = self.hass.states.get(vacuum)
                return self.async_create_entry(
                    title=state.name if state else vacuum,
                    data={CONF_VACUUM: entity.id},
                    options={
                        CONF_STREAM: user_input.get(CONF_STREAM, "").strip(),
                    },
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_VACUUM): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="vacuum", integration="roborock")
                )
            }
        ).extend(_options_schema(user_input or {}).schema)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class RoborockCameraOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = _check(user_input)
            if not errors:
                return self.async_create_entry(
                    data={
                        CONF_STREAM: user_input.get(CONF_STREAM, "").strip(),
                    }
                )
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(user_input or dict(self.config_entry.options)), errors=errors
        )
