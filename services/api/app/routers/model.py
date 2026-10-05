from fastapi import APIRouter

from app.deps import Deps
from app.schemas import ModelOut, model_out

router = APIRouter(tags=["model"])


@router.get("/model", response_model=ModelOut)
def model_info(deps: Deps) -> ModelOut:
    """Champion metadata: version, training window and test metrics."""
    bundle = deps.models.get()
    state = deps.models.pointer_state()
    return model_out(bundle, None if state is None else state.previous_version)
