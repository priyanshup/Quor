/**
 * Input-parsing helpers with heavy use of function overload signatures —
 * the public API accepts several distinct input shapes, and overloads let
 * callers get a precisely-typed return value for each rather than one
 * broad, weakly-typed signature.
 */

export function parseAmount(input: number): number;
export function parseAmount(input: string): number;
export function parseAmount(input: { cents: number }): number;
export function parseAmount(input: number | string | { cents: number }): number {
  if (typeof input === "number") {
    return Math.round(input);
  }
  if (typeof input === "string") {
    return Math.round(parseFloat(input) * 100);
  }
  return input.cents;
}

export function formatDate(input: Date): string;
export function formatDate(input: number): string;
export function formatDate(input: string, format: "iso"): string;
export function formatDate(input: Date | number | string, format?: "iso"): string {
  const date = input instanceof Date ? input : new Date(input);
  if (format === "iso" || typeof input === "string") {
    return date.toISOString();
  }
  return date.toLocaleDateString("en-US");
}

export class QueryBuilder {
  private clauses: string[] = [];

  where(field: string, value: string): this;
  where(field: string, operator: string, value: string): this;
  where(field: string, operatorOrValue: string, value?: string): this {
    if (value === undefined) {
      this.clauses.push(`${field} = ${operatorOrValue}`);
    } else {
      this.clauses.push(`${field} ${operatorOrValue} ${value}`);
    }
    return this;
  }

  build(): string {
    return this.clauses.join(" AND ");
  }
}

export function createLogger(name: string): Console;
export function createLogger(name: string, silent: true): { log: () => void };
export function createLogger(name: string, silent = false): Console | { log: () => void } {
  if (silent) {
    return { log: () => {} };
  }
  return console;
}
