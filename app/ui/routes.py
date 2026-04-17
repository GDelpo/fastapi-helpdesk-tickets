"""UI routes — thin re-export of admin and portal routers."""
from app.ui.admin import router
from app.ui.portal import portal_router

__all__ = ["router", "portal_router"]
