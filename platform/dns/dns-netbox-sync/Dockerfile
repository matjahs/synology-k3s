FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir kubernetes==31.0.0 requests==2.32.3

WORKDIR /app
COPY sync.py /app/sync.py

USER 65532:65532
ENTRYPOINT ["python", "/app/sync.py"]
