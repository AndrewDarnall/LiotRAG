""" Pydantic Models for Requests """
from pydantic import BaseModel

class Item(BaseModel):
    name: str
    price: float
    in_stock: bool = True