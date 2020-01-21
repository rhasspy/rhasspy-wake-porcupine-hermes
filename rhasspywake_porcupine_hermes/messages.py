"""Provisional messages for hermes/hotword"""
import attr
from rhasspyhermes.base import Message


@attr.s(auto_attribs=True)
class HotwordError(Message):
    """Error from Hotword component."""

    error: str
    context: str = ""
    siteId: str = "default"

    @classmethod
    def topic(cls, **kwargs) -> str:
        """Get Hermes topic"""
        return "hermes/error/hotword"
