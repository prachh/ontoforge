CREATE TABLE customers (
  id VARCHAR(64) PRIMARY KEY,
  email VARCHAR(255) NOT NULL
);
CREATE TABLE payments (
  id VARCHAR(64) PRIMARY KEY,
  customer_id VARCHAR(64) REFERENCES customers(id),
  amount DECIMAL(12,2) NOT NULL,
  currency VARCHAR(3) NOT NULL
);
CREATE TABLE refunds (
  id VARCHAR(64) PRIMARY KEY,
  payment_id VARCHAR(64) REFERENCES payments(id),
  reason TEXT,
  status VARCHAR(32) NOT NULL
);
