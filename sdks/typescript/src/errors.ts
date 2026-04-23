/**
 * Typed errors raised by the OpenGraph SDK.
 *
 * Catch `OpenGraphError` to handle any failure; narrow further when you
 * want to react to a specific condition (401 vs 429 vs 5xx).
 */

export class OpenGraphError extends Error {
  readonly status: number;
  readonly detail?: string;
  readonly payload?: unknown;

  constructor(message: string, opts: { status?: number; detail?: string; payload?: unknown } = {}) {
    super(message);
    this.name = "OpenGraphError";
    this.status = opts.status ?? 0;
    this.detail = opts.detail;
    this.payload = opts.payload;
  }
}

export class AuthError extends OpenGraphError {
  constructor(msg: string, opts: ConstructorParameters<typeof OpenGraphError>[1] = {}) {
    super(msg, opts);
    this.name = "AuthError";
  }
}

export class NotFoundError extends OpenGraphError {
  constructor(msg: string, opts: ConstructorParameters<typeof OpenGraphError>[1] = {}) {
    super(msg, opts);
    this.name = "NotFoundError";
  }
}

export class ValidationError extends OpenGraphError {
  constructor(msg: string, opts: ConstructorParameters<typeof OpenGraphError>[1] = {}) {
    super(msg, opts);
    this.name = "ValidationError";
  }
}

export class ServerError extends OpenGraphError {
  constructor(msg: string, opts: ConstructorParameters<typeof OpenGraphError>[1] = {}) {
    super(msg, opts);
    this.name = "ServerError";
  }
}

export class RateLimitError extends OpenGraphError {
  readonly retryAfterSeconds?: number;

  constructor(
    msg: string,
    opts: ConstructorParameters<typeof OpenGraphError>[1] & { retryAfterSeconds?: number } = {},
  ) {
    super(msg, opts);
    this.name = "RateLimitError";
    this.retryAfterSeconds = opts.retryAfterSeconds;
  }
}
