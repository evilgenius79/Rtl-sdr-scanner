#!/usr/bin/env bash
# Wrapper trunk-recorder invokes per finished call. Reads the shared secret
# from the police-scanner env file (same file as the FastAPI service) and
# forwards the upload over loopback HTTP.
set -Eeu

ENV_FILE="${SCANNER_ENV_FILE:-/etc/police-scanner/env}"

if [[ ! -r $ENV_FILE ]]; then
  echo "uploadhook: env file $ENV_FILE not readable" >&2
  exit 10
fi

# Source only the keys we need; reject anything weird.
SECRET=$(grep -E '^SCANNER_SECRET_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2-)
PORT=$(grep -E '^SCANNER_PORT=' "$ENV_FILE" | head -1 | cut -d= -f2-)
PORT=${PORT:-8080}

# Validate PORT is a positive integer in the valid range.
if ! [[ $PORT =~ ^[0-9]+$ ]] || (( PORT < 1 || PORT > 65535 )); then
  echo "uploadhook: invalid PORT '$PORT'" >&2
  exit 12
fi

if [[ -z $SECRET ]]; then
  echo "uploadhook: SCANNER_SECRET_KEY missing" >&2
  exit 11
fi

# Always target loopback regardless of SCANNER_HOST (which may be 0.0.0.0).
export SCANNER_INGEST_URL="http://127.0.0.1:${PORT}/api/tr/upload"
export SCANNER_INGEST_SECRET="$SECRET"

# trunk-recorder calls us with: <audio_file> <json_file>
exec /opt/police-scanner/.venv/bin/python -m police_scanner.uploadhook "$@"
