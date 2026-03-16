from fastapi import HTTPException, Request, Depends
from sqlalchemy.orm import Session
from database import get_db
from models import User

def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("user_id")
    if not uid:
        # 303/302 с заголовком Location — корректный способ редиректа из dependency
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    user = db.get(User, uid)
    if not user:
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    # Sale Manager не имеет доступа к обычным страницам — только к /sales/
    if user.role == "sale_manager":
        path = str(request.url.path)
        if not path.startswith("/sales") and path not in ("/logout",):
            raise HTTPException(status_code=302, headers={"Location": "/sales/"})
    return user
