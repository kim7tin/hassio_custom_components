"""
Support for MQTT vacuums.

For more details about this platform, please refer to the documentation at
https://www.home-assistant.io/components/vacuum.mqtt/
"""
import logging

import voluptuous as vol

from homeassistant.components.vacuum import DOMAIN
from homeassistant.components.mqtt import ATTR_DISCOVERY_HASH
from homeassistant.components.mqtt.discovery import (
    MQTT_DISCOVERY_NEW,
    clear_discovery_hash,
)
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from .schema import CONF_SCHEMA, LEGACY, STATE, MQTT_VACUUM_SCHEMA
from .schema_legacy import PLATFORM_SCHEMA_LEGACY, async_setup_entity_legacy
from .schema_state import PLATFORM_SCHEMA_STATE, async_setup_entity_state

_LOGGER = logging.getLogger(__name__)


def validate_mqtt_vacuum(value):
    """Validate MQTT vacuum schema."""
    schemas = {LEGACY: PLATFORM_SCHEMA_LEGACY, STATE: PLATFORM_SCHEMA_STATE}
    return schemas[value[CONF_SCHEMA]](value)


PLATFORM_SCHEMA = vol.All(
    MQTT_VACUUM_SCHEMA.extend({}, extra=vol.ALLOW_EXTRA), validate_mqtt_vacuum
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up MQTT vacuum through configuration.yaml."""
    await _async_setup_entity(config, async_add_entities, discovery_info)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MQTT vacuum dynamically through MQTT discovery."""

    async def async_discover(discovery_payload):
        """Discover and add a MQTT vacuum."""
        try:
            discovery_hash = discovery_payload.pop(ATTR_DISCOVERY_HASH)
            config = PLATFORM_SCHEMA(discovery_payload)
            await _async_setup_entity(
                config, async_add_entities, config_entry, discovery_hash
            )
        except Exception:
            if discovery_hash:
                clear_discovery_hash(hass, discovery_hash)
            raise

    async_dispatcher_connect(
        hass, MQTT_DISCOVERY_NEW.format(DOMAIN, "mqtt"), async_discover
    )


async def _async_setup_entity(
    config, async_add_entities, config_entry, discovery_hash=None
):
    """Set up the MQTT vacuum."""
    setup_entity = {LEGACY: async_setup_entity_legacy, STATE: async_setup_entity_state}
    await setup_entity[config[CONF_SCHEMA]](
        config, async_add_entities, config_entry, discovery_hash
    )
