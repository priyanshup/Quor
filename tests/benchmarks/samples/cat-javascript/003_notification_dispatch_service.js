/**
 * Notification dispatch service — fans out alert events to email, SMS,
 * and webhook subscribers, with per-channel rate limiting and batching.
 */

import { EventEmitter } from "events";
import { sendEmail } from "./email-client.js";
import { sendSms } from "./sms-client.js";
import { logger } from "./logger.js";

const CHANNELS = Object.freeze({
  EMAIL: "email",
  SMS: "sms",
  WEBHOOK: "webhook",
});

const DEFAULT_BATCH_WINDOW_MS = 500;
const MAX_BATCH_SIZE = 50;
const WEBHOOK_TIMEOUT_MS = 5000;

export class RateLimiter {
  constructor(maxPerMinute) {
    this.maxPerMinute = maxPerMinute;
    this.timestamps = [];
  }

  allow() {
    const now = Date.now();
    const cutoff = now - 60_000;
    this.timestamps = this.timestamps.filter((t) => t > cutoff);
    if (this.timestamps.length >= this.maxPerMinute) {
      return false;
    }
    this.timestamps.push(now);
    return true;
  }

  reset() {
    this.timestamps = [];
  }
}

export class NotificationBatcher {
  constructor(windowMs = DEFAULT_BATCH_WINDOW_MS) {
    this.windowMs = windowMs;
    this.pending = [];
    this.timer = null;
  }

  add(event, flush) {
    this.pending.push(event);
    if (this.pending.length >= MAX_BATCH_SIZE) {
      this._flushNow(flush);
      return;
    }
    if (!this.timer) {
      this.timer = setTimeout(() => this._flushNow(flush), this.windowMs);
    }
  }

  _flushNow(flush) {
    if (this.timer) {
      clearTimeout(this.timer);
      this.timer = null;
    }
    const batch = this.pending;
    this.pending = [];
    if (batch.length > 0) {
      flush(batch);
    }
  }
}

export class WebhookSubscription {
  constructor(id, url, secret, events) {
    this.id = id;
    this.url = url;
    this.secret = secret;
    this.events = new Set(events);
    this.consecutiveFailures = 0;
  }

  matches(eventType) {
    return this.events.has(eventType) || this.events.has("*");
  }

  isDisabled() {
    return this.consecutiveFailures >= 5;
  }
}

export class NotificationDispatchService extends EventEmitter {
  constructor(subscriptionStore) {
    super();
    this.subscriptionStore = subscriptionStore;
    this.rateLimiters = new Map();
    this.batcher = new NotificationBatcher();
  }

  /**
   * Fan an event out to every channel a subscriber has opted into.
   */
  async dispatch(event) {
    logger.info("dispatching event %s (%s)", event.id, event.type);
    const subscribers = await this.subscriptionStore.findFor(event.type);
    const results = [];
    for (const subscriber of subscribers) {
      results.push(await this._dispatchToSubscriber(event, subscriber));
    }
    this.emit("dispatched", { eventId: event.id, count: results.length });
    return results;
  }

  async _dispatchToSubscriber(event, subscriber) {
    switch (subscriber.channel) {
      case CHANNELS.EMAIL:
        return this._dispatchEmail(event, subscriber);
      case CHANNELS.SMS:
        return this._dispatchSms(event, subscriber);
      case CHANNELS.WEBHOOK:
        return this._dispatchWebhook(event, subscriber);
      default:
        throw new Error(`unknown channel: ${subscriber.channel}`);
    }
  }

  async _dispatchEmail(event, subscriber) {
    if (!this._checkRateLimit(subscriber.id, 30)) {
      logger.warn("email rate limit hit for %s", subscriber.id);
      return { channel: CHANNELS.EMAIL, sent: false, reason: "rate_limited" };
    }
    try {
      await sendEmail(subscriber.address, this._renderEmailBody(event));
      return { channel: CHANNELS.EMAIL, sent: true };
    } catch (err) {
      logger.error("email dispatch failed for %s: %s", subscriber.id, err.message);
      return { channel: CHANNELS.EMAIL, sent: false, reason: "send_error" };
    }
  }

  async _dispatchSms(event, subscriber) {
    if (!this._checkRateLimit(subscriber.id, 10)) {
      return { channel: CHANNELS.SMS, sent: false, reason: "rate_limited" };
    }
    const body = `${event.type}: ${event.summary}`.slice(0, 160);
    await sendSms(subscriber.phoneNumber, body);
    return { channel: CHANNELS.SMS, sent: true };
  }

  async _dispatchWebhook(event, subscription) {
    if (subscription.isDisabled()) {
      return { channel: CHANNELS.WEBHOOK, sent: false, reason: "disabled" };
    }
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), WEBHOOK_TIMEOUT_MS);
    try {
      const response = await fetch(subscription.url, {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-webhook-signature": this._sign(event, subscription.secret),
        },
        body: JSON.stringify(event),
        signal: controller.signal,
      });
      if (!response.ok) {
        subscription.consecutiveFailures += 1;
        return { channel: CHANNELS.WEBHOOK, sent: false, reason: `http_${response.status}` };
      }
      subscription.consecutiveFailures = 0;
      return { channel: CHANNELS.WEBHOOK, sent: true };
    } catch (err) {
      subscription.consecutiveFailures += 1;
      logger.error("webhook dispatch failed for %s: %s", subscription.id, err.message);
      return { channel: CHANNELS.WEBHOOK, sent: false, reason: "network_error" };
    } finally {
      clearTimeout(timeout);
    }
  }

  _checkRateLimit(subscriberId, maxPerMinute) {
    let limiter = this.rateLimiters.get(subscriberId);
    if (!limiter) {
      limiter = new RateLimiter(maxPerMinute);
      this.rateLimiters.set(subscriberId, limiter);
    }
    return limiter.allow();
  }

  _renderEmailBody(event) {
    return `<h1>${event.type}</h1><p>${event.summary}</p>`;
  }

  _sign(event, secret) {
    // HMAC signing is delegated to the shared crypto helper; kept here as a
    // thin wrapper so the signature header format stays colocated with the
    // dispatch logic that consumes it.
    return computeHmac(secret, JSON.stringify(event));
  }
}

function computeHmac(secret, payload) {
  const crypto = require("crypto");
  return crypto.createHmac("sha256", secret).update(payload).digest("hex");
}

export function buildDefaultDispatchService(subscriptionStore) {
  return new NotificationDispatchService(subscriptionStore);
}
