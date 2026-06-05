#!/bin/bash
set -e
cd "$(dirname "$0")"

# Load API key from .env
export ANTHROPIC_API_KEY=$(grep '^ANTHROPIC_API_KEY=' .env | cut -d'=' -f2-)

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "ERROR: ANTHROPIC_API_KEY not found in .env"
  exit 1
fi

# Remove any existing stopped container
docker rm -f openhands-app 2>/dev/null || true

# Create state directory if it doesn't exist
mkdir -p ~/.openhands-state

docker run -d \
  --pull=never \
  -e SANDBOX_RUNTIME_CONTAINER_IMAGE=ghcr.io/all-hands-ai/runtime:0.40-nikolaik \
  -e LLM_API_KEY="$ANTHROPIC_API_KEY" \
  -e LLM_MODEL=claude-sonnet-4-6 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -v "$HOME/.openhands-state:/.openhands-state" \
  -p 3000:3000 \
  --add-host host.docker.internal:host-gateway \
  --name openhands-app \
  ghcr.io/all-hands-ai/openhands:0.40

echo "Container started. Waiting for startup..."
sleep 10
docker logs openhands-app --tail 5
echo ""
echo "Open: http://localhost:3000"
