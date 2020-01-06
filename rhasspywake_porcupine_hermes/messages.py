"""Provisional messages for hermes/hotword"""
import attr
from rhasspyhermes.base import Message


@attr.s
class HotwordError(Message):
    """Error from Hotword component."""

    error: str = attr.ib()
    context: str = attr.ib(default="")
    siteId: str = attr.ib(default="default")

    @classmethod
    def topic(cls, **kwargs) -> str:
        """Get Hermes topic"""
        return "hermes/error/hotword"
