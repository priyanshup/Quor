// Package cart implements shopping cart item management, discount
// application, and checkout total calculation for the storefront backend.
package cart

import (
	"errors"
	"fmt"
)

// MaxQuantityPerItem caps how many units of a single SKU may be added to a cart.
const MaxQuantityPerItem = 20

// FreeShippingThresholdCents is the subtotal, in cents, at or above which
// shipping is free.
const FreeShippingThresholdCents = 5000

// ErrQuantityExceeded is returned when a requested quantity exceeds
// MaxQuantityPerItem.
var ErrQuantityExceeded = errors.New("quantity exceeds per-item limit")

// LineItem represents one SKU and its quantity/unit price in a cart.
type LineItem struct {
	SKU            string
	Quantity       int
	UnitPriceCents int
}

// Storage persists a cart's line items.
type Storage interface {
	Persist(items map[string]LineItem) error
}

// CartService manages the line items for a single customer's cart.
type CartService struct {
	storage Storage
	items   map[string]LineItem
}

// NewCartService constructs a CartService backed by the given storage.
func NewCartService(storage Storage) *CartService {
	return &CartService{storage: storage, items: make(map[string]LineItem)}
}

// AddItem adds quantity units of sku to the cart, merging with any existing
// quantity for the same SKU.
func (c *CartService) AddItem(sku string, quantity int, unitPriceCents int) error {
	if quantity > MaxQuantityPerItem {
		return fmt.Errorf("%w: %d", ErrQuantityExceeded, quantity)
	}
	existing, ok := c.items[sku]
	next := quantity
	if ok {
		next += existing.Quantity
	}
	c.items[sku] = LineItem{SKU: sku, Quantity: next, UnitPriceCents: unitPriceCents}
	return c.storage.Persist(c.items)
}

// RemoveItem removes sku from the cart entirely.
func (c *CartService) RemoveItem(sku string) error {
	delete(c.items, sku)
	return c.storage.Persist(c.items)
}

// SubtotalCents returns the sum of quantity times unit price across all
// items currently in the cart.
func (c *CartService) SubtotalCents() int {
	total := 0
	for _, item := range c.items {
		total += item.Quantity * item.UnitPriceCents
	}
	return total
}

// ShippingCents returns the shipping cost in cents, which is zero once the
// subtotal reaches FreeShippingThresholdCents.
func (c *CartService) ShippingCents() int {
	if c.SubtotalCents() >= FreeShippingThresholdCents {
		return 0
	}
	return 599
}
