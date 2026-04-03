FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace

WORKDIR /workspace

RUN pip install --no-cache-dir websockets==12.0

COPY . /workspace
