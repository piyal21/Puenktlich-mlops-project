from fastapi import APIRouter

from app.deps import Deps
from app.schemas import HealthOut

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthOut)
def health(deps: Deps) -> HealthOut:
    """Liveness plus the loaded model version and board age (never fails on missing data)."""
    return HealthOut(
        status="ok",
        model_version=deps.models.version_or_none(),
        board_generated_at=deps.board.generated_at_or_none(),
    )
