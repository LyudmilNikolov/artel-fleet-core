from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.web.deps import driver_scope_of, get_web_user

router = APIRouter(tags=["web-help"])


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    # A driver only gets the guide for what a driver can do; the full guide is for the other roles.
    return request.app.state.templates.TemplateResponse(
        request=request,
        name="help/driver.html" if driver_scope_of(request).restricted else "help/index.html",
        context={
            "request": request,
            "user": user,
            "active_page": "help",
            "settings_expiring_days": settings.COMPLIANCE_EXPIRING_SOON_DAYS,
            **request.app.state.template_globals(request),
        },
    )
