import bcrypt
from fastapi import Request
from fastapi.responses import RedirectResponse


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()


def check_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def current_workspace_id(request: Request):
    return request.session.get("workspace_id")


class LoginRequired(Exception):
    pass


def require_login(request: Request) -> int:
    wid = current_workspace_id(request)
    if not wid:
        raise LoginRequired()
    return wid


def login_redirect():
    return RedirectResponse("/login", status_code=303)
