#!/bin/zsh
set -eu

cd "${0:A:h}" || exit 1

echo "UK Visa Agent — free provider webhook tunnel"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it from https://docs.astral.sh/uv/"
  read "?Press Return to close..."
  exit 1
fi
if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required. On macOS: brew install cloudflared"
  read "?Press Return to close..."
  exit 1
fi

echo "Checking the optional provider dependencies..."
uv sync --extra live --inexact >/dev/null

tunnel_log=$(mktemp)
cloudflared tunnel --url http://127.0.0.1:8001 --no-autoupdate >"$tunnel_log" 2>&1 &
tunnel_pid=$!
cleanup() {
  kill "$tunnel_pid" >/dev/null 2>&1 || true
  rm -f "$tunnel_log"
}
trap cleanup EXIT INT TERM

public_base=""
for attempt in {1..30}; do
  public_base=$(sed -nE 's#.*(https://[a-zA-Z0-9-]+\.trycloudflare\.com).*#\1#p' "$tunnel_log" | head -1)
  if [[ -n "$public_base" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$public_base" ]]; then
  echo "The free HTTPS tunnel could not be created. Recent messages:"
  tail -20 "$tunnel_log"
  exit 1
fi

export TWILIO_WEBHOOK_PUBLIC_URL="${public_base}/webhooks/twilio/whatsapp"
echo ""
echo "Provider-only public endpoint:"
echo "$TWILIO_WEBHOOK_PUBLIC_URL"
echo ""
if [[ -z "${TWILIO_ACCOUNT_SID:-}" || -z "${TWILIO_AUTH_TOKEN:-}" ]]; then
  echo "The endpoint is safely online but will return 'not configured' until the Twilio"
  echo "test Account SID and Auth Token are supplied as environment variables."
else
  echo "Copy the exact endpoint above into Twilio's 'When a message comes in' setting."
fi
echo "The review console and case APIs are not exposed. Press Control-C to stop."
echo ""

uv run visa-agent webhook-server --host 127.0.0.1 --port 8001
