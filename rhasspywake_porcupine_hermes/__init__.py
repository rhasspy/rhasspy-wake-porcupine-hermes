"""Hermes MQTT server for Rhasspy wakeword with Porcupine"""
import asyncio
import logging
import queue
import socket
import struct
import threading
import typing
from pathlib import Path

from rhasspyhermes.audioserver import AudioFrame
from rhasspyhermes.base import Message
from rhasspyhermes.client import GeneratorType, HermesClient, TopicArgs
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
_LOGGER = logging.getLogger("rhasspywake_porcupine_hermes")

# -----------------------------------------------------------------------------


class WakeHermesMqtt(HermesClient):
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
        super().__init__(
            "rhasspywake_porcupine_hermes",
            client,
            sample_rate=sample_rate,
            sample_width=sample_width,
            channels=channels,
            siteIds=siteIds,
        )

        self.subscribe(AudioFrame, HotwordToggleOn, HotwordToggleOff, GetHotwords)

        self.porcupine = porcupine
        self.wakeword_ids = wakeword_ids
        self.model_ids = model_ids
        self.sensitivities = sensitivities

        self.keyword_dirs = keyword_dirs or []
        self.enabled = enabled
        self.disabled_reasons: typing.Set[str] = set()

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
        self.udp_siteId = self.siteId

        self.chunk_size = self.porcupine.frame_length * 2
        self.chunk_format = "h" * self.porcupine.frame_length

        self.audio_buffer = bytes()
        self.first_audio = True

        # Start threads
        threading.Thread(target=self.detection_thread_proc, daemon=True).start()

        if self.udp_audio_port is not None:
            threading.Thread(target=self.udp_thread_proc, daemon=True).start()

    # -------------------------------------------------------------------------

    async def handle_audio_frame(self, wav_bytes: bytes, siteId: str = "default"):
        """Process a single audio frame"""
        self.wav_queue.put((wav_bytes, siteId))

    async def handle_detection(
        self, keyword_index: int, wakewordId: str, siteId="default"
    ) -> typing.AsyncIterable[
        typing.Union[typing.Tuple[HotwordDetected, TopicArgs], HotwordError]
    ]:
        """Handle a successful hotword detection"""
        try:
            assert (
                len(self.model_ids) > keyword_index
            ), f"Missing {keyword_index} in models"

            yield (
                HotwordDetected(
                    siteId=siteId,
                    modelId=self.model_ids[keyword_index],
                    currentSensitivity=self.sensitivities[keyword_index],
                    modelVersion="",
                    modelType="personal",
                ),
                {"wakewordId": wakewordId},
            )
        except Exception as e:
            _LOGGER.exception("handle_detection")
            yield HotwordError(error=str(e), context=str(keyword_index), siteId=siteId)

    async def handle_get_hotwords(
        self, get_hotwords: GetHotwords
    ) -> typing.AsyncIterable[typing.Union[Hotwords, HotwordError]]:
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

            yield Hotwords(
                models={m.modelId: m for m in models},
                id=get_hotwords.id,
                siteId=get_hotwords.siteId,
            )

        except Exception as e:
            _LOGGER.exception("handle_get_hotwords")
            yield HotwordError(
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
                            wakewordId = ""

                        if not wakewordId:
                            # Use file name
                            wakewordId = Path(self.model_ids[keyword_index]).stem

                        asyncio.ensure_future(
                            self.publish_all(
                                self.handle_detection(
                                    keyword_index, wakewordId, siteId=siteId
                                )
                            ),
                            loop=self.loop,
                        )
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

    async def on_message(
        self,
        message: Message,
        siteId: typing.Optional[str] = None,
        sessionId: typing.Optional[str] = None,
        topic: typing.Optional[str] = None,
    ) -> GeneratorType:
        """Received message from MQTT broker."""
        # Check enable/disable messages
        if isinstance(message, HotwordToggleOn):
            self.disabled_reasons.discard(message.reason)
            if self.disabled_reasons:
                _LOGGER.debug("Still disabled: %s", self.disabled_reasons)
            else:
                self.enabled = True
                self.first_audio = True
                _LOGGER.debug("Enabled")
        elif isinstance(message, HotwordToggleOff):
            self.enabled = False
            self.disabled_reasons.add(message.reason)
            _LOGGER.debug("Disabled")
        elif isinstance(message, AudioFrame):
            if self.enabled:
                assert siteId, "Missing siteId"
                await self.handle_audio_frame(message.wav_bytes, siteId=siteId)
        elif isinstance(message, GetHotwords):
            async for hotword_result in self.handle_get_hotwords(message):
                yield hotword_result
