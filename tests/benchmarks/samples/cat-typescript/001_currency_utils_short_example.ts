/** Currency formatting helpers shared across the storefront frontend. */

const CENTS_PER_UNIT = 100;

/**
 * Format a whole-cent integer amount as a localized currency string.
 */
export function formatCurrency(cents: number, currency: string = "USD"): string {
  const units = cents / CENTS_PER_UNIT;
  return new Intl.NumberFormat("en-US", { style: "currency", currency }).format(units);
}

export function parseCentsFromDollars(dollars: string): number {
  return Math.round(parseFloat(dollars) * CENTS_PER_UNIT);
}
