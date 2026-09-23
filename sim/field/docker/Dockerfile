# openEMS (L2, full-wave FDTD) for the servodrive simulation.
# This machine has no passwordless sudo, so the documented source build happens
# inside a container; SPEC.md 5.3 names docker as the sanctioned alternative.
# Mount only sim/.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PIP_BREAK_SYSTEM_PACKAGES=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake git ca-certificates \
        libhdf5-dev libvtk9-dev libboost-all-dev libcgal-dev libtinyxml-dev \
        qtbase5-dev libvtk9-qt-dev \
        python3 python3-dev python3-pip python3-venv python3-setuptools \
        python3-numpy python3-matplotlib python3-h5py cython3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
RUN git clone --recursive --depth 1 https://github.com/thliebig/openEMS-Project.git
RUN cd openEMS-Project && ./update_openEMS.sh /opt/openEMS --python --skip-dep-check

ENV PATH=/opt/openEMS/bin:$PATH
ENV LD_LIBRARY_PATH=/opt/openEMS/lib:/opt/openEMS/lib64
WORKDIR /work
