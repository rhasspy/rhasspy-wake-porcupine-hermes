"""Hermes MQTT service for Rhasspy wakeword with Porcupine"""
import argparse
import itertools
import json
import logging
import os
import sys

import attr
import paho.mqtt.client as mqtt

from . import WakeHermesMqtt
from .porcupine import Porcupine

_LOGGER = logging.getLogger(__name__)


def main():
    """Main method."""
    parser = argparse.ArgumentParser(prog="rhasspywake_porcupine_hermes")
    parser.add_argument(
        "--keyword",
        required=True,
        action="append",
        help="Path(s) to one or more Porcupine keyword file(s) (.ppn)",
    )
    parser.add_argument(
        "--library", required=True, help="Path to Porcupine shared library (.so)"
    )
    parser.add_argument("--model", required=True, help="Path to Porcupine model (.pv)")
    parser.add_argument(
        "--wakewordId",
        action="append",
        help="Wakeword IDs of each keyword (default: default)",
    )
    parser.add_argument(
        "--sensitivity",
        action="append",
        help="Sensitivities of keywords (default: 0.5)",
    )
    parser.add_argument(
        "--stdin-audio", action="store_true", help="Read WAV audio from stdin"
    )
    parser.add_argument(
        "--host", default="localhost", help="MQTT host (default: localhost)"
    )
    parser.add_argument(
        "--port", type=int, default=1883, help="MQTT port (default: 1883)"
    )
    parser.add_argument(
        "--siteId",
        action="append",
        help="Hermes siteId(s) to listen for (default: all)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="Print DEBUG messages to the console"
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    _LOGGER.debug(args)

    try:
        # Load porcupine
        sensitivities = [
            float(ks[1])
            for ks in itertools.zip_longest(
                args.keyword, args.sensitivity or [], fillvalue=0.5
            )
        ]

        _LOGGER.debug(
            "Loading porcupine (kw=%s, sensitivity=%s, library=%s, model=%s)",
            args.keyword,
            sensitivities,
            args.library,
            args.model,
        )

        porcupine_handle = Porcupine(
            args.library,
            args.model,
            keyword_file_paths=args.keyword,
            sensitivities=sensitivities,
        )

        keyword_names = [
            kn[1]
            for kn in itertools.zip_longest(
                args.keyword, args.wakewordId or [], fillvalue="default"
            )
        ]

        if args.stdin_audio:
            # Read WAV from stdin, detect, and exit
            client = None
            hermes = WakeHermesMqtt(
                client, porcupine_handle, args.keyword, keyword_names, sensitivities
            )

            if os.isatty(sys.stdin.fileno()):
                print("Reading WAV data from stdin...", file=sys.stderr)

            wav_bytes = sys.stdin.buffer.read()

            # Print results as JSON
            for result in hermes.handle_audio_frame(wav_bytes):
                result_dict = attr.asdict(result)
                json.dump(result_dict, sys.stdout)

            return

        # Listen for messages
        client = mqtt.Client()
        hermes = WakeHermesMqtt(
            client,
            porcupine_handle,
            args.keyword,
            keyword_names,
            sensitivities,
            siteIds=args.siteId,
        )

        def on_disconnect(client, userdata, flags, rc):
            try:
                # Automatically reconnect
                _LOGGER.info("Disconnected. Trying to reconnect...")
                client.reconnect()
            except Exception:
                logging.exception("on_disconnect")

        # Connect
        client.on_connect = hermes.on_connect
        client.on_disconnect = on_disconnect
        client.on_message = hermes.on_message

        _LOGGER.debug("Connecting to %s:%s", args.host, args.port)
        client.connect(args.host, args.port)

        client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _LOGGER.debug("Shutting down")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
