FROM --platform=linux/amd64 qgis/qgis:4.2.2

# `--break-system-packages` allows installing uv to system Python
RUN pip install --no-cache-dir --break-system-packages uv && \
    which uv

# Keep container running for exec commands from Makefile
CMD ["tail", "-f", "/dev/null"]
