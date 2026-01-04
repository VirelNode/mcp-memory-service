#!/bin/bash
# Memory Service Startup Script
# Ensures correct configuration for production use

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# ============================================================================
# REQUIRED CONFIGURATION
# ============================================================================

# Embedding model - MUST match database schema
# Database was created with BGE-large (1024 dimensions)
export MCP_EMBEDDING_MODEL="${MCP_EMBEDDING_MODEL:-BAAI/bge-large-en-v1.5}"

# HTTP port - 8100 for memory service (8000 is kubernetes)
export MCP_HTTP_PORT="${MCP_HTTP_PORT:-8100}"

# OAuth - disable for local development, enable for production
export MCP_OAUTH_ENABLED="${MCP_OAUTH_ENABLED:-false}"

# ============================================================================
# OPTIONAL CONFIGURATION
# ============================================================================

# Storage backend (sqlite_vec, cloudflare, hybrid)
export MCP_MEMORY_STORAGE_BACKEND="${MCP_MEMORY_STORAGE_BACKEND:-sqlite_vec}"

# Database path
export MCP_MEMORY_SQLITE_PATH="${MCP_MEMORY_SQLITE_PATH:-$HOME/.local/share/mcp-memory/sqlite_vec.db}"

# HTTPS (disabled by default for local)
export MCP_HTTPS_ENABLED="${MCP_HTTPS_ENABLED:-false}"

# mDNS discovery
export MCP_MDNS_ENABLED="${MCP_MDNS_ENABLED:-false}"

# ============================================================================
# STARTUP
# ============================================================================

echo "======================================"
echo "Memory Service Startup"
echo "======================================"
echo "Embedding Model:  $MCP_EMBEDDING_MODEL"
echo "HTTP Port:        $MCP_HTTP_PORT"
echo "OAuth Enabled:    $MCP_OAUTH_ENABLED"
echo "Storage Backend:  $MCP_MEMORY_STORAGE_BACKEND"
echo "Database Path:    $MCP_MEMORY_SQLITE_PATH"
echo "======================================"

cd "$PROJECT_DIR"

# Check if running in virtual environment
if [ -z "$VIRTUAL_ENV" ] && [ -d ".venv" ]; then
    echo "Activating virtual environment..."
    source .venv/bin/activate
fi

# Run the server
exec python run_server.py "$@"
