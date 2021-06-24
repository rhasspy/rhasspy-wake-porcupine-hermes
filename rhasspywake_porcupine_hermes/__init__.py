"""Hermes MQTT server for Rhasspy wakeword with Porcupine"""
import asyncio
import io
import logging
import queue
import socket
import struct
import threading
import typing
import wave
from dataclasses import dataclass, field
from pathlib import Path

import pvporcupine
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


@dataclass
class SiteInfo:
    """Self-contained information for a single site"""

    site_id: str
    enabled: bool = True
    disabled_reasons: typing.Set[str] = field(default_factory=set)
    detection_thread: typing.Optional[threading.Thread] = None
    audio_buffer: bytes = bytes()
    first_audio: bool = True
    porcupine: typing.Optional[pvporcupine.porcupine.Porcupine] = None

    # Queue of (bytes, is_raw)
    wav_queue: "queue.Queue[typing.Tuple[bytes, bool]]" = field(
        default_factory=queue.Queue
    )

    @property
    def chunk_size(self) -> int:
        """Get number of bytes in an audio frame"""
        assert self.porcupine is not None
        return self.porcupine.frame_length * 2

    @property
    def chunk_format(self) -> str:
        """Get struct format for audio frame"""
        assert self.porcupine is not None
        return "h" * self.porcupine.frame_length


# -----------------------------------------------------------------------------


class WakeHermesMqtt(HermesClient):
    """Hermes MQTT server for Rhasspy wakeword with Porcupine."""

    def __init__(
        self,
        client,
        model_ids: typing.List[str],
        wakeword_ids: typing.List[str],
        sensitivities: typing.List[float],
        keyword_dirs: typing.Optional[typing.List[Path]] = None,
        site_ids: typing.Optional[typing.List[str]] = None,
        sample_rate: int = 16000,
        sample_width: int = 2,
        channels: int = 1,
        udp_audio: typing.Optional[typing.List[typing.Tuple[str, int, str]]] = None,
        udp_chunk_size: int = 2048,
        udp_raw_audio: typing.Optional[typing.Iterable[str]] = None,
        udp_forward_mqtt: typing.Optional[typing.Iterable[str]] = None,
        lang: typing.Optional[str] = None,
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

        self.wakeword_ids = wakeword_ids
        self.model_ids = model_ids
        self.sensitivities = sensitivities

        self.keyword_dirs = keyword_dirs or []

        # Required audio format
        self.sample_rate = sample_rate
        self.sample_width = sample_width
        self.channels = channels

        self.site_info: typing.Dict[str, SiteInfo] = {}

        # Create site information for known sites
        for site_id in self.site_ids:
            site_info = SiteInfo(site_id=site_id)

            # Create and start detection thread
            site_info.detection_thread = threading.Thread(
                target=self.detection_thread_proc, daemon=True, args=(site_info,)
            )
            site_info.detection_thread.start()

            self.site_info[site_id] = site_info

        self.lang = lang

        # Listen for raw audio on UDP too
        self.udp_chunk_size = udp_chunk_size

        # Site ids where UDP audio is raw 16Khz, 16-bit mono PCM chunks instead
        # of WAV chunks.
        self.udp_raw_audio = set(udp_raw_audio or [])

        # Site ids where UDP audio should be forward to MQTT after detection.
        self.udp_forward_mqtt = set(udp_forward_mqtt or [])

        if udp_audio:
            for udp_host, udp_port, udp_site_id in udp_audio:
                threading.Thread(
                    target=self.udp_thread_proc,
                    args=(udp_host, udp_port, udp_site_id),
                    daemon=True,
                ).start()

    # -------------------------------------------------------------------------

    def stop(self):
        """Stop detection threads."""
        _LOGGER.debug("Stopping detection threads...")

        for site_info in self.site_info.values():
            if site_info.detection_thread is not None:
                site_info.wav_queue.put((None, None))
                site_info.detection_thread.join()
                site_info.detection_thread = None

            site_info.porcupine = None

        _LOGGER.debug("Stopped")

    # -------------------------------------------------------------------------

    async def handle_audio_frame(self, wav_bytes: bytes, site_id: str = "default"):
        """Process a single audio frame"""
        site_info = self.site_info.get(site_id)
        if site_info is None:
            # Create information for new site
            site_info = SiteInfo(site_id=site_id)
            site_info.detection_thread = threading.Thread(
                target=self.detection_thread_proc, daemon=True, args=(site_info,)
            )

            site_info.detection_thread.start()
            self.site_info[site_id] = site_info

        site_info.wav_queue.put((wav_bytes, False))

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
                    lang=self.lang,
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

    def detection_thread_proc(self, site_info: SiteInfo):
        """Handle WAV audio chunks."""
        try:
            if site_info.porcupine is None:
                _LOGGER.debug("Loading porcupine for %s", site_info.site_id)
                site_info.porcupine = pvporcupine.create(
                    keyword_paths=[str(kw) for kw in self.model_ids],
                    sensitivities=self.sensitivities,
                )

            assert site_info.porcupine is not None

            while True:
                wav_bytes, is_raw = site_info.wav_queue.get()
                if wav_bytes is None:
                    # Shutdown signal
                    break

                if site_info.first_audio:
                    _LOGGER.debug("Receiving audio %s", site_info.site_id)
                    site_info.first_audio = False

                if is_raw:
                    # Raw audio chunks
                    audio_data = wav_bytes
                else:
                    # WAV chunks
                    audio_data = self.maybe_convert_wav(wav_bytes)

                # Add to persistent buffer
                site_info.audio_buffer += audio_data

                # Process in chunks.
                # Any remaining audio data will be kept in buffer.
                while len(site_info.audio_buffer) >= site_info.chunk_size:
                    chunk = site_info.audio_buffer[: site_info.chunk_size]
                    site_info.audio_buffer = site_info.audio_buffer[
                        site_info.chunk_size :
                    ]

                    unpacked_chunk = struct.unpack_from(site_info.chunk_format, chunk)
                    keyword_index = site_info.porcupine.process(unpacked_chunk)

                    if keyword_index >= 0:
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

                        assert self.loop is not None
                        asyncio.run_coroutine_threadsafe(
                            self.publish_all(
                                self.handle_detection(
                                    keyword_index,
                                    wakeword_id,
                                    site_id=site_info.site_id,
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
            site_info = self.site_info[site_id]
            is_raw_audio = site_id in self.udp_raw_audio
            forward_to_mqtt = site_id in self.udp_forward_mqtt

            udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp_socket.bind((host, port))
            _LOGGER.debug(
                "Listening for audio on UDP %s:%s (siteId=%s, raw=%s)",
                host,
                port,
                site_id,
                is_raw_audio,
            )

            chunk_size = self.udp_chunk_size
            if is_raw_audio:
                chunk_size += WAV_HEADER_BYTES

            while True:
                wav_bytes, _ = udp_socket.recvfrom(chunk_size)

                if site_info.enabled:
                    site_info.wav_queue.put((wav_bytes, is_raw_audio))
                elif forward_to_mqtt:
                    # When the wake word service is disabled, ASR should be active
                    if is_raw_audio:
                        # Re-package as WAV chunk and publish to MQTT
                        with io.BytesIO() as wav_buffer:
                            wav_file: wave.Wave_write = wave.open(wav_buffer, "wb")
                            with wav_file:
                                wav_file.setframerate(self.sample_rate)
                                wav_file.setsampwidth(self.sample_width)
                                wav_file.setnchannels(self.channels)
                                wav_file.writeframes(wav_bytes)

                            publish_wav_bytes = wav_buffer.getvalue()
                    else:
                        # Use WAV chunk as-is
                        publish_wav_bytes = wav_bytes

                    self.publish(
                        AudioFrame(wav_bytes=publish_wav_bytes),
                        site_id=site_info.site_id,
                    )
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
        site_info = self.site_info.get(site_id) if site_id else None

        if isinstance(message, HotwordToggleOn):
            if site_info:
                if message.reason == HotwordToggleReason.UNKNOWN:
                    # Always enable on unknown
                    site_info.disabled_reasons.clear()
                else:
                    site_info.disabled_reasons.discard(message.reason)

                if site_info.disabled_reasons:
                    _LOGGER.debug("Still disabled: %s", site_info.disabled_reasons)
                else:
                    site_info.enabled = True
                    site_info.first_audio = True

                    _LOGGER.debug("Enabled")
        elif isinstance(message, HotwordToggleOff):
            if site_info:
                site_info.enabled = False
                site_info.disabled_reasons.add(message.reason)
                _LOGGER.debug("Disabled")
        elif isinstance(message, AudioFrame):
            if site_info and site_info.enabled:
                await self.handle_audio_frame(
                    message.wav_bytes, site_id=site_info.site_id
                )
        elif isinstance(message, GetHotwords):
            async for hotword_result in self.handle_get_hotwords(message):
                yield hotword_result
