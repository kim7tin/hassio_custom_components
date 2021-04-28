"""OTT Pusher platform for notify component."""
import logging

from homeassistant.components.notify import (
    ATTR_TARGET,
    PLATFORM_SCHEMA,
    BaseNotificationService
)
from homeassistant.const import (
    CONF_RECIPIENT,
    HTTP_OK
)
import homeassistant.helpers.config_validation as cv
import requests
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

# PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
#     {
#         vol.Required(CONF_RECIPIENT, default=[]): vol.All(
#             cv.ensure_list, [cv.string]
#         ),
#     }
# )


def get_service(hass, config, discovery_info=None):
    """Get the OTT Pusher notification service."""
    # recipients = config[CONF_RECIPIENT]
    return OTTPusherNotificationService()


class OTTPusherNotificationService(BaseNotificationService):
    """Implementation of a notification service for the OTT Pusher service."""

    def __init__(self):
        """Initialize the service."""
        # self.recipients = recipients

    def send_message(self, message="", **kwargs):
        """Send a message to a user."""
        # keys = kwargs.get(ATTR_TARGET, [])

        for key in kwargs[ATTR_TARGET]:
            try:
                response = requests.get(
                    'https://taymay.herokuapp.com/send/?key={}&message={}'.format(key, message))
            except requests.exceptions.Timeout:
                _LOGGER.exception("Connection to the router timed out")
                continue
            if response.status_code == HTTP_OK:
                _LOGGER.debug("Send message to:\r\n%s", key)
                continue
            _LOGGER.error("Invalid response: %s", response)
