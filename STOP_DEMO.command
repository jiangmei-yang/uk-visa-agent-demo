#!/bin/zsh
set -u

cd "${0:A:h}" || exit 1

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is not installed; there is no Demo container to stop."
  read "?Press Return to close..."
  exit 0
fi

docker compose down
echo "The UK Visa Agent Demo has stopped."
