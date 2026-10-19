#!/bin/sh
# Creates the demo certificates the APIM gateway uses for TLS and mTLS, in this folder: a local CA, the gateway's server
# certificate (localhost), and a client certificate. Files that already exist are overwritten.
# Needs openssl: OpenSSL 1.1.1 or later, or LibreSSL 3.3 or later (the one macOS ships).
# Usage: sh certs/mtls/generate.sh      (or `make certs`)
set -eu
cd "$(dirname "$0")"

DAYS=3650

# The keystores' password is the setting MTLS_KEYSTORE_PASSWORD, which docker-compose.yml gives the gateway: from the
# environment, else from the .env of the project, else the demo default
PASSWORD=${MTLS_KEYSTORE_PASSWORD:-}
if [ -z "$PASSWORD" ] && [ -f ../../.env ]; then
    PASSWORD=$(sed -n 's/^MTLS_KEYSTORE_PASSWORD=//p' ../../.env | tail -1 | sed -e "s/^[\"']//" -e "s/[\"']\$//")
fi
PASSWORD=${PASSWORD:-gravitee}

# Docker creates an empty folder in place of a mounted file that is missing: a `docker compose up` typed before this
for f in server.p12 ca.pem; do
    if [ -d "$f" ]; then rmdir "$f"; fi
done

# Files only needed while the certificates are made
trap 'rm -f server.csr client.csr ca.srl .server.ext.tmp .client.ext.tmp' EXIT

# openssl prints its progress as it goes: it is heard only when it fails
quiet() {
    if ! output=$("$@" 2>&1); then
        echo "$output" >&2
        exit 1
    fi
}

# CA
quiet openssl req -x509 -newkey rsa:4096 -nodes -days $DAYS \
    -keyout ca-key.pem -out ca.pem -subj "/C=FR/O=Gravitee Lab/CN=Gravitee Lab Local CA"

# Client certificate (mTLS)
printf 'extendedKeyUsage=clientAuth\n' > .client.ext.tmp
quiet openssl req -newkey rsa:2048 -nodes -keyout client-key.pem -out client.csr \
    -subj "/C=FR/O=Gravitee Lab/CN=local-client"
quiet openssl x509 -req -in client.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days $DAYS -out client-cert.pem \
    -extfile .client.ext.tmp
quiet openssl pkcs12 -export -in client-cert.pem -inkey client-key.pem -certfile ca.pem -name client \
    -out client.p12 -passout "pass:$PASSWORD"

# Gateway server certificate (HTTPS listener on :8443). Its keystore comes last: make init takes server.p12 for the
# sign that the whole set exists.
printf 'subjectAltName=DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth\n' > .server.ext.tmp
quiet openssl req -newkey rsa:2048 -nodes -keyout server-key.pem -out server.csr \
    -subj "/C=FR/O=Gravitee Lab/CN=localhost"
quiet openssl x509 -req -in server.csr -CA ca.pem -CAkey ca-key.pem -CAcreateserial -days $DAYS -out server-cert.pem \
    -extfile .server.ext.tmp
quiet openssl pkcs12 -export -in server-cert.pem -inkey server-key.pem -certfile ca.pem -name gateway \
    -out server.p12 -passout "pass:$PASSWORD"

# The keys stay private; the gateway reads server.p12 as its own user (uid 1001), which owns nothing here on Linux
chmod 600 ./*-key.pem client.p12
chmod 644 server.p12
echo "Created the demo certificates in certs/mtls (keystore password: MTLS_KEYSTORE_PASSWORD, gravitee by default)"
