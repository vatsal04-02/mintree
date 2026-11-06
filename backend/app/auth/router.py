from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.administration.models import SessionRecord, User
from app.audit import service as audit
from app.auth.schemas import ChangePasswordIn, LoginIn, MeOut
from app.core.config import get_settings
from app.core.db import get_db
from app.core.deps import CurrentUser, current_user_allow_pw_change
from app.core.errors import Unprocessable
from app.core.security import hash_password, hash_token, new_token, validate_password_strength, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# Verified against when the email is unknown so response time doesn't reveal valid accounts.
_DUMMY_HASH = hash_password("not-a-real-password-0Aa")


def _me(cu: CurrentUser) -> MeOut:
    return MeOut(
        id=cu.user.id,
        email=cu.user.email,
        full_name=cu.user.full_name,
        department_name=cu.user.department_name,
        roles=sorted(r.name for r in cu.user.roles),
        permissions=sorted(cu.permissions),
        must_change_password=cu.user.must_change_password,
        csrf_token=cu.session.csrf_token,
    )


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(
        s.session_cookie_name, token, httponly=True, secure=s.cookie_secure, samesite=s.cookie_samesite,
        max_age=s.session_ttl_hours * 3600, path="/",
    )


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db, scope="function")):
    s = get_settings()
    now = datetime.now(timezone.utc)
    email = body.email.lower()
    user = db.scalar(select(User).where(User.email == email))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")

    if not user:
        verify_password(body.password, _DUMMY_HASH)
        audit.record(db, user=None, user_email=email, action="login_failed", entity="user")
        db.commit()
        raise generic
    if user.locked_until and user.locked_until > now:
        audit.record(db, user=user, action="login_blocked_locked", entity="user", entity_id=user.id)
        db.commit()
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked. Try again later.")
    if not user.is_active or not verify_password(body.password, user.password_hash):
        if user.is_active:
            user.failed_login_count += 1
            if user.failed_login_count >= s.max_failed_logins:
                user.locked_until = now + timedelta(minutes=s.lockout_minutes)
                user.failed_login_count = 0
        audit.record(db, user=user, action="login_failed", entity="user", entity_id=user.id)
        db.commit()  # persist the counter even though we are returning an error
        raise generic

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = now
    token = new_token()
    sess = SessionRecord(
        token_hash=hash_token(token), csrf_token=new_token(), user_id=user.id,
        expires_at=now + timedelta(hours=s.session_ttl_hours),
        ip=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:255],
        last_seen_at=now,
    )
    db.add(sess)
    audit.record(db, user=user, action="login", entity="user", entity_id=user.id)
    db.flush()
    _set_cookie(response, token)
    perms = {p.code for role in user.roles for p in role.permissions}
    return _me(CurrentUser(user=user, session=sess, permissions=perms))


@router.get("/me", response_model=MeOut)
def me(cu: CurrentUser = Depends(current_user_allow_pw_change)):
    return _me(cu)


@router.post("/logout", status_code=204)
def logout(response: Response, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user_allow_pw_change)):
    cu.session.revoked_at = datetime.now(timezone.utc)
    audit.record(db, user=cu.user, action="logout", entity="user", entity_id=cu.user.id)
    response.delete_cookie(get_settings().session_cookie_name, path="/")


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordIn, db: Session = Depends(get_db, scope="function"), cu: CurrentUser = Depends(current_user_allow_pw_change)
):
    if not verify_password(body.current_password, cu.user.password_hash):
        raise Unprocessable("Current password is incorrect")
    if err := validate_password_strength(body.new_password):
        raise Unprocessable(err)
    if body.new_password == body.current_password:
        raise Unprocessable("New password must differ from the current one")
    cu.user.password_hash = hash_password(body.new_password)
    cu.user.must_change_password = False
    # Revoke every other session for this user.
    now = datetime.now(timezone.utc)
    for other in db.scalars(select(SessionRecord).where(
        SessionRecord.user_id == cu.user.id, SessionRecord.revoked_at.is_(None), SessionRecord.id != cu.session.id
    )):
        other.revoked_at = now
    audit.record(db, user=cu.user, action="password_changed", entity="user", entity_id=cu.user.id)
