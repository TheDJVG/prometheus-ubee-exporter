FROM python:3.10-alpine

# set environment variables
ENV PYTHONFAULTHANDLER=1 \
  PYTHONUNBUFFERED=1 \
  PYTHONHASHSEED=random \
  CRYPTOGRAPHY_DONT_BUILD_RUST=1 \
  PIP_DISABLE_PIP_VERSION_CHECK=1 \
  PIP_NO_CACHE_DIR=1

WORKDIR /tmp
COPY requirements.txt  ./

RUN pip install -r requirements.txt

WORKDIR /app
COPY src/ ./

ENTRYPOINT ["./prometheus-ubee-exporter"]