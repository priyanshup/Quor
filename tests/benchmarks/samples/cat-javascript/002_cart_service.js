/**
 * Shopping cart service — item management, discount codes, and checkout
 * total calculation for the storefront frontend.
 */

import { fetchDiscountCode } from "./discounts.js";
import { logger } from "./logger.js";

const MAX_QUANTITY_PER_ITEM = 20;
const FREE_SHIPPING_THRESHOLD_CENTS = 5000;

export class CartService {
  constructor(storage) {
    this.storage = storage;
    this.items = new Map();
  }

  addItem(sku, quantity, unitPriceCents) {
    if (quantity > MAX_QUANTITY_PER_ITEM) {
      throw new Error(`quantity ${quantity} exceeds per-item limit`);
    }
    const existing = this.items.get(sku);
    const nextQuantity = existing ? existing.quantity + quantity : quantity;
    this.items.set(sku, { sku, quantity: nextQuantity, unitPriceCents });
    logger.info("cart: added %s x%s", sku, quantity);
    this.storage.persist(this.items);
  }

  removeItem(sku) {
    this.items.delete(sku);
    this.storage.persist(this.items);
  }

  updateQuantity(sku, quantity) {
    const existing = this.items.get(sku);
    if (!existing) {
      throw new Error(`cannot update missing sku ${sku}`);
    }
    existing.quantity = quantity;
    this.storage.persist(this.items);
  }

  subtotalCents() {
    let total = 0;
    for (const item of this.items.values()) {
      total += item.quantity * item.unitPriceCents;
    }
    return total;
  }

  async applyDiscountCode(code) {
    const discount = await fetchDiscountCode(code);
    if (!discount || !discount.active) {
      throw new Error("invalid or expired discount code");
    }
    return Math.round(this.subtotalCents() * (1 - discount.percentOff / 100));
  }

  shippingCents() {
    return this.subtotalCents() >= FREE_SHIPPING_THRESHOLD_CENTS ? 0 : 599;
  }

  checkoutSummary() {
    const subtotal = this.subtotalCents();
    const shipping = this.shippingCents();
    return {
      itemCount: this.items.size,
      subtotalCents: subtotal,
      shippingCents: shipping,
      totalCents: subtotal + shipping,
    };
  }

  clear() {
    this.items.clear();
    this.storage.persist(this.items);
  }
}

export function formatCents(cents) {
  return `$${(cents / 100).toFixed(2)}`;
}
