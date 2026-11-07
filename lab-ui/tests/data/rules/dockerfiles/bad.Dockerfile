# syntax=evil/frontend:1
FROM python
FROM node:latest AS builder
FROM python:3.14-slim
ADD https://example.test/archive.tgz /opt/
ADD git@example.test:org/repo.git /src
RUN curl -fsSL https://example.test/install.sh | sh
RUN wget -qO- https://example.test/x | sudo bash
RUN bash -c "$(curl -fsSL https://example.test/install.sh)"
RUN --security=insecure echo
RUN --network=host echo
RUN --mount=type=secret,id=key echo
RUN --mount=type=ssh echo
ENV API_TOKEN=abc123
ARG DB_PASSWORD=hunter2
COPY --from=nginx:1.27 /etc/nginx/nginx.conf /nginx.conf
COPY --from=builder /out /out
USER root
