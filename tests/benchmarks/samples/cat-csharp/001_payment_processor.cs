// Payment processing orchestration for the storefront checkout flow.
using System;
using Storefront.Payments.Errors;
using Storefront.Payments.Gateway;

namespace Storefront.Payments
{
    /// <summary>
    /// Coordinates charge, refund, and duplicate-detection logic.
    /// </summary>
    public class PaymentProcessor
    {
        private const string DefaultCurrency = "USD";

        private readonly IPaymentGateway _gateway;
        private readonly IChargeLedger _ledger;

        public PaymentProcessor(IPaymentGateway gateway, IChargeLedger ledger)
        {
            _gateway = gateway;
            _ledger = ledger;
        }

        /// <summary>
        /// Charge an account, guarding against duplicate pending charges.
        /// </summary>
        public string Charge(string accountId, int amount)
        {
            if (_ledger.HasPendingCharge(accountId, amount))
            {
                throw new DuplicateChargeException(accountId);
            }
            Console.WriteLine($"charging account {accountId} for {amount}");
            var result = _gateway.Charge(accountId, amount);
            _ledger.MarkSettled(accountId, result.ChargeId);
            return result.ChargeId;
        }

        /// <summary>
        /// Refund a previously settled charge.
        /// </summary>
        public void Refund(string chargeId)
        {
            Console.WriteLine($"refunding charge {chargeId}");
            _gateway.Refund(chargeId);
            _ledger.MarkRefunded(chargeId);
        }
    }

    public class DuplicateChargeException : Exception
    {
        public DuplicateChargeException(string accountId)
            : base($"duplicate pending charge for account {accountId}")
        {
        }
    }
}
