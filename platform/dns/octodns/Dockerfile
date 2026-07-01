FROM python:3.12-slim-bookworm

RUN pip install --no-cache-dir \
    octodns==1.12.0 \
    octodns-netbox-dns==0.3.17 \
    octodns-unifi==1.1.5

WORKDIR /config
ENTRYPOINT ["octodns-sync"]
CMD ["--config-file=/config/production.yaml"]
