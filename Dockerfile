FROM python:3.11-slim

RUN mkdir -p /data
WORKDIR /data
COPY . /data

RUN pip install --no-cache-dir .

ENV CFDNS_SOCKET=/tmp/cfdns.sock

ENTRYPOINT ["cfdns", "daemon"]
