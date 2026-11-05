# Meridian payments
A synthetic payment platform used for repeatable exploration. It contains no real transactions.

## Refund policy
Customers may request a full refund within 30 days of payment. A refund must reference a captured payment and include a reason.

## Payment lifecycle
A customer initiates a payment. Payment records preserve the currency and amount. A refund refers to exactly one payment.

## Known inconsistency
The implementation currently permits refunds within 14 days. This differs from the 30-day policy above. Neither statement should be approved as a business constraint without a human decision.
