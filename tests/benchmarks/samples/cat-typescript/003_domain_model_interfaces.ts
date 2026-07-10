/**
 * Domain model types shared across the storefront services — order,
 * customer, address, and payment-method shapes. Almost entirely
 * declarations; the few functions here are pure type-guards.
 */

export interface Address {
  line1: string;
  line2?: string;
  city: string;
  state: string;
  postalCode: string;
  country: string;
}

export interface Customer {
  id: string;
  email: string;
  fullName: string;
  billingAddress: Address;
  shippingAddresses: Address[];
  createdAt: Date;
}

export interface Money {
  cents: number;
  currency: string;
}

export type PaymentMethodType = "card" | "bank_transfer" | "wallet";

export interface CardPaymentMethod {
  type: "card";
  last4: string;
  brand: string;
  expiryMonth: number;
  expiryYear: number;
}

export interface BankTransferPaymentMethod {
  type: "bank_transfer";
  bankName: string;
  accountLast4: string;
}

export interface WalletPaymentMethod {
  type: "wallet";
  provider: "apple_pay" | "google_pay" | "paypal";
}

export type PaymentMethod = CardPaymentMethod | BankTransferPaymentMethod | WalletPaymentMethod;

export interface LineItem {
  sku: string;
  productName: string;
  quantity: number;
  unitPrice: Money;
}

export interface ShippingOption {
  id: string;
  label: string;
  cost: Money;
  estimatedDays: number;
}

export interface Discount {
  code: string;
  percentOff?: number;
  amountOffCents?: number;
  expiresAt?: Date;
}

export interface CheckoutSession {
  id: string;
  customer: Customer;
  items: LineItem[];
  shippingOption: ShippingOption | null;
  appliedDiscount: Discount | null;
  paymentMethod: PaymentMethod | null;
}

export function isCardPayment(method: PaymentMethod): method is CardPaymentMethod {
  return method.type === "card";
}

export function isExpiredDiscount(discount: Discount): boolean {
  return discount.expiresAt !== undefined && discount.expiresAt.getTime() < Date.now();
}
