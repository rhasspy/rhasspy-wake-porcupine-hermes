#!/usr/bin/env python3
"""Publish WAV using Hermes protocol"""
import argparse
import io
import json
import logging
import os
import sys
import threading
import wave
from pathlib import Path

import paho.mqtt.client as mqtt

_LOGGER = logging.getLogger(__name__)

TOPIC_DETECTED = "hermes/hotword/{wakeword_id}/detected"

# -----------------------------------------------------------------------------


def main():
    """Main method"""
    parser = argparse.ArgumentParser(prog="publish_wav")
    parser.add_argument(
        "--site-id", default="default", help="Site ID to publish audio to"
    )
    parser.add_argument("--session-id", default="", help="Session ID for ASR")
    parser.add_argument(
        "--wakeword-id", default="default", help="ID of wakeword to wait for"
    )
    parser.add_argument("--chunk-size", default=2048, help="Bytes per WAV chunk")
    parser.add_argument(
        "--host", default="localhost", help="MQTT host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=1883, help="MQTT port (default: 1883)"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to the console"
    )
    args, wav_paths = parser.parse_known_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    _LOGGER.debug(args)

    subscribed_event = threading.Event()
    detected_topic = TOPIC_DETECTED.format(wakeword_id=args.wakeword_id)

    def on_connect(client, userdata, flags, rc):
        try:
            for topic in [detected_topic]:
                client.subscribe(topic)
                _LOGGER.debug("Subscribed to %s", topic)

            subscribed_event.set()
        except Exception:
            _LOGGER.exception("on_connect")

    detected_event = threading.Event()

    def on_message(client, userdata, msg):
        try:
            if msg.topic == detected_topic:
                detected_json = json.loads(msg.payload.strip())

                # Print to console
                json.dump(detected_json, sys.stdout)
                print("", file=sys.stdout)
                sys.stdout.flush()

                detected_event.set()
        except Exception:
            _LOGGER.exception("on_message")

    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message

    # Wait until connected and subscribed to topics
    _LOGGER.debug("Connecting to %s:%s", args.host, args.port)
    client.connect(args.host, args.port)
    client.loop_start()
    subscribed_event.wait()

    def send_wav(wav_bytes: bytes):
        with io.BytesIO(wav_bytes) as in_io:
            with wave.open(in_io) as in_wav:
                audio_bytes = in_wav.readframes(in_wav.getnframes())

                try:
                    # Send audio in chunks
                    num_audio_bytes = len(audio_bytes)
                    while audio_bytes:
                        chunk = audio_bytes[: args.chunk_size]
                        audio_bytes = audio_bytes[args.chunk_size :]

                        # Wrap chunk in WAV
                        with io.BytesIO() as out_io:
                            out_wav: wave.Wave_write = wave.open(out_io, "wb")
                            with out_wav:
                                out_wav.setframerate(in_wav.getframerate())
                                out_wav.setsampwidth(in_wav.getsampwidth())
                                out_wav.setnchannels(in_wav.getnchannels())
                                out_wav.writeframes(chunk)

                            # Publish audio frame
                            client.publish(
                                f"hermes/audioServer/{args.site_id}/audioFrame",
                                out_io.getvalue(),
                            )
                except Exception:
                    _LOGGER.exception("send_wav")

                _LOGGER.debug("Sent %s byte(s) of audio data", num_audio_bytes)

    try:
        if wav_paths:
            for wav_path in wav_paths:
                detected_event.clear()

                # Send WAV
                wav_bytes = Path(wav_path).read_bytes()
                send_wav(wav_bytes)

                # Wait for detected event
                _LOGGER.debug("Waiting for detected")
                detected_event.wait()
        else:
            if os.isatty(sys.stdin.fileno()):
                print("Reading WAV data from stdin...", file=sys.stderr)

            wav_bytes = sys.stdin.buffer.read()
            send_wav(wav_bytes)

            # Wait for detected event
            _LOGGER.debug("Waiting for detected")
            detected_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
