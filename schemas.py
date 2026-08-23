# schemas.py
from pydantic import BaseModel
from typing import List, Optional

# Tool 1: Get Customer and Accounts
class CustomerAccountsInput(BaseModel):
    customer_id: str

class TransactionModel(BaseModel):
    id: Optional[str] = None
    amount: Optional[float] = None
    timestamp: Optional[str] = None

class AccountModel(BaseModel):
    id: str = None
    name: str = None
    transactions: List[TransactionModel] = []

class CustomerModel(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = None
    on_watchlist: Optional[bool] = False
    is_pep: Optional[bool] = False

class CustomerAccountsOutput(BaseModel):
    customer: CustomerModel
    accounts: List[AccountModel]

# Tool 2: Identify watchlisted customers in suspicious rings
from typing import Dict, Any

class RingModel(BaseModel):
    ring_path: List[Dict[str, Any]]  # List of node dicts
    watched_customers: List[Dict[str, Any]]  # List of customer dicts
    watch_relationships: List[Dict[str, Any]]  # List of relationship dicts

class CustomerRingsInput(BaseModel):
    max_number_rings: int = 10
    customer_in_watchlist: Optional[bool] = True
    customer_is_pep: Optional[bool] = False

class CustomerRingsOutput(BaseModel):
    customer_rings: List[RingModel]


class GenerateCypherRequest(BaseModel):
    question: str
    database_schema: str
