"""
Handlers package for Shohruh Matematika Bot & Mini App.
"""
from .user import user_router
from .admin import admin_router
from .webapp import create_web_app

__all__ = ["user_router", "admin_router", "create_web_app"]
