"""Hermes MQTT service for Rhasspy wakeword with Porcupine"""
import argparse
import asyncio
import itertools
import json
import logging
import os
import platform
import sys
from pathlib import Path

import attr
import paho.mqtt.client as mqtt
import rhasspyhermes.cli as hermes_cli

from . import WakeHermesMqtt
from .porcupine import Porcupine

_DIR = Path(__file__).parent
_LOGGER = logging.getLogger("rhasspywake_porcupine_hermes")

# -----------------------------------------------------------------------------


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
        "--keyword-dir",
        action="append",
        default=[],
        help="Path to directory with keyword files",
    )
    parser.add_argument("--library", help="Path to Porcupine shared library (.so)")
    parser.add_argument("--model", help="Path to Porcupine model (.pv)")
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
        "--udp-audio-port", type=int, help="Also listen for WAV audio on UDP"
    )

    hermes_cli.add_hermes_args(parser)
    args = parser.parse_args()

    hermes_cli.setup_logging(args)
    _LOGGER.debug(args)

    try:
        # Load porcupine
        sensitivities = [
            float(ks[1])
            for ks in itertools.zip_longest(
                args.keyword, args.sensitivity or [], fillvalue=0.5
            )
        ]

        machine = platform.machine()

        if args.keyword_dir:
            args.keyword_dir = [Path(d) for d in args.keyword_dir]

        # Add embedded keywords too
        keyword_base = _DIR / "porcupine" / "resources" / "keyword_files"

        if machine in ["armv6l", "armv7l", "aarch64"]:
            args.keyword_dir.append(keyword_base / "raspberrypi")
        else:
            args.keyword_dir.append(keyword_base / "linux")

        # Resolve all keyword files against keyword dirs
        for i, keyword in enumerate(args.keyword):
            maybe_keyword = Path(keyword)
            if not maybe_keyword.is_file():
                # Try to resolve agains keyword dirs
                for keyword_dir in args.keyword_dir:
                    maybe_keyword = keyword_dir / keyword
                    if maybe_keyword.is_file():
                        # Overwrite with resolved path
                        args.keyword[i] = str(maybe_keyword)

        if not args.library:
            # Use embedded library
            lib_dir = os.path.join(_DIR, "porcupine", "lib")
            if machine == "armv6l":
                # Pi 0/1
                lib_dir = os.path.join(lib_dir, "raspberry-pi", "cortex-a7")
            elif machine in ["armv7l", "aarch64"]:
                # Pi 2/3/4
                lib_dir = os.path.join(lib_dir, "raspberry-pi", "cortex-a53")
            else:
                # Assume x86_64
                lib_dir = os.path.join(lib_dir, "linux", "x86_64")

            args.library = os.path.join(lib_dir, "libpv_porcupine.so")

        if not args.model:
            # Use embedded model
            args.model = os.path.join(
                _DIR, "porcupine", "lib", "common", "porcupine_params.pv"
            )

        _LOGGER.debug(
            "Loading porcupine (kw=%s, kwdirs=%s, sensitivity=%s, library=%s, model=%s)",
            args.keyword,
            [str(d) for d in args.keyword_dir],
            sensitivities,
            args.library,
            args.model,
        )

        porcupine_handle = Porcupine(
            args.library,
            args.model,
            keyword_file_paths=[str(kw) for kw in args.keyword],
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

        loop = asyncio.get_event_loop()

        # Listen for messages
        client = mqtt.Client()
        hermes = WakeHermesMqtt(
            client,
            porcupine_handle,
            args.keyword,
            keyword_names,
            sensitivities,
            keyword_dirs=args.keyword_dir,
            udp_audio_port=args.udp_audio_port,
            siteIds=args.siteId,
            loop=loop,
        )

        _LOGGER.debug("Connecting to %s:%s", args.host, args.port)
        hermes_cli.connect(client, args)
        client.loop_start()

        # Run event loop
        hermes.loop.run_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _LOGGER.debug("Shutting down")


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
