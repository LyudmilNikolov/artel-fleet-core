from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.compliance import router as compliance_router
from app.api.v1.documents import router as documents_router
from app.api.v1.driver_documents import router as driver_documents_router
from app.api.v1.drivers import router as drivers_router
from app.api.v1.expenses import router as expenses_router
from app.api.v1.maintenance import router as maintenance_router
from app.api.v1.mileage import router as mileage_router
from app.api.v1.reports import router as reports_router
from app.api.v1.users import router as users_router
from app.api.v1.vehicles import router as vehicles_router
from app.config import settings

api_router = APIRouter()

api_router.include_router(auth_router)
api_router.include_router(users_router)
api_router.include_router(vehicles_router)
api_router.include_router(drivers_router)
api_router.include_router(driver_documents_router)
api_router.include_router(mileage_router)
api_router.include_router(maintenance_router)
api_router.include_router(expenses_router)
api_router.include_router(compliance_router)
api_router.include_router(documents_router)
api_router.include_router(reports_router)


@api_router.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": settings.APP_VERSION}
