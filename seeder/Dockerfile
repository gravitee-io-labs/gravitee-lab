# Seeds a playground through the management APIs (see seed.py)
FROM openfga/cli:v0.8.2@sha256:31a565d14d1c5ca8170d3a2225eb66517d9f490539967bcb3421613c5e2538a4 AS fga

FROM python:3.15.1-slim@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
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
