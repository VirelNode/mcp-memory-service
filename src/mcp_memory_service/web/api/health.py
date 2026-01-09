# Copyright 2024 Heinrich Krupp
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Health check endpoints for the HTTP interface.
"""

import logging
import time
import platform
import psutil

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from typing import Dict, Any, Optional, TYPE_CHECKING

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ...storage.base import MemoryStorage
from ..dependencies import get_storage
from ... import __version__
from ...config import OAUTH_ENABLED

# OAuth authentication imports (conditional)
if OAUTH_ENABLED or TYPE_CHECKING:
    from ..oauth.middleware import require_read_access, AuthenticationResult
else:
    # Provide type stubs when OAuth is disabled
    AuthenticationResult = None
    require_read_access = None

router = APIRouter()


class TimeResponse(BaseModel):
    """Current time response for temporal awareness."""
    iso: str
    human: str
    date: str
    time: str
    timezone: str
    unix: str
    day_of_week: str
    hour_24: str


class SessionResponse(BaseModel):
    """Current session information for temporal awareness."""
    session_number: int
    session_start: str
    session_start_human: str
    elapsed_seconds: int
    elapsed_human: str
    current_time: str
    current_time_human: str
    gap_from_previous: Optional[str] = None


class HealthResponse(BaseModel):
    """Basic health check response."""
    status: str
    version: str
    timestamp: str
    uptime_seconds: float


class DetailedHealthResponse(BaseModel):
    """Detailed health check response."""
    status: str
    version: str
    timestamp: str
    uptime_seconds: float
    storage: Dict[str, Any]
    system: Dict[str, Any]
    performance: Dict[str, Any]
    statistics: Dict[str, Any] = None


# Track startup time for uptime calculation
_startup_time = time.time()


@router.get("/health", response_model=HealthResponse)
async def health_check():
    """Basic health check endpoint."""
    return HealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=time.time() - _startup_time
    )


@router.get("/time", response_model=TimeResponse)
async def get_current_time():
    """Get current real-world time for temporal awareness."""
    now = datetime.now()
    tz_name = time.tzname[0] if time.daylight == 0 else time.tzname[1]

    return TimeResponse(
        iso=now.isoformat(),
        human=now.strftime("%A, %B %d, %Y at %I:%M:%S %p"),
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%I:%M:%S %p"),
        timezone=tz_name,
        unix=str(int(now.timestamp())),
        day_of_week=now.strftime("%A"),
        hour_24=now.strftime("%H:%M")
    )


def format_elapsed_time(seconds: int) -> str:
    """Format elapsed time in human-readable format."""
    if seconds < 60:
        return f"{seconds} seconds"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        if minutes > 0:
            return f"{hours} hour{'s' if hours != 1 else ''} {minutes} min"
        return f"{hours} hour{'s' if hours != 1 else ''}"


@router.get("/session", response_model=SessionResponse)
async def get_current_session(
    storage: MemoryStorage = Depends(get_storage)
):
    """Get current Claude Code session information for temporal awareness.

    Returns session number, start time, and elapsed duration.
    Useful for Claude to know how long the current conversation has been running.
    """
    now = datetime.now()

    # Search for the most recent session-start memory
    try:
        results = await storage.search_by_tags(
            tags=["session-start"],
            operation="OR"
        )

        if results and len(results) > 0:
            # Sort by created_at descending and take the most recent
            sorted_results = sorted(results, key=lambda x: x.created_at if hasattr(x, 'created_at') else 0, reverse=True)
            session_memory = sorted_results[0]

            # Handle both dict and Memory object formats
            content = session_memory.content if hasattr(session_memory, 'content') else session_memory.get('content', '')

            # Parse session metadata from content
            session_number = 1
            session_start = None
            gap_from_previous = None

            for line in content.split('\n'):
                if line.startswith('Session #'):
                    try:
                        session_number = int(line.replace('Session #', '').strip())
                    except ValueError:
                        pass
                elif line.startswith('Started:'):
                    session_start = line.replace('Started:', '').strip()
                elif line.startswith('Gap from previous:'):
                    gap_from_previous = line.replace('Gap from previous:', '').strip()

            # Calculate elapsed time (using UTC for comparison)
            elapsed_seconds = 0
            if session_start:
                try:
                    # Parse session start time (stored in UTC with Z suffix)
                    start_dt = datetime.fromisoformat(session_start.replace('Z', '+00:00'))
                    # Get current time in UTC for comparison
                    now_utc = datetime.now(timezone.utc)
                    elapsed_seconds = int((now_utc - start_dt).total_seconds())
                except Exception:
                    pass

            return SessionResponse(
                session_number=session_number,
                session_start=session_start or now.isoformat(),
                session_start_human=datetime.fromisoformat(session_start).strftime("%I:%M %p") if session_start else now.strftime("%I:%M %p"),
                elapsed_seconds=elapsed_seconds,
                elapsed_human=format_elapsed_time(elapsed_seconds),
                current_time=now.isoformat(),
                current_time_human=now.strftime("%A, %B %d, %Y at %I:%M:%S %p"),
                gap_from_previous=gap_from_previous
            )

    except Exception as e:
        logger.warning(f"Could not retrieve session info: {e}")

    # Return default response if no session found
    return SessionResponse(
        session_number=0,
        session_start=now.isoformat(),
        session_start_human=now.strftime("%I:%M %p"),
        elapsed_seconds=0,
        elapsed_human="Just started",
        current_time=now.isoformat(),
        current_time_human=now.strftime("%A, %B %d, %Y at %I:%M:%S %p"),
        gap_from_previous=None
    )


@router.get("/health/detailed", response_model=DetailedHealthResponse)
async def detailed_health_check(
    storage: MemoryStorage = Depends(get_storage),
    user: AuthenticationResult = Depends(require_read_access) if OAUTH_ENABLED else None
):
    """Detailed health check with system and storage information."""
    
    # Get system information
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    
    system_info = {
        "platform": platform.system(),
        "platform_version": platform.version(),
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(),
        "memory_total_gb": round(memory_info.total / (1024**3), 2),
        "memory_available_gb": round(memory_info.available / (1024**3), 2),
        "memory_percent": memory_info.percent,
        "disk_total_gb": round(disk_info.total / (1024**3), 2),
        "disk_free_gb": round(disk_info.free / (1024**3), 2),
        "disk_percent": round((disk_info.used / disk_info.total) * 100, 2)
    }
    
    # Get storage information (support all storage backends)
    try:
        # Get statistics from storage using universal get_stats() method
        if hasattr(storage, 'get_stats') and callable(getattr(storage, 'get_stats')):
            # All storage backends now have async get_stats()
            stats = await storage.get_stats()
        else:
            stats = {"error": "Storage backend doesn't support statistics"}

        if "error" not in stats:
            # Detect backend type from storage class or stats
            backend_name = stats.get("storage_backend", storage.__class__.__name__)
            if "sqlite" in backend_name.lower():
                backend_type = "sqlite-vec"
            elif "cloudflare" in backend_name.lower():
                backend_type = "cloudflare"
            elif "hybrid" in backend_name.lower():
                backend_type = "hybrid"
            else:
                backend_type = backend_name

            storage_info = {
                "backend": backend_type,
                "status": "connected",
                "accessible": True
            }

            # Add backend-specific information if available
            if hasattr(storage, 'db_path'):
                storage_info["database_path"] = storage.db_path
            if hasattr(storage, 'embedding_model_name'):
                storage_info["embedding_model"] = storage.embedding_model_name

            # Add sync status for hybrid backend
            if backend_type == "hybrid" and hasattr(storage, 'get_sync_status'):
                try:
                    sync_status = await storage.get_sync_status()
                    storage_info["sync_status"] = {
                        "is_running": sync_status.get('is_running', False),
                        "last_sync_time": sync_status.get('last_sync_time', 0),
                        "pending_operations": sync_status.get('pending_operations', 0),
                        "operations_processed": sync_status.get('operations_processed', 0),
                        "operations_failed": sync_status.get('operations_failed', 0),
                        "time_since_last_sync": time.time() - sync_status.get('last_sync_time', 0) if sync_status.get('last_sync_time', 0) > 0 else 0
                    }
                except Exception as sync_err:
                    storage_info["sync_status"] = {"error": str(sync_err)}

            # Merge all stats
            storage_info.update(stats)
        else:
            storage_info = {
                "backend": storage.__class__.__name__,
                "status": "error",
                "accessible": False,
                "error": stats["error"]
            }

    except Exception as e:
        storage_info = {
            "backend": storage.__class__.__name__ if hasattr(storage, '__class__') else "unknown",
            "status": "error",
            "error": str(e)
        }
    
    # Performance metrics (basic for now)
    performance_info = {
        "uptime_seconds": time.time() - _startup_time,
        "uptime_formatted": format_uptime(time.time() - _startup_time)
    }
    
    # Extract statistics for separate field if available
    statistics = {
        "total_memories": storage_info.get("total_memories", 0),
        "unique_tags": storage_info.get("unique_tags", 0),
        "memories_this_week": storage_info.get("memories_this_week", 0),
        "database_size_mb": storage_info.get("database_size_mb", 0),
        "backend": storage_info.get("backend", "sqlite-vec")
    }
    
    return DetailedHealthResponse(
        status="healthy",
        version=__version__,
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=time.time() - _startup_time,
        storage=storage_info,
        system=system_info,
        performance=performance_info,
        statistics=statistics
    )


@router.get("/health/sync-status")
async def sync_status(
    storage: MemoryStorage = Depends(get_storage),
    user: AuthenticationResult = Depends(require_read_access) if OAUTH_ENABLED else None
):
    """Get current initial sync status for hybrid storage."""

    # Check if this is a hybrid storage that supports sync status
    if hasattr(storage, 'get_initial_sync_status'):
        sync_status = storage.get_initial_sync_status()
        return {
            "sync_supported": True,
            "status": sync_status
        }
    else:
        return {
            "sync_supported": False,
            "status": {
                "in_progress": False,
                "total": 0,
                "completed": 0,
                "finished": True,
                "progress_percentage": 100
            }
        }


def format_uptime(seconds: float) -> str:
    """Format uptime in human-readable format."""
    if seconds < 60:
        return f"{seconds:.1f} seconds"
    elif seconds < 3600:
        return f"{seconds/60:.1f} minutes"
    elif seconds < 86400:
        return f"{seconds/3600:.1f} hours"
    else:
        return f"{seconds/86400:.1f} days"