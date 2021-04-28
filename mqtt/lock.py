"""Support for MQTT locks."""
import logging

import voluptuous as vol

from homeassistant.components import lock, mqtt
from homeassistant.components.lock import LockDevice
from homeassistant.const import (
    CONF_DEVICE,
    CONF_NAME,
    CONF_OPTIMISTIC,
    CONF_VALUE_TEMPLATE,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.typing import ConfigType, HomeAssistantType

from . import (
    ATTR_DISCOVERY_HASH,
    CONF_COMMAND_TOPIC,
    CONF_QOS,
    CONF_RETAIN,
    CONF_STATE_TOPIC,
    CONF_UNIQUE_ID,
    MqttAttributes,
    MqttAvailability,
    MqttDiscoveryUpdate,
    MqttEntityDeviceInfo,
    subscription,
)
from .discovery import MQTT_DISCOVERY_NEW, clear_discovery_hash

_LOGGER = logging.getLogger(__name__)

CONF_PAYLOAD_LOCK = "payload_lock"
CONF_PAYLOAD_UNLOCK = "payload_unlock"

DEFAULT_NAME = "MQTT Lock"
DEFAULT_OPTIMISTIC = False
DEFAULT_PAYLOAD_LOCK = "LOCK"
DEFAULT_PAYLOAD_UNLOCK = "UNLOCK"
PLATFORM_SCHEMA = (
    mqtt.MQTT_RW_PLATFORM_SCHEMA.extend(
        {
            vol.Optional(CONF_DEVICE): mqtt.MQTT_ENTITY_DEVICE_INFO_SCHEMA,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_OPTIMISTIC, default=DEFAULT_OPTIMISTIC): cv.boolean,
            vol.Optional(CONF_PAYLOAD_LOCK, default=DEFAULT_PAYLOAD_LOCK): cv.string,
            vol.Optional(
                CONF_PAYLOAD_UNLOCK, default=DEFAULT_PAYLOAD_UNLOCK
            ): cv.string,
            vol.Optional(CONF_UNIQUE_ID): cv.string,
        }
    )
    .extend(mqtt.MQTT_AVAILABILITY_SCHEMA.schema)
    .extend(mqtt.MQTT_JSON_ATTRS_SCHEMA.schema)
)


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
):
    """Set up MQTT lock panel through configuration.yaml."""
    await _async_setup_entity(config, async_add_entities)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MQTT lock dynamically through MQTT discovery."""

    async def async_discover(discovery_payload):
        """Discover and add an MQTT lock."""
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
        hass, MQTT_DISCOVERY_NEW.format(lock.DOMAIN, "mqtt"), async_discover
    )


async def _async_setup_entity(
    config, async_add_entities, config_entry=None, discovery_hash=None
):
    """Set up the MQTT Lock platform."""
    async_add_entities([MqttLock(config, config_entry, discovery_hash)])


class MqttLock(
    MqttAttributes,
    MqttAvailability,
    MqttDiscoveryUpdate,
    MqttEntityDeviceInfo,
    LockDevice,
):
    """Representation of a lock that can be toggled using MQTT."""

    def __init__(self, config, config_entry, discovery_hash):
        """Initialize the lock."""
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._state = False
        self._sub_state = None
        self._optimistic = False

        # Load config
        self._setup_from_config(config)

        device_config = config.get(CONF_DEVICE)

        MqttAttributes.__init__(self, config)
        MqttAvailability.__init__(self, config)
        MqttDiscoveryUpdate.__init__(self, discovery_hash, self.discovery_update)
        MqttEntityDeviceInfo.__init__(self, device_config, config_entry)

    async def async_added_to_hass(self):
        """Subscribe to MQTT events."""
        await super().async_added_to_hass()
        await self._subscribe_topics()

    async def discovery_update(self, discovery_payload):
        """Handle updated discovery message."""
        config = PLATFORM_SCHEMA(discovery_payload)
        self._setup_from_config(config)
        await self.attributes_discovery_update(config)
        await self.availability_discovery_update(config)
        await self.device_info_discovery_update(config)
        await self._subscribe_topics()
        self.async_write_ha_state()

    def _setup_from_config(self, config):
        """(Re)Setup the entity."""
        self._config = config

        self._optimistic = config[CONF_OPTIMISTIC]

    async def _subscribe_topics(self):
        """(Re)Subscribe to topics."""
        value_template = self._config.get(CONF_VALUE_TEMPLATE)
        if value_template is not None:
            value_template.hass = self.hass

        @callback
        def message_received(msg):
            """Handle new MQTT messages."""
            payload = msg.payload
            if value_template is not None:
                payload = value_template.async_render_with_possible_json_value(payload)
            if payload == self._config[CONF_PAYLOAD_LOCK]:
                self._state = True
            elif payload == self._config[CONF_PAYLOAD_UNLOCK]:
                self._state = False

            self.async_write_ha_state()

        if self._config.get(CONF_STATE_TOPIC) is None:
            # Force into optimistic mode.
            self._optimistic = True
        else:
            self._sub_state = await subscription.async_subscribe_topics(
                self.hass,
                self._sub_state,
                {
                    "state_topic": {
                        "topic": self._config.get(CONF_STATE_TOPIC),
                        "msg_callback": message_received,
                        "qos": self._config[CONF_QOS],
                    }
                },
            )

    async def async_will_remove_from_hass(self):
        """Unsubscribe when removed."""
        self._sub_state = await subscription.async_unsubscribe_topics(
            self.hass, self._sub_state
        )
        await MqttAttributes.async_will_remove_from_hass(self)
        await MqttAvailability.async_will_remove_from_hass(self)

    @property
    def should_poll(self):
        """No polling needed."""
        return False

    @property
    def name(self):
        """Return the name of the lock."""
        return self._config[CONF_NAME]

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def is_locked(self):
        """Return true if lock is locked."""
        return self._state

    @property
    def assumed_state(self):
        """Return true if we do optimistic updates."""
        return self._optimistic

    async def async_lock(self, **kwargs):
        """Lock the device.

        This method is a coroutine.
        """
        mqtt.async_publish(
            self.hass,
            self._config[CONF_COMMAND_TOPIC],
            self._config[CONF_PAYLOAD_LOCK],
            self._config[CONF_QOS],
            self._config[CONF_RETAIN],
        )
        if self._optimistic:
            # Optimistically assume that the lock has changed state.
            self._state = True
            self.async_write_ha_state()

    async def async_unlock(self, **kwargs):
        """Unlock the device.

        This method is a coroutine.
        """
        mqtt.async_publish(
            self.hass,
            self._config[CONF_COMMAND_TOPIC],
            self._config[CONF_PAYLOAD_UNLOCK],
            self._config[CONF_QOS],
            self._config[CONF_RETAIN],
        )
        if self._optimistic:
            # Optimistically assume that the lock has changed state.
            self._state = False
            self.async_write_ha_state()
