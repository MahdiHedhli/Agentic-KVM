# Agentic-KVM MCP Server
# Runs over stdio for `docker exec` invocation, matching the
# unifi-network-mcp deployment pattern.
#
# Build:  docker build -t pikvm-mcp .
# Run:    docker run -d --name pikvm-mcp \
#           -e PIKVM_TARGETS='[{"name":"lab","host":"pikvm-lab.ts.net","password":"..."}]' \
#           -e PIKVM_OPERATOR_ID=operator@redteam \
#           -v pikvm-mcp-audit:/var/log/pikvm-mcp \
#           pikvm-mcp
# Invoke: docker exec -i pikvm-mcp pikvm-mcp

FROM python:3.12-slim AS base

WORKDIR /app

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml ./
COPY src/ src/

# Install dependencies (production only)
RUN uv pip install --system --no-cache .

# Audit log volume
VOLUME /var/log/pikvm-mcp

# The container stays alive; MCP sessions are started via `docker exec`
# This keeps the container running so docker exec can attach
CMD ["sleep", "infinity"]
