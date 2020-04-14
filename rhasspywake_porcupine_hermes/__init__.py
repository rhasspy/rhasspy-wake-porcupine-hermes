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
    HotwordToggleReason,
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
        site_ids: typing.Optional[typing.List[str]] = None,
        enabled: bool = True,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        udp_audio: typing.Optional[typing.List[typing.Tuple[str, int, str]]] = None,
        udp_chunk_size: int = 2048,
    ):
        super().__init__(
            "rhasspywake_porcupine_hermes",
            client,
            sample_rate=sample_rate,
            sample_width=sample_width,
            channels=channels,
            site_ids=site_ids,
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

        # Queue of WAV audio chunks to process (plus site_id)
        self.wav_queue: queue.Queue = queue.Queue()

        self.chunk_size = self.porcupine.frame_length * 2
        self.chunk_format = "h" * self.porcupine.frame_length

        self.audio_buffer = bytes()
        self.first_audio = True

        # Start threads
        threading.Thread(target=self.detection_thread_proc, daemon=True).start()

        # Listen for raw audio on UDP too
        self.udp_chunk_size = udp_chunk_size

        if udp_audio:
            for udp_host, udp_port, udp_site_id in udp_audio:
                threading.Thread(
                    target=self.udp_thread_proc,
                    args=(udp_host, udp_port, udp_site_id),
                    daemon=True,
                ).start()

    # -------------------------------------------------------------------------

    async def handle_audio_frame(self, wav_bytes: bytes, site_id: str = "default"):
        """Process a single audio frame"""
        self.wav_queue.put((wav_bytes, site_id))

    async def handle_detection(
        self, keyword_index: int, wakeword_id: str, site_id="default"
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
                    site_id=site_id,
                    model_id=self.model_ids[keyword_index],
                    current_sensitivity=self.sensitivities[keyword_index],
                    model_version="",
                    model_type="personal",
                ),
                {"wakeword_id": wakeword_id},
            )
        except Exception as e:
            _LOGGER.exception("handle_detection")
            yield HotwordError(
                error=str(e), context=str(keyword_index), site_id=site_id
            )

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

                models.append(Hotword(model_id=ppn_file.name, model_words=model_words))

            yield Hotwords(
                models=models, id=get_hotwords.id, site_id=get_hotwords.site_id
            )

        except Exception as e:
            _LOGGER.exception("handle_get_hotwords")
            yield HotwordError(
                error=str(e), context=str(get_hotwords), site_id=get_hotwords.site_id
            )

    def detection_thread_proc(self):
        """Handle WAV audio chunks."""
        try:
            while True:
                wav_bytes, site_id = self.wav_queue.get()
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
                            wakeword_id = self.wakeword_ids[keyword_index]
                        else:
                            wakeword_id = ""

                        if not wakeword_id:
                            # Use file name
                            wakeword_id = Path(self.model_ids[keyword_index]).stem

                        asyncio.run_coroutine_threadsafe(
                            self.publish_all(
                                self.handle_detection(
                                    keyword_index, wakeword_id, site_id=site_id
                                )
                            ),
                            self.loop,
                        )
        except Exception:
            _LOGGER.exception("detection_thread_proc")

    # -------------------------------------------------------------------------

    def udp_thread_proc(self, host: str, port: int, site_id: str):
        """Handle WAV chunks from UDP socket."""
        try:
            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind((host, port))
            _LOGGER.debug("Listening for audio on UDP %s:%s", host, port)

            while True:
                wav_bytes, _ = udp_socket.recvfrom(
                    self.udp_chunk_size + WAV_HEADER_BYTES
                )

                if self.enabled:
                    self.wav_queue.put((wav_bytes, site_id))
        except Exception:
            _LOGGER.exception("udp_thread_proc")

    # -------------------------------------------------------------------------

    async def on_message_blocking(
        self,
        message: Message,
        site_id: typing.Optional[str] = None,
        session_id: typing.Optional[str] = None,
        topic: typing.Optional[str] = None,
    ) -> GeneratorType:
        """Received message from MQTT broker."""
        # Check enable/disable messages
        if isinstance(message, HotwordToggleOn):
            if message.reason == HotwordToggleReason.UNKNOWN:
                # Always enable on unknown
                self.disabled_reasons.clear()
            else:
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
                assert site_id, "Missing site_id"
                await self.handle_audio_frame(message.wav_bytes, site_id=site_id)
        elif isinstance(message, GetHotwords):
            async for hotword_result in self.handle_get_hotwords(message):
                yield hotword_result
