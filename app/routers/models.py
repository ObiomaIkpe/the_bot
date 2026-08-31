from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.models.model import Model
from app.models.user import User
from app.schemas.model import ModelOut

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=list[ModelOut])
def list_models(
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Every registered model -- not admin-gated, since every trader-
    facing dropdown (Trade History's model filter, etc.) needs this
    too, not just the admin pages. Adding a model is the admin-gated
    action (POST /admin/models); reading the list is not."""
    return db.query(Model).order_by(Model.created_at.asc()).all()
