from src.api.routes.account import router as account_router
from src.api.routes.admin import router as admin_router
from src.api.routes.billing import router as billing_router
from src.api.routes.connections import router as connections_router
from src.api.routes.profiles import router as profiles_router
from src.api.routes.webhooks import router as webhooks_router

__all__ = [
    "account_router",
    "admin_router",
    "billing_router",
    "connections_router",
    "profiles_router",
    "webhooks_router",
]
