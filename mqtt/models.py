"""Modesl used by multiple MQTT modules."""
from typing import Union, Callable

import attr

# pylint: disable=invalid-name
PublishPayloadType = Union[str, bytes, int, float, None]


@attr.s(slots=True, frozen=True)
class Message:
    """MQTT Message."""

    topic = attr.ib(type=str)
    payload = attr.ib(type=PublishPayloadType)
    qos = attr.ib(type=int)
    retain = attr.ib(type=bool)


MessageCallbackType = Callable[[Message], None]
