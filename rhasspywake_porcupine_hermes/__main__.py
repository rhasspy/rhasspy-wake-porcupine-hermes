"""Hermes MQTT service for Rhasspy wakeword with Porcupine"""
import argparse
import asyncio
import itertools
import json
import logging
import os
import platform
import subprocess
import sys
from enum import Enum
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
    parser = argparse.ArgumentParser(prog="rhasspy-wake-porcupine-hermes")
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
        "--wakeword-id",
        action="append",
        help="Wakeword IDs of each keyword (default: use file name)",
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
        "--udp-audio",
        nargs=3,
        action="append",
        help="Host/port/siteId for UDP audio input",
    )

    hermes_cli.add_hermes_args(parser)
    args = parser.parse_args()

    hermes_cli.setup_logging(args)
    _LOGGER.debug(args)

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

    if machine in ["armv6l", "armv7l", "armv8"]:
        # Raspberry Pi
        args.keyword_dir.append(keyword_base / "raspberrypi")
    else:
        # Desktop/server
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
            lib_dir = os.path.join(lib_dir, "raspberry-pi", "arm11")
        elif machine in ["armv7l", "armv8"]:
            # Pi 2 uses Cortex A7
            # Pi 3 uses Cortex A53
            # Pi 4 uses Cortex A72
            cpu_model = guess_cpu_model()
            _LOGGER.debug("Guessing you have an ARM %s", cpu_model)
            lib_dir = os.path.join(lib_dir, "raspberry-pi", str(cpu_model.value))
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
            args.keyword, args.wakeword_id or [], fillvalue=""
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

    udp_audio = []
    if args.udp_audio:
        udp_audio = [
            (host, int(port), site_id) for host, port, site_id in args.udp_audio
        ]

    # Listen for messages
    client = mqtt.Client()
    hermes = WakeHermesMqtt(
        client,
        porcupine_handle,
        args.keyword,
        keyword_names,
        sensitivities,
        keyword_dirs=args.keyword_dir,
        udp_audio=udp_audio,
        site_ids=args.site_id,
    )

    _LOGGER.debug("Connecting to %s:%s", args.host, args.port)
    hermes_cli.connect(client, args)
    client.loop_start()

    try:
        # Run event loop
        asyncio.run(hermes.handle_messages_async())
    except KeyboardInterrupt:
        pass
    finally:
        _LOGGER.debug("Shutting down")
        client.loop_stop()


# -----------------------------------------------------------------------------


class CortexModel(str, Enum):
    """Possible ARM Cortex CPU models."""

    A7 = "cortex-a7"
    A53 = "cortex-a53"
    A72 = "cortex-a72"


def guess_cpu_model() -> CortexModel:
    """Tries to guess which ARM Cortex CPU this program is running on (Pi 2-4)."""
    # Assume Pi 3 if all else fails
    model = CortexModel.A53

    try:
        # Try lscpu
        lscpu_lines = subprocess.check_output(["lscpu"]).splitlines()
        for line_bytes in lscpu_lines:
            line = line_bytes.decode().lower()
            if "cortex-a7" in line:
                # Pi 2
                return CortexModel.A7

            if "cortex-a53" in line:
                # Pi 3
                return CortexModel.A53

            if "cortex-a72" in line:
                # Pi 4
                return CortexModel.A72
    except Exception:
        pass

    try:
        # Try /proc/cpuinfo
        # See: https://www.raspberrypi.org/documentation/hardware/raspberrypi/revision-codes/README.md
        with open("/proc/cpuinfo", "r") as cpuinfo:
            for line in cpuinfo:
                line = line.strip().lower()
                if line.startswith("revision"):
                    revision = line.split(":", maxsplit=1)[1].strip()
                    if revision in ["a01040", "a01041", "a21041", "a22042"]:
                        # Pi 2
                        return CortexModel.A7

                    if revision in [
                        "a02082",
                        "a020d3",
                        "a22082",
                        "a32082",
                        "a52082",
                        "a22083",
                    ]:
                        # Pi 3
                        return CortexModel.A53

                    if revision in ["a03111", "b03111", "b03112", "c03111", "c03112"]:
                        # Pi 4
                        return CortexModel.A72
    except Exception:
        pass

    return model


# -----------------------------------------------------------------------------

if __name__ == "__main__":
    main()
