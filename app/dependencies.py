"""Shared FastAPI dependencies: DB session, auth."""
import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_session

_security = HTTPBasic()


def require_basic_auth(
    credentials: Annotated[HTTPBasicCredentials, Depends(_security)],
) -> str:
    """Validate HTTP Basic Auth against .env credentials. Returns username on success."""
    correct_user = secrets.compare_digest(
        credentials.username.encode("utf-8"),
        settings.basic_auth_user.encode("utf-8"),
    )
    correct_pass = secrets.compare_digest(
        credentials.password.encode("utf-8"),
        settings.basic_auth_pass.encode("utf-8"),
    )
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username


SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthUser = Annotated[str, Depends(require_basic_auth)]
