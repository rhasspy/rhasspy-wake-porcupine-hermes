# Rhasspy Wake Porcupine Hermes

[![Continous Integration](https://github.com/rhasspy/rhasspy-wake-porcupine-hermes/workflows/Tests/badge.svg)](https://github.com/rhasspy/rhasspy-wake-porcupine-hermes/actions)
[![GitHub license](https://img.shields.io/github/license/rhasspy/rhasspy-wake-porcupine-hermes.svg)](https://github.com/rhasspy/rhasspy-wake-porcupine-hermes/blob/master/LICENSE)

Implements `hermes/hotword` functionality from [Hermes protocol](https://docs.snips.ai/reference/hermes) using [Porcupine](https://github.com/Picovoice/Porcupine).

## Running With Docker

```bash
docker run -it rhasspy/rhasspy-wake-porcupine-hermes:<VERSION> <ARGS>
```

## Building From Source

Clone the repository and create the virtual environment:

```bash
git clone https://github.com/rhasspy/rhasspy-wake-porcupine-hermes.git
cd rhasspy-wake-porcupine-hermes
make venv
```

Run the `bin/rhasspy-wake-porcupine-hermes` script to access the command-line interface:

```bash
bin/rhasspy-wake-porcupine-hermes --help
```

## Building the Debian Package

Follow the instructions to build from source, then run:

```bash
source .venv/bin/activate
make debian
```

If successful, you'll find a `.deb` file in the `dist` directory that can be installed with `apt`.

## Building the Docker Image

Follow the instructions to build from source, then run:

```bash
source .venv/bin/activate
make docker
```

This will create a Docker image tagged `rhasspy/rhasspy-wake-porcupine-hermes:<VERSION>` where `VERSION` comes from the file of the same name in the source root directory.

NOTE: If you add things to the Docker image, make sure to whitelist them in `.dockerignore`.

## Command-Line Options

```
usage: rhasspywake_porcupine_hermes [-h] --keyword KEYWORD --library LIBRARY
                                    --model MODEL [--wakewordId WAKEWORDID]
                                    [--sensitivity SENSITIVITY]
                                    [--stdin-audio] [--host HOST]
                                    [--port PORT] [--siteId SITEID] [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --keyword KEYWORD     Path(s) to one or more Porcupine keyword file(s)
                        (.ppn)
  --library LIBRARY     Path to Porcupine shared library (.so)
  --model MODEL         Path to Porcupine model (.pv)
  --wakewordId WAKEWORDID
                        Wakeword IDs of each keyword (default: default)
  --sensitivity SENSITIVITY
                        Sensitivities of keywords (default: 0.5)
  --stdin-audio         Read WAV audio from stdin
  --host HOST           MQTT host (default: localhost)
  --port PORT           MQTT port (default: 1883)
  --siteId SITEID       Hermes siteId(s) to listen for (default: all)
  --debug               Print DEBUG messages to the console
```
