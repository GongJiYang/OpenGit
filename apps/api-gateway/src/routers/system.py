import os

from fastapi import APIRouter, Depends, Request
from sqlmodel import Session

from agent_auth.deps import get_auth_session
from core.middleware import limiter
from core.security import STORE_ROOT
from dependencies.services import get_indexer
from schemas.system import MemoryStatusResponse, SystemStats

router = APIRouter()


@router.get("/api/v1/memory/status", response_model=MemoryStatusResponse)
def get_memory_status():
    """Expose whether persistent memory is configured and usable."""
    # TODO: expose memory status via facade; temporary static value
    return MemoryStatusResponse(enabled=False, backend="unknown")


@router.get("/stats", response_model=SystemStats)
@limiter.limit("30/minute")
def get_stats(request: Request, auth_session: Session = Depends(get_auth_session)):
    """Returns real-time system statistics (no mock values)."""
    repos = [d for d in os.listdir(STORE_ROOT) if not d.startswith('.')]

    # TODO: expose agent listing via facade; return empty until available
    active_agents = []

    total_vectors = 0
    idx = get_indexer(request)
    if idx and getattr(idx, "client", None):
        try:
            total_vectors = idx.client.count(
                collection_name=idx.collection_name,
                exact=True,
            ).count
        except Exception:
            total_vectors = 0

    system_load = "N/A"
    try:
        system_load = f"{os.getloadavg()[0]:.2f}"
    except Exception:
        pass

    return SystemStats(
        active_agents=len(active_agents),
        total_repos=len(repos),
        total_vectors=total_vectors,
        system_load=system_load,
    )


@router.get("/api/v1/system/routes", tags=["System"])
@router.get("/routes", tags=["System"])
async def list_all_routes(request: Request):
    """
    List all registered API routes.

    Useful for debugging and Agent CLI usage.
    """
    routes = []
    for route in request.app.routes:
        if hasattr(route, "methods") and route.methods:
            routes.append(
                {
                    "path": route.path,
                    "methods": list(route.methods),
                    "name": route.name,
                    "tags": list(route.tags) if hasattr(route, "tags") and route.tags else [],
                }
            )
    # Sort by path for easier reading
    routes.sort(key=lambda r: r["path"])
    return {"total": len(routes), "routes": routes}
