//! Shopping cart service — item management, discount codes, and checkout
//! total calculation for the storefront backend.

use std::collections::HashMap;

/// Caps how many units of a single SKU may be added to a cart.
pub const MAX_QUANTITY_PER_ITEM: u32 = 20;

/// The subtotal, in cents, at or above which shipping is free.
pub const FREE_SHIPPING_THRESHOLD_CENTS: u64 = 5000;

/// One SKU and its quantity/unit price in a cart.
#[derive(Debug, Clone)]
pub struct LineItem {
    pub sku: String,
    pub quantity: u32,
    pub unit_price_cents: u64,
}

/// Errors that can occur while mutating a cart.
#[derive(Debug)]
pub enum CartError {
    QuantityExceeded(u32),
    ItemNotFound(String),
}

/// Manages the line items for a single customer's cart.
pub struct CartService {
    items: HashMap<String, LineItem>,
}

impl CartService {
    /// Construct an empty cart.
    pub fn new() -> Self {
        CartService {
            items: HashMap::new(),
        }
    }

    /// Add `quantity` units of `sku` to the cart, merging with any existing
    /// quantity for the same SKU.
    pub fn add_item(&mut self, sku: &str, quantity: u32, unit_price_cents: u64) -> Result<(), CartError> {
        if quantity > MAX_QUANTITY_PER_ITEM {
            return Err(CartError::QuantityExceeded(quantity));
        }
        let next_quantity = match self.items.get(sku) {
            Some(existing) => existing.quantity + quantity,
            None => quantity,
        };
        self.items.insert(
            sku.to_string(),
            LineItem {
                sku: sku.to_string(),
                quantity: next_quantity,
                unit_price_cents,
            },
        );
        Ok(())
    }

    /// Remove `sku` from the cart entirely.
    pub fn remove_item(&mut self, sku: &str) -> Result<(), CartError> {
        match self.items.remove(sku) {
            Some(_) => Ok(()),
            None => Err(CartError::ItemNotFound(sku.to_string())),
        }
    }

    /// Return the sum of quantity times unit price across all items.
    pub fn subtotal_cents(&self) -> u64 {
        self.items
            .values()
            .map(|item| item.quantity as u64 * item.unit_price_cents)
            .sum()
    }

    /// Return the shipping cost in cents, zero once the subtotal reaches
    /// FREE_SHIPPING_THRESHOLD_CENTS.
    pub fn shipping_cents(&self) -> u64 {
        if self.subtotal_cents() >= FREE_SHIPPING_THRESHOLD_CENTS {
            0
        } else {
            599
        }
    }
}
