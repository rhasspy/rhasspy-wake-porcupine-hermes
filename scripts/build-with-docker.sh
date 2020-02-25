#!/usr/bin/env bash
set -e
this_dir="$( cd "$( dirname "$0" )" && pwd )"
src_dir="$(realpath "${this_dir}/..")"

version="$(cat "${src_dir}/VERSION")"

if [[ -z "${version}" ]]; then
    echo "VERSION missing"
    exit 1
fi

if [[ -z "$(command -v qemu-arm-static)" ]]; then
    echo "Need to install qemu-user-static"
    sudo apt-get update
    sudo apt-get install qemu-arm-static
fi

# Copy qemu for ARM architectures
mkdir -p "${src_dir}/etc"
for qemu_file in qemu-arm-static qemu-aarch64-static; do
    dest_file="${src_dir}/etc/${qemu_file}"

    if [[ ! -s "${dest_file}" ]]; then
        cp "$(which ${qemu_file})" "${dest_file}"
    fi
done

architecture="$(bash "${src_dir}/architecture.sh")"

# Do Docker builds
service_name="$(basename "${src_dir}")"
docker_archs=('amd64' 'arm32v7' 'arm64v8' 'arm32v6')
if [[ ! -z "$1" ]]; then
    docker_archs=("$@")
fi
declare -A friendly_archs
friendly_archs=(['amd64']='amd64' ['arm32v7']='armhf' ['arm64v8']='aarch64' ['arm32v6']='arm32v6')

for docker_arch in "${docker_archs[@]}"; do
    friendly_arch="${friendly_archs[${docker_arch}]}"
    echo "${docker_arch} ${friendly_arch}"

    if [[ -z "${friendly_arch}" ]]; then
       exit 1
    fi

    # Determine mapped directories based on architecture
    case "${architecture}" in
        armhf)
            lib_arch_dir="raspberry-pi/cortex-a53"
            keyword_arch_dir="raspberrypi"
        ;;

        aarch64)
            lib_arch_dir="raspberry-pi/cortex-a53"
            keyword_arch_dir="raspberrypi"
        ;;

        armv6l)
            lib_arch_dir="raspberry-pi/cortex-a7"
            keyword_arch_dir="raspberrypi"
        ;;

        *)
            lib_arch_dir="linux/x86_64"
            keyword_arch_dir="linux"
        ;;
    esac

    docker_tag="rhasspy/${service_name}:${version}-${friendly_arch}"

    docker build "${src_dir}" \
        --build-arg "BUILD_ARCH=${docker_arch}" \
        --build-arg "FRIENDLY_ARCH=${friendly_arch}" \
        --build-arg "PORCUPINE_LIB=${lib_arch_dir}" \
        --build-arg "PORCUPINE_KW=${keyword_arch_dir}" \
        -t "${docker_tag}"
done
