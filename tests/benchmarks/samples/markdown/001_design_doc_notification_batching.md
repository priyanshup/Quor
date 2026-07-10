# Design Doc: Notification Batching for the Alerts Service

## Background

Over the last two quarters, the Alerts Service has grown from a simple email-on-trigger
pipeline into the primary way operators find out about incidents across every team that
depends on it. As adoption has grown, so has the volume of individual notifications each
on-call engineer receives during a noisy incident. A single flapping health check can, in
the worst observed case, generate several hundred individual notification events within a
ten-minute window, each one dispatched as its own email, Slack message, and push
notification. This is not a hypothetical concern — three separate on-call rotations have
filed complaints in the last month specifically about notification volume during incidents,
and one engineer described the experience as "impossible to tell which of the two hundred
messages actually matters." The support team has independently flagged this as a top-five
driver of "please mute this channel" requests, which defeats the entire purpose of the
alerting system: if operators mute the channel to survive an incident, they may miss the
next genuinely novel alert that arrives once the storm has passed.

This document proposes a batching layer that sits between the existing rule evaluation
engine and the notification dispatchers (email, Slack, push). The goal is to reduce the
number of individual notification events a human receives during a burst, without
increasing the time-to-first-notification for a genuinely new incident, and without
silently dropping any individual alert's content — every alert that would have been sent
individually today must still be discoverable in the batched digest.

## Problem Statement

Today, every rule evaluation that transitions from OK to ALERT (or ALERT to OK) produces
exactly one notification event, dispatched immediately and independently of any other
in-flight notification. This model works well for isolated, low-frequency alerts, but
breaks down under two common real-world conditions:

- **Flapping conditions.** A metric that oscillates around a threshold can transition
  state dozens of times in a short window, each transition producing its own notification.
- **Correlated failures.** A single root cause (a bad deploy, a downstream dependency
  outage) can trigger many independent rules simultaneously, each of which is individually
  correct to fire, but which collectively describe one underlying problem rather than many
  unrelated ones.

REQ-101: The system must reduce the number of individual notification events delivered to
a human during a burst of correlated or flapping alerts, without requiring the operator to
change how they configure individual alert rules.

REQ-102: Any alert that would have been delivered under the current one-event-per-transition
model must remain fully recoverable from the batched output — batching is a presentation and
delivery-cadence concern, not a data-loss risk.

REQ-103: Time-to-first-notification for a genuinely novel alert (the first alert of a new
kind within a configurable batching window) must not regress relative to the current
unbatched behavior.

## Non-Goals

This design does not attempt to perform root-cause correlation across dissimilar alert
types. Grouping alerts that share an underlying cause but fire on unrelated rules (for
example, a database latency alert and a checkout-conversion-rate alert that are both
downstream of the same network partition) is a genuinely hard problem that would require
either a dependency graph of services or a statistical correlation engine, and is
explicitly out of scope for this iteration. This document is about reducing volume from a
*single* rule's repeated transitions and about grouping alerts that fire within the same
short window, not about explaining *why* they are related.

This design also does not change how individual rules are authored, evaluated, or
thresholded. The batching layer sits entirely downstream of rule evaluation and has no
visibility into, or influence over, the evaluation logic itself.

## Proposed Architecture

The batching layer is a new component, the Notification Aggregator, inserted between the
existing Rule Evaluator and the three notification dispatchers. Its responsibilities are:

1. Receive every notification event exactly as it is produced today (no change to the
   Rule Evaluator's output contract).
2. Group events into a batch keyed by (rule identifier, alerting channel, batching window).
3. Flush a batch either when the batching window elapses or when a configurable maximum
   batch size is reached, whichever comes first.
4. Render a single digest notification per flushed batch, containing a summary line plus
   the full list of individual events that were folded into it.

### Batching Window

**Decision:** the default batching window is 60 seconds, configurable per notification
channel (not globally, and not per-rule). Sixty seconds was chosen as a starting point
because it is long enough to catch the overwhelming majority of flapping bursts observed in
the last quarter's incident data, while remaining short enough that "time to first
notification" for a genuinely new alert is not perceptibly worse than today's behavior —
the first event in any new batch is always flushed immediately, per REQ-103, so the window
only affects the *second and subsequent* events of the same kind.

Channel-level (rather than rule-level) configuration was chosen over the alternative of
letting each rule author set their own window, because rule authors do not have visibility
into how noisy their rule turns out to be in practice until after it has already caused a
problem — defaulting sensibly at the channel level and allowing an explicit per-channel
override covers the common case without requiring every rule author to make a judgment call
they are not well positioned to make.

### Batch Flushing

A batch is flushed under either of two conditions:

- The batching window (default 60 seconds, per channel) has elapsed since the first event
  in the batch arrived.
- The batch has accumulated `max_batch_size` events (default 200), whichever comes first —
  this cap exists specifically to bound worst-case digest size during a truly extreme burst,
  and to bound the Aggregator's own memory footprint per in-flight batch.

TODO: determine whether `max_batch_size` should scale with the number of distinct services
represented in the batch, rather than being a flat constant — early data suggests a batch
that spans many services during a widespread outage may warrant a lower per-service cap
than a batch that is entirely one noisy rule on one service, but there isn't yet enough
production data to set that policy with confidence. Tracked separately; not blocking this
design.

### Digest Rendering

Each flushed batch renders as a single notification with:

- A summary line: rule name, total event count in the batch, and the time range covered.
- The full, unabridged list of individual events, each retaining its original timestamp,
  severity, and message — nothing about an individual event's content is summarized,
  rewritten, or dropped. Batching changes *delivery cadence*, never *content*.
- A link to the underlying incident timeline, where every individual event remains
  independently queryable exactly as it is today.

```python
def render_digest(batch: NotificationBatch) -> str:
    lines = [
        f"{batch.rule_name}: {len(batch.events)} events "
        f"between {batch.first_event.timestamp} and {batch.last_event.timestamp}"
    ]
    for event in batch.events:
        lines.append(f"  [{event.timestamp}] {event.severity}: {event.message}")
    return "\n".join(lines)
```

## Failure Modes

**WARNING:** the Aggregator introduces a new single point of delay between rule evaluation
and delivery. If the Aggregator itself becomes unavailable, notifications must not be
silently dropped — the design requires a fail-open path where, if the Aggregator cannot
accept an event within a short timeout, the event is dispatched directly through the
existing unbatched path instead. This preserves the current system's delivery guarantee at
the cost of losing batching's benefit for that one event, which is the correct trade-off:
a slightly noisier notification stream during an Aggregator outage is acceptable, a
silently dropped alert is not.

**NOTE:** clock skew between the event producer and the Aggregator could, in principle,
cause an event to be assigned to the wrong batching window. Because the batching window is
short (60 seconds default) and the consequence of a misassignment is at most "this event
appears in the next digest instead of this one" rather than any data loss, this is judged
an acceptable risk rather than something requiring a distributed-clock solution.

## Rollout Plan

1. Ship the Aggregator behind a per-channel opt-in flag, defaulting to off.
2. Enable on the two channels with the highest historical notification volume first,
   with the affected on-call rotations informed in advance.
3. Collect at least two weeks of before/after volume data on those two channels before
   considering a broader default-on rollout.
4. ADR-201 will record the final decision on default-on timing once that data exists;
   it is deliberately not decided in this document.

## Open Questions

- Should a digest that spans multiple distinct severities (e.g. mostly WARNING with one
  CRITICAL mixed in) surface the highest severity in the summary line, or always summarize
  at the batch's own aggregate severity? Current lean is "always surface the highest
  severity present," so a single CRITICAL event is never hidden inside a WARNING-labeled
  digest, but this needs sign-off from the on-call rotations most affected by it.
- FIXME: the current draft of the digest renderer does not yet handle the case where a
  batch's events span more than one calendar day (a genuinely long-running flap). The
  summary line's "time range" formatting assumes same-day events; this needs a follow-up
  pass before the design is considered final.

## Appendix: Example Digest Output

A representative digest for a flapping health check, as it would appear to an on-call
engineer under this design:

```
payments-latency-p99: 47 events between 03:14:02 and 03:14:58
  [03:14:02] WARNING: p99 latency 850ms exceeds 500ms threshold
  [03:14:07] OK: p99 latency 410ms back within threshold
  [03:14:11] WARNING: p99 latency 720ms exceeds 500ms threshold
  [03:14:16] OK: p99 latency 380ms back within threshold
  ... (43 more events omitted from this appendix for brevity, all present in the real digest)
```

Every one of the 47 events remains individually present and queryable in the real digest
and in the incident timeline — the appendix above elides the middle only for the purposes
of this document, which is a liberty the actual Aggregator implementation never takes.

## Related Work and Prior Art

Several other teams at the company have faced a version of this same problem before, and it
is worth summarizing what they did and why their solutions do not directly transfer here,
so that reviewers of this document do not spend time re-suggesting approaches that were
already considered and set aside for reasons specific to this system. The billing team's
dunning-email pipeline faced a superficially similar volume problem two years ago, where a
single payment-retry loop could generate many individual failure notifications in a short
window. Their solution was to simply rate-limit the notification channel itself, dropping
any event beyond a fixed cap within a time window rather than batching and preserving all of
them. That approach was explicitly rejected for this design, because REQ-102 above requires
that no alert content ever be lost, and a rate limiter that silently drops events the way
the billing team's does is fundamentally incompatible with that requirement. The billing
team's use case tolerated data loss in a way that an operational alerting system cannot:
a dropped dunning email retry notification is a minor inconvenience, while a dropped
CRITICAL infrastructure alert could mean a real incident goes unnoticed.

The search-infra team took a different approach that is closer in spirit to what this
document proposes: they built a deduplication layer that collapses genuinely identical
repeated events (same rule, same message, same target) into a single notification with a
repeat counter, without any time-windowed batching at all. This is a strictly narrower
solution than what is proposed here, because it only helps with exact-duplicate flapping
and does nothing for the correlated-but-not-identical case described in the Problem
Statement above (many different rules firing together during one incident). Their approach
was considered as a smaller, lower-risk first step, but ultimately rejected as the primary
solution here because the correlated-failure case is, based on the incident data reviewed
for this document, at least as significant a contributor to notification volume as pure
flapping is, and a solution that only addresses one of the two would leave real value on
the table.

It is also worth noting that several commercial alerting products this company evaluated in
the past (outside the scope of this document to name specifically) offer batching or
grouping as a built-in feature, and part of the motivation for building this internally
rather than adopting one of those products wholesale is that the existing Rule Evaluator and
its three notification dispatchers are deeply integrated with several other internal systems
in ways that would make a wholesale platform migration a much larger project than the
narrower problem this document is trying to solve. This document does not attempt to
relitigate that broader platform decision, which was made independently and is out of scope
here; it simply takes the existing Rule Evaluator and dispatcher architecture as a given
constraint and designs within it.

Finally, it is worth being honest that this document's proposed 60-second default window was
not derived from a rigorous statistical analysis of the incident data — it was derived from
a rough eyeballing of a sample of past incidents, where 60 seconds appeared to be roughly the
point past which the marginal benefit of a longer window (catching slightly more of a
flapping burst) started to trade off noticeably against the cost of delaying legitimate
distinct events into the same digest. A more rigorous analysis, if someone has the time to
do it properly, might land on a meaningfully different number, and the rollout plan's
two-week data-collection period before wider default-on rollout is specifically designed to
catch that possibility before it becomes a default affecting every channel at once.
