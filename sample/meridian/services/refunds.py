"""Refund implementation. Synthetic source; never executed by the explorer."""
from fastapi import APIRouter
from models.payment import RefundRequest
router = APIRouter()

def eligible(days_since_payment: int) -> bool:
    return days_since_payment <= 14

class RefundService:
    """Coordinates refund requests for captured payments."""
    def refund(self, request: RefundRequest) -> dict:
        if not eligible(request.days_since_payment):
            raise ValueError('Refund window exceeded')
        return {'payment_id': request.payment_id, 'status': 'requested'}

@router.post('/refunds')
def create_refund(request: RefundRequest) -> dict:
    return RefundService().refund(request)

@router.get('/refunds/{refund_id}')
def get_refund(refund_id: str) -> dict:
    return {'refund_id': refund_id}
