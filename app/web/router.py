from fastapi import APIRouter, Depends

from app.web.auth import router as auth_router
from app.web.csrf import csrf_protect
from app.web.compliance import router as compliance_router
from app.web.dashboard import router as dashboard_router
from app.web.documents import router as documents_router
from app.web.driver_documents import router as driver_documents_router
from app.web.drivers import router as drivers_router
from app.web.expenses import router as expenses_router
from app.web.maintenance import router as maintenance_router
from app.web.notification_settings import router as notification_settings_router
from app.web.notifications import router as notifications_router
from app.web.reports import router as reports_router
from app.web.settings import router as settings_router
from app.web.help import router as help_router
from app.web.vehicles import router as vehicles_router

web_router = APIRouter(dependencies=[Depends(csrf_protect)])

web_router.include_router(auth_router)
web_router.include_router(dashboard_router)
web_router.include_router(vehicles_router)
web_router.include_router(drivers_router)
web_router.include_router(driver_documents_router)
web_router.include_router(maintenance_router)
web_router.include_router(expenses_router)
web_router.include_router(compliance_router)
web_router.include_router(reports_router)
web_router.include_router(notifications_router)
web_router.include_router(notification_settings_router)
web_router.include_router(settings_router)
web_router.include_router(documents_router)
web_router.include_router(help_router)
