from decimal import Decimal

from pydantic import BaseModel, Field


class MessageResponse(BaseModel):
    detail: str


class TopupResponse(BaseModel):
    detail: str
    balance: Decimal = Field(ge=0, examples=[Decimal("12.50")])


# Reusable OpenAPI error response declarations.
# Spread into the `responses=` parameter on route decorators:
#   @router.post("/foo", response_model=Foo, responses={**HTTP_404, **HTTP_409})
_ERR = {"model": MessageResponse}
HTTP_400 = {400: {**_ERR, "description": "Bad request"}}
HTTP_402 = {402: {**_ERR, "description": "Insufficient balance"}}
HTTP_403 = {403: {**_ERR, "description": "Forbidden"}}
HTTP_404 = {404: {**_ERR, "description": "Not found"}}
HTTP_409 = {409: {**_ERR, "description": "Conflict"}}
