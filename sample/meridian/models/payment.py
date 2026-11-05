from pydantic import BaseModel

class RefundRequest(BaseModel):
    payment_id: str
    days_since_payment: int
    reason: str

class Payment(BaseModel):
    id: str
    customer_id: str
    amount: float
    currency: str
