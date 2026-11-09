FROM python:3.11-slim

RUN mkdir -p /data
WORKDIR /data
COPY . /data

RUN pip install --no-cache-dir .

ENV CFDNS_SOCKET=/tmp/cfdns.sock
# Non-editable install: project_root() would point into site-packages, so set config path explicitly
ENV CFDNS_CONFIG=/data/config.json

ENTRYPOINT ["cfdns", "daemon"]
