from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from persistence import CommitRecord, get_session

router = APIRouter()


@router.get("/api/v1/stats/leaderboard")
def get_leaderboard(session: Session = Depends(get_session)):
    """[Blind-Spot 5] Leaderboard based on success rate."""
    records = session.exec(select(CommitRecord)).all()
    stats = {}
    for r in records:
        if r.agent_id not in stats:
            stats[r.agent_id] = {"total": 0, "success": 0}
        stats[r.agent_id]["total"] += 1
        if r.status == "approved":
            stats[r.agent_id]["success"] += 1

    leaderboard = []
    for aid, data in stats.items():
        rate = (data["success"] / data["total"]) * 100
        leaderboard.append(
            {
                "agent_id": aid,
                "success_rate": f"{rate:.1f}%",
                "total_commits": data["total"],
                "rank": "Gold 🦞" if rate > 90 else "Silver 🦞",
            }
        )

    return sorted(leaderboard, key=lambda x: float(x["success_rate"].replace('%', '')), reverse=True)
