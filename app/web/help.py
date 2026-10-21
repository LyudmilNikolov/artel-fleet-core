from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.web.deps import get_web_user

router = APIRouter(tags=["web-help"])


@router.get("/help", response_class=HTMLResponse)
async def help_page(request: Request, db: AsyncSession = Depends(get_db)):
    user = await get_web_user(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=302)

    return request.app.state.templates.TemplateResponse(
        request=request,
        name="help/index.html",
        context={
            "request": request,
            "user": user,
            "active_page": "help",
            **request.app.state.template_globals(request),
        },
    )
