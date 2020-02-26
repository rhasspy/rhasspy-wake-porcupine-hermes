"""Hermes MQTT server for Rhasspy wakeword with Porcupine"""
import io
import json
import logging
import struct
import subprocess
import typing
import wave
from pathlib import Path

import attr
from rhasspyhermes.audioserver import AudioFrame
from rhasspyhermes.base import Message
from rhasspyhermes.wake import (
    GetHotwords,
    Hotword,
    HotwordDetected,
    HotwordError,
    Hotwords,
    HotwordToggleOff,
    HotwordToggleOn,
)

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
        keyword_dir: typing.Optional[Path] = None,
        siteIds: typing.Optional[typing.List[str]] = None,
        enabled: bool = True,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
    ):
        self.client = client
        self.porcupine = porcupine
        self.wakeword_ids = wakeword_ids
        self.model_ids = model_ids
        self.sensitivities = sensitivities

        self.keyword_dir = keyword_dir
        self.siteIds = siteIds or []
        self.enabled = enabled

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        self.chunk_size = self.porcupine.frame_length * 2
        self.chunk_format = "h" * self.porcupine.frame_length

        # Topics to listen for WAV chunks on
        self.audioframe_topics: typing.List[str] = []
        for siteId in self.siteIds:
            self.audioframe_topics.append(AudioFrame.topic(siteId=siteId))

        self.first_audio: bool = True

        self.audio_buffer = bytes()

    # -------------------------------------------------------------------------

    def handle_audio_frame(
        self, wav_bytes: bytes, siteId: str = "default"
    ) -> typing.Iterable[
        typing.Tuple[str, typing.Union[HotwordDetected, HotwordError]]
    ]:
        """Process a single audio frame"""
        # Extract/convert audio data
        audio_data = self.maybe_convert_wav(wav_bytes)

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

                if keyword_index < len(self.wakeword_ids):
                    wakewordId = self.wakeword_ids[keyword_index]
                else:
                    wakewordId = "default"

                yield (wakewordId, self.handle_detection(keyword_index, siteId=siteId))

    def handle_detection(
        self, keyword_index, siteId="default"
    ) -> typing.Union[HotwordDetected, HotwordError]:
        """Handle a successful hotword detection"""
        try:
            assert (
                len(self.model_ids) > keyword_index
            ), f"Missing {keyword_index} in models"

            return HotwordDetected(
                siteId=siteId,
                modelId=self.model_ids[keyword_index],
                currentSensitivity=self.sensitivities[keyword_index],
                modelVersion="",
                modelType="personal",
            )
        except Exception as e:
            _LOGGER.exception("handle_detection")
            return HotwordError(error=str(e), context=str(keyword_index), siteId=siteId)

    def handle_get_hotwords(
        self, get_hotwords: GetHotwords
    ) -> typing.Union[Hotwords, HotwordError]:
        """Report available hotwords"""
        try:
            if self.keyword_dir:
                # Add all models from keyword dir
                model_paths = list(self.keyword_dir.glob("*.ppn"))
            else:
                # Add current model(s) only
                model_paths = [Path(model_id) for model_id in self.model_ids]

            models: typing.List[Hotword] = []
            for ppn_file in model_paths:
                words = ppn_file.with_suffix("").name.split("_")
                if len(words) == 1:
                    # porcupine.ppn -> "porcupine"
                    model_words = words[0]
                else:
                    # smart_mirror_linux.ppn -> "smart mirror"
                    model_words = " ".join(words[:-1])

                models.append(Hotword(modelId=ppn_file.name, modelWords=model_words))

            return Hotwords(
                models={m.modelId: m for m in models},
                id=get_hotwords.id,
                siteId=get_hotwords.siteId,
            )

        except Exception as e:
            _LOGGER.exception("handle_get_hotwords")
            return HotwordError(
                error=str(e), context=str(get_hotwords), siteId=get_hotwords.siteId
            )

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        """Connected to MQTT broker."""
        try:
            topics = [
                HotwordToggleOn.topic(),
                HotwordToggleOff.topic(),
                GetHotwords.topic(),
            ]

            if self.audioframe_topics:
                # Specific siteIds
                topics.extend(self.audioframe_topics)
            else:
                # All siteIds
                topics.append(AudioFrame.topic(siteId="+"))

            for topic in topics:
                self.client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)
        except Exception:
            _LOGGER.exception("on_connect")

    def on_message(self, client, userdata, msg):
        """Received message from MQTT broker."""
        try:
            if not msg.topic.endswith("/audioFrame"):
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
            elif self.enabled and AudioFrame.is_topic(msg.topic):
                # Handle audio frames
                if (not self.audioframe_topics) or (
                    msg.topic in self.audioframe_topics
                ):
                    if self.first_audio:
                        _LOGGER.debug("Receiving audio")
                        self.first_audio = False

                    siteId = AudioFrame.get_siteId(msg.topic)
                    for wakewordId, result in self.handle_audio_frame(
                        msg.payload, siteId=siteId
                    ):
                        if isinstance(result, HotwordDetected):
                            # Topic contains wake word id
                            self.publish(result, wakewordId=wakewordId)
                        else:
                            self.publish(result)
            elif msg.topic == GetHotwords.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.publish(
                        self.handle_get_hotwords(GetHotwords.from_dict(json_payload))
                    )

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

    # -------------------------------------------------------------------------

    def _check_siteId(self, json_payload: typing.Dict[str, typing.Any]) -> bool:
        if self.siteIds:
            return json_payload.get("siteId", "default") in self.siteIds

        # All sites
        return True

    # -------------------------------------------------------------------------

    def _convert_wav(self, wav_bytes: bytes) -> bytes:
        """Converts WAV data to required format with sox. Return raw audio."""
        return subprocess.run(
            [
                "sox",
                "-t",
                "wav",
                "-",
                "-r",
                str(self.sample_rate),
                "-e",
                "signed-integer",
                "-b",
                str(self.sample_width * 8),
                "-c",
                str(self.channels),
                "-t",
                "raw",
                "-",
            ],
            check=True,
            stdout=subprocess.PIPE,
            input=wav_bytes,
        ).stdout

    def maybe_convert_wav(self, wav_bytes: bytes) -> bytes:
        """Converts WAV data to required format if necessary. Returns raw audio."""
        with io.BytesIO(wav_bytes) as wav_io:
            with wave.open(wav_io, "rb") as wav_file:
                if (
                    (wav_file.getframerate() != self.sample_rate)
                    or (wav_file.getsampwidth() != self.sample_width)
                    or (wav_file.getnchannels() != self.channels)
                ):
                    # Return converted wav
                    return self._convert_wav(wav_bytes)

                # Return original audio
                return wav_file.readframes(wav_file.getnframes())
