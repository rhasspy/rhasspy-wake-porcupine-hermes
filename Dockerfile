ARG BUILD_ARCH=amd64
FROM ${BUILD_ARCH}/python:3.7-buster-slim
ARG PORCUPINE_LIB
ARG PORCUPINE_KW

COPY porcupine/lib/common/porcupine_params.pv /porcupine/
COPY porcupine/lib/${PORCUPINE_LIB}/libporcupine.so /porcupine/
COPY porcupine/resources/keyword_files/${PORCUPINE_KW}/ /porcupine/keywords/

WORKDIR /

COPY rhasspywake_porcupine_hermes/ /rhasspywake_porcupine_hermes

ENTRYPOINT ["python3", "-m", "rhasspywake_porcupine_hermes", "--library", "/porcupine/libporcupine.so", "--model", "/porcupine/porcupine_params.pv"]
