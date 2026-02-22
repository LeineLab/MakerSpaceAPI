from app.database import get_db

# Re-export for convenience so routers can import from one place
__all__ = ["get_db"]
