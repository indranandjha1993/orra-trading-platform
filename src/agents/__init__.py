from src.agents.auth_service import app as auth_app
from src.agents.notification_service import app as notification_app
from src.agents.ticker_service import app as ticker_app

__all__ = ["auth_app", "notification_app", "ticker_app"]
