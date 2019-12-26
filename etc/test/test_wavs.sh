#!/usr/bin/env bash

function detect {
    python3 -m rhasspywake_porcupine_hermes \
            --library porcupine/lib/linux/x86_64/libpv_porcupine.so \
            --model porcupine/lib/common/porcupine_params.pv \
            --keyword porcupine/resources/keyword_files/linux/porcupine_linux.ppn \
            --stdin-audio | \
        jq -r '.modelId'
}

model_id="$(detect < etc/test/porcupine.wav)"
if [[ ! -z "${model_id}" ]]; then
    echo "porcupine.wav OK"
else
    echo "porcupine.wav FAILED"
    exit 1
fi

model_id="$(detect < etc/test/what_time_is_it.wav)"
if [[ -z "${model_id}" ]]; then
    echo "what_time_is_it.wav OK"
else
    echo "what_time_is_it.wav FAILED"
    exit 1
fi
