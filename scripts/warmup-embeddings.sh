#!/bin/bash
#
# Embedding Model Warm-up Script
# Pre-loads the embedding model to avoid cold start latency and failures
#
# Run after service starts or on boot
#

set -euo pipefail

HEALTH_URL="http://localhost:8100/api/health"
TEST_URL="http://localhost:8100/api/memories"
LOG_TAG="embedding-warmup"
MAX_WAIT=120

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [$LOG_TAG] $1"
    logger -t "$LOG_TAG" "$1" 2>/dev/null || true
}

wait_for_service() {
    log "Waiting for memory service to be ready..."
    local elapsed=0
    while [ $elapsed -lt $MAX_WAIT ]; do
        if curl -sf --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then
            log "Service is responding"
            return 0
        fi
        sleep 5
        elapsed=$((elapsed + 5))
    done
    log "Service did not become ready within ${MAX_WAIT}s"
    return 1
}

warmup_embedding_model() {
    log "Warming up embedding model with test queries..."

    # Run 3 embedding operations to fully warm up the model
    local success_count=0

    for i in 1 2 3; do
        local response
        response=$(curl -sf --max-time 60 -X POST "$TEST_URL" \
            -H "Content-Type: application/json" \
            -d "{\"content\": \"Embedding warmup test $i - loading BAAI/bge-large-en-v1.5 into GPU memory\", \"tags\": [\"warmup\"], \"memory_type\": \"test\"}" 2>&1)

        if echo "$response" | grep -q '"success": true'; then
            success_count=$((success_count + 1))
            log "Warmup $i/3 successful"

            # Clean up test memory
            local hash
            hash=$(echo "$response" | grep -o '"content_hash": "[^"]*"' | cut -d'"' -f4)
            if [ -n "$hash" ]; then
                curl -sf -X DELETE "http://localhost:8100/api/memories/$hash" >/dev/null 2>&1 || true
            fi
        else
            log "Warmup $i/3 failed: $response"
        fi

        sleep 2
    done

    if [ $success_count -ge 2 ]; then
        log "Embedding model warmed up successfully ($success_count/3 tests passed)"
        return 0
    else
        log "Embedding warmup failed ($success_count/3 tests passed)"
        return 1
    fi
}

main() {
    log "Starting embedding warmup..."

    if ! wait_for_service; then
        log "Cannot warmup - service not available"
        exit 1
    fi

    if warmup_embedding_model; then
        log "Warmup complete - memory service is fully operational"
        exit 0
    else
        log "Warmup failed - embedding model may have issues"
        exit 1
    fi
}

main "$@"
