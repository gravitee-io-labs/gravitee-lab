# Seeds a playground through the management APIs (see seed.py)
FROM openfga/cli:v0.8.1@sha256:1b8035ea4aeb8ad739cbea972488cdda37886644ed004737880b63403bae0fe8 AS fga

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY --from=fga /fga /usr/local/bin/fga
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY seed.py ./
COPY lab_seeder lab_seeder
# Runs as root: it records what it creates in the mounted /state, whoever owns that folder on the host
ENTRYPOINT ["python", "seed.py"]
