"""Support for MQTT discovery."""
import asyncio
import json
import logging
import re

from homeassistant.components import mqtt
from homeassistant.const import CONF_DEVICE, CONF_PLATFORM
from homeassistant.helpers.discovery import async_load_platform
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.typing import HomeAssistantType

from .abbreviations import ABBREVIATIONS, DEVICE_ABBREVIATIONS
from .const import ATTR_DISCOVERY_HASH, CONF_STATE_TOPIC

_LOGGER = logging.getLogger(__name__)

TOPIC_MATCHER = re.compile(
    r"(?P<component>\w+)/(?:(?P<node_id>[a-zA-Z0-9_-]+)/)"
    r"?(?P<object_id>[a-zA-Z0-9_-]+)/config"
)

SUPPORTED_COMPONENTS = [
    "alarm_control_panel",
    "binary_sensor",
    "camera",
    "climate",
    "cover",
    "fan",
    "light",
    "lock",
    "sensor",
    "switch",
    "vacuum",
]

CONFIG_ENTRY_COMPONENTS = [
    "alarm_control_panel",
    "binary_sensor",
    "camera",
    "climate",
    "cover",
    "fan",
    "light",
    "lock",
    "sensor",
    "switch",
    "vacuum",
]

DEPRECATED_PLATFORM_TO_SCHEMA = {
    "light": {"mqtt_json": "json", "mqtt_template": "template"}
}

# These components require state_topic to be set.
# If not specified, infer state_topic from discovery topic.
IMPLICIT_STATE_TOPIC_COMPONENTS = ["alarm_control_panel", "binary_sensor", "sensor"]


ALREADY_DISCOVERED = "mqtt_discovered_components"
DATA_CONFIG_ENTRY_LOCK = "mqtt_config_entry_lock"
CONFIG_ENTRY_IS_SETUP = "mqtt_config_entry_is_setup"
MQTT_DISCOVERY_UPDATED = "mqtt_discovery_updated_{}"
MQTT_DISCOVERY_NEW = "mqtt_discovery_new_{}_{}"

TOPIC_BASE = "~"


def clear_discovery_hash(hass, discovery_hash):
    """Clear entry in ALREADY_DISCOVERED list."""
    del hass.data[ALREADY_DISCOVERED][discovery_hash]


class MQTTConfig(dict):
    """Dummy class to allow adding attributes."""

    pass


async def async_start(
    hass: HomeAssistantType, discovery_topic, hass_config, config_entry=None
) -> bool:
    """Initialize of MQTT Discovery."""

    async def async_device_message_received(msg):
        """Process the received message."""
        payload = msg.payload
        topic = msg.topic
        topic_trimmed = topic.replace(f"{discovery_topic}/", "", 1)
        match = TOPIC_MATCHER.match(topic_trimmed)

        if not match:
            return

        component, node_id, object_id = match.groups()

        if component not in SUPPORTED_COMPONENTS:
            _LOGGER.warning("Integration %s is not supported", component)
            return

        if payload:
            try:
                payload = json.loads(payload)
            except ValueError:
                _LOGGER.warning("Unable to parse JSON %s: '%s'", object_id, payload)
                return

        payload = MQTTConfig(payload)

        for key in list(payload.keys()):
            abbreviated_key = key
            key = ABBREVIATIONS.get(key, key)
            payload[key] = payload.pop(abbreviated_key)

        if CONF_DEVICE in payload:
            device = payload[CONF_DEVICE]
            for key in list(device.keys()):
                abbreviated_key = key
                key = DEVICE_ABBREVIATIONS.get(key, key)
                device[key] = device.pop(abbreviated_key)

        if TOPIC_BASE in payload:
            base = payload.pop(TOPIC_BASE)
            for key, value in payload.items():
                if isinstance(value, str) and value:
                    if value[0] == TOPIC_BASE and key.endswith("_topic"):
                        payload[key] = "{}{}".format(base, value[1:])
                    if value[-1] == TOPIC_BASE and key.endswith("_topic"):
                        payload[key] = "{}{}".format(value[:-1], base)

        # If present, the node_id will be included in the discovered object id
        discovery_id = " ".join((node_id, object_id)) if node_id else object_id
        discovery_hash = (component, discovery_id)

        if payload:
            # Attach MQTT topic to the payload, used for debug prints
            setattr(payload, "__configuration_source__", f"MQTT (topic: '{topic}')")

            if CONF_PLATFORM in payload and "schema" not in payload:
                platform = payload[CONF_PLATFORM]
                if (
                    component in DEPRECATED_PLATFORM_TO_SCHEMA
                    and platform in DEPRECATED_PLATFORM_TO_SCHEMA[component]
                ):
                    schema = DEPRECATED_PLATFORM_TO_SCHEMA[component][platform]
                    payload["schema"] = schema
                    _LOGGER.warning(
                        '"platform": "%s" is deprecated, ' 'replace with "schema":"%s"',
                        platform,
                        schema,
                    )
            payload[CONF_PLATFORM] = "mqtt"

            if (
                CONF_STATE_TOPIC not in payload
                and component in IMPLICIT_STATE_TOPIC_COMPONENTS
            ):
                # state_topic not specified, infer from discovery topic
                payload[CONF_STATE_TOPIC] = "{}/{}/{}{}/state".format(
                    discovery_topic,
                    component,
                    "%s/" % node_id if node_id else "",
                    object_id,
                )
                _LOGGER.warning(
                    'implicit %s is deprecated, add "%s":"%s" to '
                    "%s discovery message",
                    CONF_STATE_TOPIC,
                    CONF_STATE_TOPIC,
                    payload[CONF_STATE_TOPIC],
                    topic,
                )

            payload[ATTR_DISCOVERY_HASH] = discovery_hash

        if ALREADY_DISCOVERED not in hass.data:
            hass.data[ALREADY_DISCOVERED] = {}
        if discovery_hash in hass.data[ALREADY_DISCOVERED]:
            # Dispatch update
            _LOGGER.info(
                "Component has already been discovered: %s %s, sending update",
                component,
                discovery_id,
            )
            async_dispatcher_send(
                hass, MQTT_DISCOVERY_UPDATED.format(discovery_hash), payload
            )
        elif payload:
            # Add component
            _LOGGER.info("Found new component: %s %s", component, discovery_id)
            hass.data[ALREADY_DISCOVERED][discovery_hash] = None

            if component not in CONFIG_ENTRY_COMPONENTS:
                await async_load_platform(hass, component, "mqtt", payload, hass_config)
                return

            config_entries_key = "{}.{}".format(component, "mqtt")
            async with hass.data[DATA_CONFIG_ENTRY_LOCK]:
                if config_entries_key not in hass.data[CONFIG_ENTRY_IS_SETUP]:
                    await hass.config_entries.async_forward_entry_setup(
                        config_entry, component
                    )
                    hass.data[CONFIG_ENTRY_IS_SETUP].add(config_entries_key)

            async_dispatcher_send(
                hass, MQTT_DISCOVERY_NEW.format(component, "mqtt"), payload
            )

    hass.data[DATA_CONFIG_ENTRY_LOCK] = asyncio.Lock()
    hass.data[CONFIG_ENTRY_IS_SETUP] = set()

    await mqtt.async_subscribe(
        hass, discovery_topic + "/#", async_device_message_received, 0
    )

    return True
