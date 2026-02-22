from fastapi import APIRouter

from app.api.v1 import bankomat, machines, products, rentals, sessions, transactions, users

api_router = APIRouter()

api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(machines.router, prefix="/machines", tags=["machines"])
api_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
api_router.include_router(products.router, tags=["products"])
api_router.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
api_router.include_router(bankomat.router, prefix="/bankomat", tags=["bankomat"])
api_router.include_router(rentals.router, prefix="/rentals", tags=["rentals"])
