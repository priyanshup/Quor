// Package payments handles charge, refund, and duplicate-charge detection
// for the storefront checkout flow.
package payments

import (
	"errors"
	"fmt"
	"log"
)

// DefaultCurrency is the currency code used when none is specified.
const DefaultCurrency = "USD"

// ErrDuplicateCharge indicates a charge was attempted twice for the same
// account within the dedupe window.
var ErrDuplicateCharge = errors.New("duplicate pending charge")

// ChargeResult holds the outcome of a successful gateway charge.
type ChargeResult struct {
	ChargeID string
	Amount   int
}

// Gateway abstracts the upstream payment provider.
type Gateway interface {
	Charge(accountID string, amount int) (ChargeResult, error)
	Refund(chargeID string) error
}

// Ledger tracks pending and settled charges per account.
type Ledger interface {
	HasPendingCharge(accountID string, amount int) bool
	MarkSettled(accountID string, chargeID string)
	MarkRefunded(chargeID string)
}

// Processor coordinates charge, refund, and duplicate-detection logic.
type Processor struct {
	gateway Gateway
	ledger  Ledger
}

// NewProcessor constructs a Processor backed by the given gateway and ledger.
func NewProcessor(gateway Gateway, ledger Ledger) *Processor {
	return &Processor{gateway: gateway, ledger: ledger}
}

// Charge charges an account, guarding against duplicate pending charges.
func (p *Processor) Charge(accountID string, amount int) (string, error) {
	if p.ledger.HasPendingCharge(accountID, amount) {
		return "", fmt.Errorf("%w: account %s", ErrDuplicateCharge, accountID)
	}
	log.Printf("charging account %s for %d", accountID, amount)
	result, err := p.gateway.Charge(accountID, amount)
	if err != nil {
		return "", err
	}
	p.ledger.MarkSettled(accountID, result.ChargeID)
	return result.ChargeID, nil
}

// Refund refunds a previously settled charge.
func (p *Processor) Refund(chargeID string) error {
	log.Printf("refunding charge %s", chargeID)
	if err := p.gateway.Refund(chargeID); err != nil {
		return err
	}
	p.ledger.MarkRefunded(chargeID)
	return nil
}

// validateAmount is a package-level helper implemented as a func literal,
// used by callers that need to check an amount before charging.
var validateAmount = func(amount int) error {
	if amount <= 0 {
		return errors.New("amount must be positive")
	}
	return nil
}
