# Rhasspy Wake Porcupine Hermes

[![Continous Integration](https://github.com/rhasspy/rhasspy-wake-porcupine-hermes/workflows/Tests/badge.svg)](https://github.com/rhasspy/rhasspy-wake-porcupine-hermes/actions)
[![GitHub license](https://img.shields.io/github/license/rhasspy/rhasspy-wake-porcupine-hermes.svg)](https://github.com/rhasspy/rhasspy-wake-porcupine-hermes/blob/master/LICENSE)

Implements `hermes/hotword` functionality from [Hermes protocol](https://docs.snips.ai/reference/hermes) using [Porcupine](https://github.com/Picovoice/Porcupine).

## Requirements

* Python 3.7
* [Porcupine](https://github.com/Picovoice/Porcupine)

## Installation

```bash
$ git clone https://github.com/rhasspy/rhasspy-wake-porcupine-hermes
$ cd rhasspy-wake-porcupine-hermes
$ ./configure
$ make
$ make install
```

## Running

```bash
$ bin/rhasspy-wake-porcupine-hermes <ARGS>
```

## Command-Line Options

```
usage: rhasspy-wake-porcupine-hermes [-h] --keyword KEYWORD
                                     [--keyword-dir KEYWORD_DIR]
                                     [--library LIBRARY] [--model MODEL]
                                     [--wakeword-id WAKEWORD_ID]
                                     [--sensitivity SENSITIVITY]
                                     [--stdin-audio]
                                     [--udp-audio UDP_AUDIO UDP_AUDIO UDP_AUDIO]
                                     [--host HOST] [--port PORT]
                                     [--username USERNAME]
                                     [--password PASSWORD] [--tls]
                                     [--tls-ca-certs TLS_CA_CERTS]
                                     [--tls-certfile TLS_CERTFILE]
                                     [--tls-keyfile TLS_KEYFILE]
                                     [--tls-cert-reqs {CERT_REQUIRED,CERT_OPTIONAL,CERT_NONE}]
                                     [--tls-version TLS_VERSION]
                                     [--tls-ciphers TLS_CIPHERS]
                                     [--site-id SITE_ID] [--debug]
                                     [--log-format LOG_FORMAT]

optional arguments:
  -h, --help            show this help message and exit
  --keyword KEYWORD     Path(s) to one or more Porcupine keyword file(s)
                        (.ppn)
  --keyword-dir KEYWORD_DIR
                        Path to directory with keyword files
  --library LIBRARY     Path to Porcupine shared library (.so)
  --model MODEL         Path to Porcupine model (.pv)
  --wakeword-id WAKEWORD_ID
                        Wakeword IDs of each keyword (default: use file name)
  --sensitivity SENSITIVITY
                        Sensitivities of keywords (default: 0.5)
  --stdin-audio         Read WAV audio from stdin
  --udp-audio UDP_AUDIO UDP_AUDIO UDP_AUDIO
                        Host/port/siteId for UDP audio input
  --host HOST           MQTT host (default: localhost)
  --port PORT           MQTT port (default: 1883)
  --username USERNAME   MQTT username
  --password PASSWORD   MQTT password
  --tls                 Enable MQTT TLS
  --tls-ca-certs TLS_CA_CERTS
                        MQTT TLS Certificate Authority certificate files
  --tls-certfile TLS_CERTFILE
                        MQTT TLS certificate file (PEM)
  --tls-keyfile TLS_KEYFILE
                        MQTT TLS key file (PEM)
  --tls-cert-reqs {CERT_REQUIRED,CERT_OPTIONAL,CERT_NONE}
                        MQTT TLS certificate requirements (default:
                        CERT_REQUIRED)
  --tls-version TLS_VERSION
                        MQTT TLS version (default: highest)
  --tls-ciphers TLS_CIPHERS
                        MQTT TLS ciphers to use
  --site-id SITE_ID     Hermes site id(s) to listen for (default: all)
  --debug               Print DEBUG messages to the console
  --log-format LOG_FORMAT
                        Python logger format
```
