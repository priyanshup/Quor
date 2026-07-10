/**
 * Orders service — order creation, fulfillment state transitions, and
 * cancellation/refund handling for the storefront backend.
 */

import { PaymentGateway } from "./payment-gateway";
import { InventoryClient } from "./inventory-client";
import { Logger } from "./logger";

export type OrderStatus =
  | "pending"
  | "paid"
  | "fulfilling"
  | "shipped"
  | "delivered"
  | "cancelled"
  | "refunded";

export interface OrderLineItem {
  sku: string;
  quantity: number;
  unitPriceCents: number;
}

export interface Order {
  id: string;
  customerId: string;
  status: OrderStatus;
  items: OrderLineItem[];
  totalCents: number;
  createdAt: Date;
}

export interface CreateOrderInput {
  customerId: string;
  items: OrderLineItem[];
  paymentMethodToken: string;
}

export class OrderNotFoundError extends Error {
  constructor(orderId: string) {
    super(`order not found: ${orderId}`);
  }
}

export class InvalidTransitionError extends Error {
  constructor(from: OrderStatus, to: OrderStatus) {
    super(`cannot transition order from ${from} to ${to}`);
  }
}

const VALID_TRANSITIONS: Record<OrderStatus, OrderStatus[]> = {
  pending: ["paid", "cancelled"],
  paid: ["fulfilling", "cancelled", "refunded"],
  fulfilling: ["shipped", "cancelled"],
  shipped: ["delivered", "refunded"],
  delivered: ["refunded"],
  cancelled: [],
  refunded: [],
};

export class OrdersService {
  constructor(
    private readonly store: OrderStore,
    private readonly gateway: PaymentGateway,
    private readonly inventory: InventoryClient,
    private readonly logger: Logger,
  ) {}

  async createOrder(input: CreateOrderInput): Promise<Order> {
    this.logger.info(`creating order for customer ${input.customerId}`);
    await this.inventory.reserve(input.items);
    const totalCents = this.calculateTotal(input.items);
    const order: Order = {
      id: generateOrderId(),
      customerId: input.customerId,
      status: "pending",
      items: input.items,
      totalCents,
      createdAt: new Date(),
    };
    await this.store.save(order);
    return order;
  }

  calculateTotal(items: OrderLineItem[]): number {
    return items.reduce((sum, item) => sum + item.quantity * item.unitPriceCents, 0);
  }

  async capturePayment(orderId: string, paymentMethodToken: string): Promise<Order> {
    const order = await this.getOrderOrThrow(orderId);
    const result = await this.gateway.charge(order.totalCents, paymentMethodToken);
    if (!result.success) {
      throw new Error(`payment capture failed: ${result.declineReason}`);
    }
    return this.transition(order, "paid");
  }

  async beginFulfillment(orderId: string): Promise<Order> {
    const order = await this.getOrderOrThrow(orderId);
    return this.transition(order, "fulfilling");
  }

  async markShipped(orderId: string, trackingNumber: string): Promise<Order> {
    const order = await this.getOrderOrThrow(orderId);
    const updated = await this.transition(order, "shipped");
    await this.store.attachTracking(orderId, trackingNumber);
    return updated;
  }

  async markDelivered(orderId: string): Promise<Order> {
    const order = await this.getOrderOrThrow(orderId);
    return this.transition(order, "delivered");
  }

  async cancelOrder(orderId: string, reason: string): Promise<Order> {
    const order = await this.getOrderOrThrow(orderId);
    if (order.status === "paid" || order.status === "fulfilling") {
      await this.inventory.release(order.items);
    }
    this.logger.info(`cancelling order ${orderId}: ${reason}`);
    return this.transition(order, "cancelled");
  }

  async refundOrder(orderId: string): Promise<Order> {
    const order = await this.getOrderOrThrow(orderId);
    await this.gateway.refund(order.totalCents);
    return this.transition(order, "refunded");
  }

  private async transition(order: Order, next: OrderStatus): Promise<Order> {
    const allowed = VALID_TRANSITIONS[order.status];
    if (!allowed.includes(next)) {
      throw new InvalidTransitionError(order.status, next);
    }
    const updated: Order = { ...order, status: next };
    await this.store.save(updated);
    return updated;
  }

  private async getOrderOrThrow(orderId: string): Promise<Order> {
    const order = await this.store.findById(orderId);
    if (!order) {
      throw new OrderNotFoundError(orderId);
    }
    return order;
  }

  async listOrdersForCustomer(customerId: string, limit: number = 20): Promise<Order[]> {
    return this.store.findByCustomer(customerId, limit);
  }
}

export interface OrderStore {
  save(order: Order): Promise<void>;
  findById(orderId: string): Promise<Order | null>;
  findByCustomer(customerId: string, limit: number): Promise<Order[]>;
  attachTracking(orderId: string, trackingNumber: string): Promise<void>;
}

function generateOrderId(): string {
  return `ord_${Math.random().toString(36).slice(2, 12)}`;
}
