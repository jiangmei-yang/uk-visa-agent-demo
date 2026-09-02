#!/bin/zsh
set -u

cd "${0:A:h}" || exit 1

echo "UK Visa Agent Demo"
echo "Checking Docker Desktop..."

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is not installed. Install it from:"
  echo "https://www.docker.com/products/docker-desktop/"
  read "?Press Return to close..."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is installed but not running. Open it, wait until it is ready, then try again."
  read "?Press Return to close..."
  exit 1
fi

echo "Preparing the Demo. The first launch may take a few minutes..."
if ! docker compose up --build --detach; then
  echo "The Demo could not be started. Review the message above and try again."
  read "?Press Return to close..."
  exit 1
fi

for attempt in {1..90}; do
  if curl --fail --silent http://127.0.0.1:8000/health >/dev/null 2>&1; then
    echo "The Demo is ready. Opening your browser..."
    open http://127.0.0.1:8000
    echo "You may close this window. Use STOP_DEMO.command when finished."
    exit 0
  fi
  sleep 2
done

echo "The Demo did not become ready in time. Recent diagnostic messages:"
docker compose logs --tail 30
read "?Press Return to close..."
exit 1
