from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.model_config import ModelConfig
from app.models.user import User
from app.schemas.model_configs import ModelConfigOut

router = APIRouter(prefix="/model-configs", tags=["model-configs"])


@router.get("", response_model=list[ModelConfigOut])
def list_model_configs(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return (
        db.query(ModelConfig)
        .filter(ModelConfig.user_id == current_user.user_id)
        .order_by(ModelConfig.model_name)
        .all()
    )
