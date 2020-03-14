"""Hermes MQTT server for Rhasspy wakeword with Porcupine"""
import io
import json
import logging
import queue
import socket
import struct
import subprocess
import threading
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

WAV_HEADER_BYTES = 44
_LOGGER = logging.getLogger(__name__)

# -----------------------------------------------------------------------------


class WakeHermesMqtt:
    """Hermes MQTT server for Rhasspy wakeword with Porcupine."""

    def __init__(
        self,
        client,
        porcupine: typing.Any,
        model_ids: typing.List[str],
        wakeword_ids: typing.List[str],
        sensitivities: typing.List[float],
        keyword_dirs: typing.Optional[typing.List[Path]] = None,
        siteIds: typing.Optional[typing.List[str]] = None,
        enabled: bool = True,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        udp_audio_port: typing.Optional[int] = None,
        udp_chunk_size: int = 2048,
    ):
        self.client = client
        self.porcupine = porcupine
        self.wakeword_ids = wakeword_ids
        self.model_ids = model_ids
        self.sensitivities = sensitivities

        self.keyword_dirs = keyword_dirs or []
        self.siteIds = siteIds or []
        self.enabled = enabled

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        # Queue of WAV audio chunks to process (plus siteId)
        self.wav_queue: queue.Queue = queue.Queue()

        # Listen for raw audio on UDP too
        self.udp_audio_port = udp_audio_port
        self.udp_chunk_size = udp_chunk_size

        # siteId used for detections from UDP
        self.udp_siteId = "default" if not self.siteIds else self.siteIds[0]

        self.chunk_size = self.porcupine.frame_length * 2
        self.chunk_format = "h" * self.porcupine.frame_length

        # Topics to listen for WAV chunks on
        self.audioframe_topics: typing.List[str] = []
        for siteId in self.siteIds:
            self.audioframe_topics.append(AudioFrame.topic(siteId=siteId))

        self.first_audio: bool = True

        self.audio_buffer = bytes()

    # -------------------------------------------------------------------------

    def handle_audio_frame(self, wav_bytes: bytes, siteId: str = "default"):
        """Process a single audio frame"""
        self.wav_queue.put((wav_bytes, siteId))

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
            if self.keyword_dirs:
                # Add all models from keyword dir
                model_paths = []
                for keyword_dir in self.keyword_dirs:
                    if not keyword_dir.is_dir():
                        _LOGGER.warning("Missing keyword dir: %s", str(keyword_dir))
                        continue

                    for keyword_file in keyword_dir.glob("*.ppn"):
                        model_paths.append(keyword_file)
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

    def detection_thread_proc(self):
        """Handle WAV audio chunks."""
        try:
            while True:
                wav_bytes, siteId = self.wav_queue.get()
                if self.first_audio:
                    _LOGGER.debug("Receiving audio")
                    self.first_audio = False

                # Add to persistent buffer
                audio_data = self.maybe_convert_wav(wav_bytes)
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

                        message = self.handle_detection(keyword_index, siteId=siteId)
                        self.publish(message, wakewordId=wakewordId)

        except Exception:
            _LOGGER.exception("detection_thread_proc")

    # -------------------------------------------------------------------------

    def udp_thread_proc(self):
        """Handle WAV chunks from UDP socket."""
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind(("127.0.0.1", self.udp_audio_port))
            _LOGGER.debug("Listening for audio on UDP port %s", self.udp_audio_port)

            while True:
                wav_bytes, _ = udp_socket.recvfrom(
                    self.udp_chunk_size + WAV_HEADER_BYTES
                )
                self.wav_queue.put((wav_bytes, self.udp_siteId))
        except Exception:
            _LOGGER.exception("udp_thread_proc")

    # -------------------------------------------------------------------------

    def on_connect(self, client, userdata, flags, rc):
        """Connected to MQTT broker."""
        try:
            # Start threads
            threading.Thread(target=self.detection_thread_proc, daemon=True).start()

            if self.udp_audio_port is not None:
                threading.Thread(target=self.udp_thread_proc, daemon=True).start()

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
                    siteId = AudioFrame.get_siteId(msg.topic)
                    self.handle_audio_frame(msg.payload, siteId=siteId)
            elif msg.topic == GetHotwords.topic():
                json_payload = json.loads(msg.payload or "{}")
                if self._check_siteId(json_payload):
                    self.publish(
                        self.handle_get_hotwords(GetHotwords.from_dict(json_payload))
                    )
        except Exception:
            _LOGGER.exception("on_message")
            _LOGGER.error("%s %s", msg.topic, msg.payload)

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
