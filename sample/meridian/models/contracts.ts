export interface Customer {
  id: string;
  email: string;
}
export interface Payment {
  id: string;
  customerId: string;
  amount: number;
  currency: string;
}
export interface RefundRequest {
  paymentId: string;
  reason: string;
}
export function formatPayment(payment: Payment): string {
  return `${payment.currency} ${payment.amount}`;
}
