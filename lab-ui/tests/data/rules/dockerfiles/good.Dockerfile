# syntax=docker/dockerfile:1.7
ARG PYTHON=python:3.14-slim@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef

FROM ${PYTHON} AS base
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    # a comment between continued lines is part of the instruction
    && pip cache purge

FROM base AS test
COPY tests ./tests
RUN python -m unittest discover -s tests -t .

FROM base
COPY --from=test /app/tests /app/tests
COPY --chown=app:app . .
ENV PORT=8000 API_URL=http://api:8000 PASSWORD_FILE=/run/secrets/password TOKEN_URL=http://auth/token
ARG BUILD_DATE
USER app:app
CMD ["python", "-m", "app"]
