#!/bin/bash
#
# Memory Service Watchdog
# Monitors Claude's memory service health and self-heals on failure
#
# Run via systemd timer every 2 minutes
#

set -euo pipefail

SERVICE_NAME="mcp-memory-service"
HEALTH_URL="http://localhost:8100/api/health"
EMBEDDING_TEST_URL="http://localhost:8100/api/memories"
LOG_TAG="memory-watchdog"
MAX_RETRIES=3
RETRY_DELAY=5

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

check_service_running() {
    systemctl --user is-active --quiet "$SERVICE_NAME"
}

check_health_endpoint() {
    curl -sf --max-time 10 "$HEALTH_URL" >/dev/null 2>&1
}

check_embedding_works() {
    # Try to store a test memory and verify embeddings are working
    local response
    response=$(curl -sf --max-time 30 -X POST "$EMBEDDING_TEST_URL" \
        -H "Content-Type: application/json" \
        -d '{"content": "watchdog health check test", "tags": ["watchdog-test"], "memory_type": "test"}' 2>&1)

    if echo "$response" | grep -q '"success": true'; then
        # Clean up test memory
        local hash
        hash=$(echo "$response" | grep -o '"content_hash": "[^"]*"' | cut -d'"' -f4)
        if [ -n "$hash" ]; then
            curl -sf -X DELETE "http://localhost:8100/api/memories/$hash" >/dev/null 2>&1 || true
        fi
        return 0
    else
        return 1
    fi
}

restart_service() {
    log "Restarting $SERVICE_NAME..."

    # Kill any zombie processes on port 8100
    fuser -k 8100/tcp 2>/dev/null || true
    sleep 2

    # Restart the service
    systemctl --user restart "$SERVICE_NAME"

    # Wait for startup
    sleep 15

    # Verify it came back
    if check_health_endpoint; then
        log "Service restarted successfully"
        return 0
    else
        log "Service failed to restart properly"
        return 1
    fi
}

notify_failure() {
    local message="$1"
    log "CRITICAL: $message"

    # Send notification via ntfy if available
    if curl -sf --max-time 5 "http://localhost:9080/health" >/dev/null 2>&1; then
        curl -sf -X POST "http://localhost:9080/claude-memory" \
            -H "Title: Memory Service Alert" \
            -H "Priority: high" \
            -H "Tags: warning" \
            -d "$message" 2>/dev/null || true
    fi
}

main() {
    log "Starting health check..."

    # Check 1: Is service running?
    if ! check_service_running; then
        log "Service not running, attempting restart..."
        if restart_service; then
            log "Recovery successful"
            exit 0
        else
            notify_failure "Memory service failed to start after restart attempt"
            exit 1
        fi
    fi

    # Check 2: Is health endpoint responding?
    local health_ok=false
    for i in $(seq 1 $MAX_RETRIES); do
        if check_health_endpoint; then
            health_ok=true
            break
        fi
        log "Health check attempt $i/$MAX_RETRIES failed, retrying in ${RETRY_DELAY}s..."
        sleep $RETRY_DELAY
    done

    if ! $health_ok; then
        log "Health endpoint not responding after $MAX_RETRIES attempts"
        if restart_service; then
            log "Recovery successful after health endpoint failure"
            exit 0
        else
            notify_failure "Memory service health endpoint failed, restart unsuccessful"
            exit 1
        fi
    fi

    # Check 3: Can we actually generate embeddings?
    if ! check_embedding_works; then
        log "Embedding generation failed (broken pipe likely)"
        if restart_service; then
            log "Recovery successful after embedding failure"
            exit 0
        else
            notify_failure "Memory service embedding failed, restart unsuccessful"
            exit 1
        fi
    fi

    log "All health checks passed"
    exit 0
}

main "$@"
