/**
 * ShoppingCart — renders the current cart contents, lets the user adjust
 * quantities, and shows a running total. Fetches cart state from the
 * cart API on mount and re-fetches after any mutation.
 */

import { useState, useEffect, useCallback } from "react";
import { formatCurrency } from "./currency-utils";

export interface CartLineItemProps {
  sku: string;
  name: string;
  quantity: number;
  unitPriceCents: number;
  onQuantityChange: (sku: string, quantity: number) => void;
  onRemove: (sku: string) => void;
}

function CartLineItem({
  sku,
  name,
  quantity,
  unitPriceCents,
  onQuantityChange,
  onRemove,
}: CartLineItemProps) {
  return (
    <tr className="cart-line-item">
      <td>{name}</td>
      <td>
        <input
          type="number"
          min={1}
          value={quantity}
          onChange={(e) => onQuantityChange(sku, Number(e.target.value))}
        />
      </td>
      <td>{formatCurrency(unitPriceCents * quantity)}</td>
      <td>
        <button onClick={() => onRemove(sku)}>Remove</button>
      </td>
    </tr>
  );
}

export interface CartItem {
  sku: string;
  name: string;
  quantity: number;
  unitPriceCents: number;
}

interface ShoppingCartProps {
  customerId: string;
}

export function ShoppingCart({ customerId }: ShoppingCartProps) {
  const [items, setItems] = useState<CartItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadCart = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(`/api/cart?customerId=${customerId}`);
      if (!response.ok) {
        throw new Error(`failed to load cart: ${response.status}`);
      }
      const data: CartItem[] = await response.json();
      setItems(data);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "unknown error");
    } finally {
      setLoading(false);
    }
  }, [customerId]);

  useEffect(() => {
    loadCart();
  }, [loadCart]);

  const handleQuantityChange = async (sku: string, quantity: number) => {
    await fetch(`/api/cart/${sku}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ quantity }),
    });
    await loadCart();
  };

  const handleRemove = async (sku: string) => {
    await fetch(`/api/cart/${sku}`, { method: "DELETE" });
    await loadCart();
  };

  const total = items.reduce((sum, item) => sum + item.quantity * item.unitPriceCents, 0);

  if (loading) {
    return <p>Loading cart...</p>;
  }

  if (error) {
    return <p className="cart-error">Could not load cart: {error}</p>;
  }

  if (items.length === 0) {
    return <p>Your cart is empty.</p>;
  }

  return (
    <div className="shopping-cart">
      <table>
        <thead>
          <tr>
            <th>Item</th>
            <th>Quantity</th>
            <th>Subtotal</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <CartLineItem
              key={item.sku}
              sku={item.sku}
              name={item.name}
              quantity={item.quantity}
              unitPriceCents={item.unitPriceCents}
              onQuantityChange={handleQuantityChange}
              onRemove={handleRemove}
            />
          ))}
        </tbody>
      </table>
      <div className="cart-total">Total: {formatCurrency(total)}</div>
    </div>
  );
}
