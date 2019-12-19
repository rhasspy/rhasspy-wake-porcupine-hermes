"""Hermes MQTT server for Rhasspy wakeword with Porcupine"""
import io
import json
import logging
import typing
import struct
import wave

import attr
from rhasspyhermes.audioserver import AudioFrame
from rhasspyhermes.base import Message
from rhasspyhermes.wake import HotwordToggleOn, HotwordToggleOff, HotwordDetected

_LOGGER = logging.getLogger(__name__)


class WakeHermesMqtt:
    """Hermes MQTT server for Rhasspy wakeword with Porcupine."""

    def __init__(
        self,
        client,
        porcupine: typing.Any,
        model_ids: typing.List[str],
        wakeword_ids: typing.List[str],
        sensitivities: typing.List[float],
        siteId: str = "default",
        enabled: bool = True,
    ):
        self.client = client
        self.porcupine = porcupine
        self.wakeword_ids = wakeword_ids
        self.model_ids = model_ids
        self.sensitivities = sensitivities
        self.siteId = siteId
        self.enabled = enabled

        self.chunk_size = self.porcupine.frame_length * 2
        self.chunk_format = "h" * self.porcupine.frame_length

        # Topic to listen for WAV chunks on
        self.audioframe_topic: str = AudioFrame.topic(siteId=self.siteId)
        self.first_audio: bool = True

        self.audio_buffer = bytes()

    # -------------------------------------------------------------------------

    def handle_detection(self, keyword_index):
        try:
            detected = HotwordDetected(
                siteId=self.siteId,
                modelId=self.model_ids[keyword_index],
                currentSensitivity=self.sensitivities[keyword_index],
                modelVersion="",
                modelType="personal",
            )

            self.publish(detected, wakeword_id=self.wakeword_ids[keyword_index])
        except Exception:
            _LOGGER.exception("handle_detection")

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        """Connected to MQTT broker."""
        try:
            topics = [
                self.audioframe_topic,
                HotwordToggleOn.topic(),
                HotwordToggleOff.topic(),
            ]
            for topic in topics:
                self.client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)
        except Exception:
            _LOGGER.exception("on_connect")

    def on_message(self, client, userdata, msg):
        """Received message from MQTT broker."""
        try:
            _LOGGER.debug("Received %s byte(s) on %s", len(msg.payload), msg.topic)
            # Check enable/disable messages
            if msg.topic == HotwordToggleOn.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.enabled = True
                    self.first_audio = True
                    _LOGGER.debug("Enabled")
            elif msg.topic == HotwordToggleOff.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.enabled = False
                    _LOGGER.debug("Disabled")

            if not self.enabled:
                # Disabled
                return

            # Handle audio frames
            if msg.topic == self.audioframe_topic:
                if self.first_audio:
                    _LOGGER.debug("Receiving audio")
                    self.first_audio = False

                # Extract audio data.
                # TODO: Convert to appropriate format.
                with io.BytesIO(msg.payload) as wav_io:
                    with wave.open(wav_io) as wav_file:
                        audio_data = wav_file.readframes(wav_file.getnframes())

                # Add to persistent buffer
                self.audio_buffer += audio_data

                # Process in chunks.
                # Any remaining audio data will be kept in buffer.
                while len(self.audio_buffer) >= self.chunk_size:
                    chunk = self.audio_buffer[: self.chunk_size]
                    self.audio_buffer = self.audio_buffer[self.chunk_size :]

                    unpacked_chunk = struct.unpack_from(self.chunk_format, chunk)
                    keyword_index = self.porcupine.process(unpacked_chunk)

                    if keyword_index:
                        # Detection
                        if len(self.model_ids) == 1:
                            keyword_index = 0

                        self.handle_detection(keyword_index)

        except Exception:
            _LOGGER.exception("on_message")

    def publish(self, message: Message, **topic_args):
        """Publish a Hermes message to MQTT."""
        try:
            _LOGGER.debug("-> %s", message)
            topic = message.topic(**topic_args)
            payload = json.dumps(attr.asdict(message))
            _LOGGER.debug("Publishing %s char(s) to %s", len(payload), topic)
            self.client.publish(topic, payload)
        except Exception:
            _LOGGER.exception("on_message")

    def _check_siteId(self, json_payload: typing.Dict[str, typing.Any]) -> bool:
        return json_payload.get("siteId", "default") == self.siteId
