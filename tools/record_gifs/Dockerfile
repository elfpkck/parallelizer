# Extends the project's test image (built by `make build`) with a virtual display,
# a window manager, input automation, and a screen recorder.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
RUN apt-get update && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        xvfb openbox xdotool ffmpeg && \
    rm -rf /var/lib/apt/lists/*
