//! Payment processing orchestration — charge, refund, and duplicate-charge
//! detection for the storefront checkout flow.

use std::fmt;

/// The currency code used when none is specified.
pub const DEFAULT_CURRENCY: &str = "USD";

/// Errors that can occur while charging or refunding an account.
#[derive(Debug)]
pub enum PaymentError {
    DuplicateCharge(String),
    GatewayError(String),
}

impl fmt::Display for PaymentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PaymentError::DuplicateCharge(account_id) => {
                write!(f, "duplicate pending charge for account {}", account_id)
            }
            PaymentError::GatewayError(message) => write!(f, "gateway error: {}", message),
        }
    }
}

/// Abstracts the upstream payment provider.
pub trait Gateway {
    fn charge(&self, account_id: &str, amount: u64) -> Result<String, PaymentError>;
    fn refund(&self, charge_id: &str) -> Result<(), PaymentError>;
}

/// Tracks pending and settled charges per account.
pub trait Ledger {
    fn has_pending_charge(&self, account_id: &str, amount: u64) -> bool;
    fn mark_settled(&mut self, account_id: &str, charge_id: &str);
    fn mark_refunded(&mut self, charge_id: &str);
}

/// Coordinates charge, refund, and duplicate-detection logic.
pub struct Processor<G: Gateway, L: Ledger> {
    gateway: G,
    ledger: L,
}

impl<G: Gateway, L: Ledger> Processor<G, L> {
    /// Construct a Processor backed by the given gateway and ledger.
    pub fn new(gateway: G, ledger: L) -> Self {
        Processor { gateway, ledger }
    }

    /// Charge an account, guarding against duplicate pending charges.
    pub fn charge(&mut self, account_id: &str, amount: u64) -> Result<String, PaymentError> {
        if self.ledger.has_pending_charge(account_id, amount) {
            return Err(PaymentError::DuplicateCharge(account_id.to_string()));
        }
        println!("charging account {} for {}", account_id, amount);
        let charge_id = self.gateway.charge(account_id, amount)?;
        self.ledger.mark_settled(account_id, &charge_id);
        Ok(charge_id)
    }

    /// Refund a previously settled charge.
    pub fn refund(&mut self, charge_id: &str) -> Result<(), PaymentError> {
        println!("refunding charge {}", charge_id);
        self.gateway.refund(charge_id)?;
        self.ledger.mark_refunded(charge_id);
        Ok(())
    }
}
