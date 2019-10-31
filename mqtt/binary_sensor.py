"""Support for MQTT binary sensors."""
from datetime import timedelta
import logging

import voluptuous as vol

from homeassistant.components import binary_sensor, mqtt
from homeassistant.components.binary_sensor import (
    DEVICE_CLASSES_SCHEMA,
    BinarySensorDevice,
)
from homeassistant.const import (
    CONF_DEVICE,
    CONF_DEVICE_CLASS,
    CONF_FORCE_UPDATE,
    CONF_NAME,
    CONF_PAYLOAD_OFF,
    CONF_PAYLOAD_ON,
    CONF_VALUE_TEMPLATE,
)
from homeassistant.core import callback
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_connect
import homeassistant.helpers.event as evt
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.typing import ConfigType, HomeAssistantType
from homeassistant.util import dt as dt_util

from . import (
    ATTR_DISCOVERY_HASH,
    CONF_QOS,
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

DEFAULT_NAME = "MQTT Binary sensor"
CONF_OFF_DELAY = "off_delay"
DEFAULT_PAYLOAD_OFF = "OFF"
DEFAULT_PAYLOAD_ON = "ON"
DEFAULT_FORCE_UPDATE = False
CONF_EXPIRE_AFTER = "expire_after"

PLATFORM_SCHEMA = (
    mqtt.MQTT_RO_PLATFORM_SCHEMA.extend(
        {
            vol.Optional(CONF_DEVICE): mqtt.MQTT_ENTITY_DEVICE_INFO_SCHEMA,
            vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
            vol.Optional(CONF_EXPIRE_AFTER): cv.positive_int,
            vol.Optional(CONF_FORCE_UPDATE, default=DEFAULT_FORCE_UPDATE): cv.boolean,
            vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
            vol.Optional(CONF_OFF_DELAY): vol.All(vol.Coerce(int), vol.Range(min=0)),
            vol.Optional(CONF_PAYLOAD_OFF, default=DEFAULT_PAYLOAD_OFF): cv.string,
            vol.Optional(CONF_PAYLOAD_ON, default=DEFAULT_PAYLOAD_ON): cv.string,
            vol.Optional(CONF_UNIQUE_ID): cv.string,
        }
    )
    .extend(mqtt.MQTT_AVAILABILITY_SCHEMA.schema)
    .extend(mqtt.MQTT_JSON_ATTRS_SCHEMA.schema)
)


async def async_setup_platform(
    hass: HomeAssistantType, config: ConfigType, async_add_entities, discovery_info=None
):
    """Set up MQTT binary sensor through configuration.yaml."""
    await _async_setup_entity(config, async_add_entities)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up MQTT binary sensor dynamically through MQTT discovery."""

    async def async_discover(discovery_payload):
        """Discover and add a MQTT binary sensor."""
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
        hass, MQTT_DISCOVERY_NEW.format(binary_sensor.DOMAIN, "mqtt"), async_discover
    )


async def _async_setup_entity(
    config, async_add_entities, config_entry=None, discovery_hash=None
):
    """Set up the MQTT binary sensor."""
    async_add_entities([MqttBinarySensor(config, config_entry, discovery_hash)])


class MqttBinarySensor(
    MqttAttributes,
    MqttAvailability,
    MqttDiscoveryUpdate,
    MqttEntityDeviceInfo,
    BinarySensorDevice,
):
    """Representation a binary sensor that is updated by MQTT."""

    def __init__(self, config, config_entry, discovery_hash):
        """Initialize the MQTT binary sensor."""
        self._config = config
        self._unique_id = config.get(CONF_UNIQUE_ID)
        self._state = None
        self._sub_state = None
        self._expiration_trigger = None
        self._delay_listener = None
        self._expired = None
        device_config = config.get(CONF_DEVICE)

        MqttAttributes.__init__(self, config)
        MqttAvailability.__init__(self, config)
        MqttDiscoveryUpdate.__init__(self, discovery_hash, self.discovery_update)
        MqttEntityDeviceInfo.__init__(self, device_config, config_entry)

    async def async_added_to_hass(self):
        """Subscribe mqtt events."""
        await super().async_added_to_hass()
        await self._subscribe_topics()

    async def discovery_update(self, discovery_payload):
        """Handle updated discovery message."""
        config = PLATFORM_SCHEMA(discovery_payload)
        self._config = config
        await self.attributes_discovery_update(config)
        await self.availability_discovery_update(config)
        await self.device_info_discovery_update(config)
        await self._subscribe_topics()
        self.async_write_ha_state()

    async def _subscribe_topics(self):
        """(Re)Subscribe to topics."""
        value_template = self._config.get(CONF_VALUE_TEMPLATE)
        if value_template is not None:
            value_template.hass = self.hass

        @callback
        def off_delay_listener(now):
            """Switch device off after a delay."""
            self._delay_listener = None
            self._state = False
            self.async_write_ha_state()

        @callback
        def state_message_received(msg):
            """Handle a new received MQTT state message."""
            payload = msg.payload
            # auto-expire enabled?
            expire_after = self._config.get(CONF_EXPIRE_AFTER)

            if expire_after is not None and expire_after > 0:

                # When expire_after is set, and we receive a message, assume device is not expired since it has to be to receive the message
                self._expired = False

                # Reset old trigger
                if self._expiration_trigger:
                    self._expiration_trigger()
                    self._expiration_trigger = None

                # Set new trigger
                expiration_at = dt_util.utcnow() + timedelta(seconds=expire_after)

                self._expiration_trigger = async_track_point_in_utc_time(
                    self.hass, self.value_is_expired, expiration_at
                )

            value_template = self._config.get(CONF_VALUE_TEMPLATE)
            if value_template is not None:
                payload = value_template.async_render_with_possible_json_value(
                    payload, variables={"entity_id": self.entity_id}
                )
            if payload == self._config[CONF_PAYLOAD_ON]:
                self._state = True
            elif payload == self._config[CONF_PAYLOAD_OFF]:
                self._state = False
            else:  # Payload is not for this entity
                _LOGGER.warning(
                    "No matching payload found" " for entity: %s with state_topic: %s",
                    self._config[CONF_NAME],
                    self._config[CONF_STATE_TOPIC],
                )
                return

            if self._delay_listener is not None:
                self._delay_listener()
                self._delay_listener = None

            off_delay = self._config.get(CONF_OFF_DELAY)
            if self._state and off_delay is not None:
                self._delay_listener = evt.async_call_later(
                    self.hass, off_delay, off_delay_listener
                )

            self.async_write_ha_state()

        self._sub_state = await subscription.async_subscribe_topics(
            self.hass,
            self._sub_state,
            {
                "state_topic": {
                    "topic": self._config[CONF_STATE_TOPIC],
                    "msg_callback": state_message_received,
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

    @callback
    def value_is_expired(self, *_):
        """Triggered when value is expired."""

        self._expiration_trigger = None
        self._expired = True

        self.async_write_ha_state()

    @property
    def should_poll(self):
        """Return the polling state."""
        return False

    @property
    def name(self):
        """Return the name of the binary sensor."""
        return self._config[CONF_NAME]

    @property
    def is_on(self):
        """Return true if the binary sensor is on."""
        return self._state

    @property
    def device_class(self):
        """Return the class of this sensor."""
        return self._config.get(CONF_DEVICE_CLASS)

    @property
    def force_update(self):
        """Force update."""
        return self._config[CONF_FORCE_UPDATE]

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def available(self) -> bool:
        """Return true if the device is available and value has not expired."""
        expire_after = self._config.get(CONF_EXPIRE_AFTER)
        # pylint: disable=no-member
        return MqttAvailability.available.fget(self) and (
            expire_after is None or not self._expired
        )
