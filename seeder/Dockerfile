# Seeds a playground through the management APIs (see seed.py)
FROM openfga/cli:v0.8.2@sha256:31a565d14d1c5ca8170d3a2225eb66517d9f490539967bcb3421613c5e2538a4 AS fga

FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY --from=fga /fga /usr/local/bin/fga
WORKDIR /app
COPY requirements.txt .
# pip leaves with the build: the seed never runs it, and what it vendors (certifi, urllib3) would stay in the image unpatched
RUN pip install -r requirements.txt \
 && pip uninstall -y pip
COPY seed.py ./
COPY lab_seeder lab_seeder
# Runs as root: it records what it creates in the mounted /state, whoever owns that folder on the host
ENTRYPOINT ["python", "seed.py"]
