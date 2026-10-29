# Observability and Instrumentation Playbook (English)

Complete methodology for instrumenting code so production behavior is visible and diagnosable. Load
this before adding any telemetry. The Chinese counterpart is `references/zh-CN.md`; both carry
identical rules, thresholds and checklists.

---

## 1. Overview

Code you can't observe is code you can't operate. Observability is the ability to answer "what is the
system doing and why?" from the outside, using the telemetry the code emits. Instrumentation is not a
post-launch add-on — it's written alongside the feature, the same way tests are. If a feature ships
without telemetry, the first user-reported bug becomes archaeology instead of a query.

---

## 2. When to Use

- Building any feature that will run in production
- Adding a new service, endpoint, background job, or external integration
- A production incident took too long to diagnose ("we couldn't tell what happened")
- Setting up or reviewing alerting rules
- Reviewing a PR that adds I/O, retries, queues, or cross-service calls

**NOT for:**

- Diagnosing a failure happening right now — use the `debugging-and-error-recovery` skill
  (observability is what makes that skill fast next time)
- Profiling and optimizing measured slowness — use the `performance-optimization` skill
- Launch-day monitoring checklists and rollback triggers — see the `shipping-and-launch` skill; this
  skill covers the instrumentation that feeds them

---

## 3. Step 1 — Define "Working" Before Instrumenting

Telemetry without a question is noise. Before adding any instrumentation, write down 2–4 questions an
on-call engineer will ask about this feature:

```
FEATURE: checkout payment retry
QUESTIONS ON-CALL WILL ASK:
1. What fraction of payments succeed on first attempt vs after retry?
2. When a payment fails permanently, why? (provider error? timeout? validation?)
3. Is the payment provider slower than usual?
→ Every signal below must help answer one of these.
```

If you can't name the questions, you're not ready to instrument — you'll log everything and learn
nothing.

Write the questions where the on-call engineer will find them: in the feature's runbook, in the ADR
that covers the feature, or in a comment above the telemetry setup. Questions nobody can find get
answered by guesswork at 3am.

---

## 4. Step 2 — Pick the Right Signal for Each Question

| Signal | Answers | Cost profile | Example |
|---|---|---|---|
| **Structured log** | "What happened in this specific case?" | Per-event; grows with traffic | `payment_failed` with provider error code |
| **Metric** | "How often / how fast, in aggregate?" | Fixed per series; cheap to query | p99 latency of provider calls |
| **Trace** | "Where did time go across services?" | Per-request; usually sampled | One slow checkout, broken down by hop |

Rule of thumb: metrics tell you **that** something is wrong, traces tell you **where**, logs tell you
**why**.

The cost profile drives the choice. A per-event signal (logs) gets expensive and useless at high
volume; a fixed-cardinality aggregate (metrics) stays cheap and is the only thing you can alert on
reliably. If the question is "how often", it is a metric — not a log line someone greps at 3am.

---

## 5. Step 3 — Structured Logging

Log events, not prose. Every log line is a JSON object with a stable event name and machine-readable
fields:

```typescript
// BAD: string interpolation — unqueryable, inconsistent
logger.info(`Payment ${id} failed for user ${userId} after ${n} retries`);

// GOOD: stable event name + structured fields
logger.warn({
  event: 'payment_failed',
  paymentId: id,
  provider: 'stripe',
  errorCode: err.code,
  attempt: n,
}, 'payment failed');
```

**Log levels — use them consistently:**

| Level | Meaning | On-call action |
|---|---|---|
| `error` | Invariant broken; someone may need to act | Investigate |
| `warn` | Degraded but handled (retry succeeded, fallback used) | Watch for trends |
| `info` | Significant business event (order placed, job finished) | None |
| `debug` | Diagnostic detail | Off in production by default |

**Correlation IDs are mandatory.** Generate (or accept) a request ID at the system boundary and attach
it to every log line, span, and outbound call. Without it, you cannot reconstruct a single request
from interleaved logs:

```typescript
// Express: child logger per request, ID propagated downstream
app.use((req, res, next) => {
  req.id = req.headers['x-request-id'] ?? crypto.randomUUID();
  req.log = logger.child({ requestId: req.id });
  res.setHeader('x-request-id', req.id);
  next();
});
```

Accept an inbound ID when one is present and generate one when it is not — otherwise the trace breaks
at your boundary and every upstream hop is orphaned.

**When several entry points write to one log, name the entry point.** A correlation ID identifies a
run; it does not say which code path started it. The same job reached by a scheduler, by a replay
endpoint, and by a manual CLI run produces interchangeable lines in one sink, so attributing a line
falls back to elimination — cross-reading the scheduler's history, the process table, a deploy log —
and that argument holds only as long as those external records happen to still exist. Stamp the entry
point where the run starts, next to the correlation ID, and propagate both the same way:

```typescript
// One helper for every entry point: the run's own logger carries both fields.
// `entryPoint`, not `source` — ECS reserves `source.*` for network fields.
export const runLog = (entryPoint: 'scheduler' | 'replay_endpoint' | 'cli', runId: string) =>
  logger.child({ entryPoint, requestId: runId });

// scheduler tick        -> runLog('scheduler', crypto.randomUUID())
// POST /jobs/:id/replay -> runLog('replay_endpoint', req.id)
// CLI invocation        -> runLog('cli', process.env.RUN_ID ?? crypto.randomUUID())
```

Both fields have to cross the same boundaries as the correlation ID — queue metadata, HTTP headers —
or a worker re-derives the entry point and guesses. A field that merely correlates with an entry point
is a hint, not an attribution: anything that can invoke the job can reproduce it.

**Never log secrets, tokens, passwords, or full PII.** This is a hard rule from the
`security-and-hardening` skill — telemetry pipelines are a classic data-leak path. Allowlist fields;
don't log whole request bodies.

```typescript
// Allowlist, don't denylist: only these fields ever reach the log sink
const safeFields = (user: User) => ({ userId: user.id, plan: user.plan });
// Never: logger.info({ user }) — the whole object, including tokens, lands in the sink
```

---

## 6. Step 4 — Metrics

For request-driven services, instrument **RED** on every endpoint and every external dependency:
**R**ate (requests/sec), **E**rrors (failure rate), **D**uration (latency histogram, not average). For
resources (queues, pools, hosts), use **USE**: **U**tilization, **S**aturation, **E**rrors.

As with tracing, the vendor-neutral path is the OpenTelemetry metrics API (same SDK and context as
step 5). The example below uses Prometheus' `prom-client` — one common backend choice, not the only
one; the RED/USE and cardinality rules are identical either way.

```typescript
import { Histogram } from 'prom-client';

const httpDuration = new Histogram({
  name: 'http_request_duration_seconds',
  help: 'HTTP request duration',
  labelNames: ['method', 'route', 'status_class'],  // '2xx', not '200'
  buckets: [0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
});
```

**Cardinality is the failure mode.** Every unique label combination is a separate time series. Labels
must come from small, fixed sets (route template, status class, provider name). Never use user IDs,
raw URLs, error messages, or other unbounded values as labels — that belongs in logs and traces.

```
OK as label:    route="/api/tasks/:id"   status_class="5xx"   provider="stripe"
NEVER a label:  user_id, email, request_id, full URL, error message text
```

Track averages never, percentiles always: an average hides the 1% of users having a terrible time. Use
histograms and read p50/p95/p99.

**Naming conventions keep metrics findable:** suffix `_seconds` for durations, `_total` for counters,
use base units (seconds, bytes) rather than milliseconds or kilobytes, and put the unit in the name so
a dashboard author never has to guess.

---

## 7. Step 5 — Distributed Tracing

Use OpenTelemetry — it's the vendor-neutral standard, and auto-instrumentation covers HTTP, gRPC, and
common DB clients with near-zero code:

```typescript
// tracing.ts — must be imported before anything else
import { NodeSDK } from '@opentelemetry/sdk-node';
import { getNodeAutoInstrumentations } from '@opentelemetry/auto-instrumentations-node';

const sdk = new NodeSDK({
  serviceName: 'checkout-service',
  instrumentations: [getNodeAutoInstrumentations()],
});
sdk.start();
```

Add manual spans only around meaningful internal units of work (e.g., `applyDiscounts`,
`chargeProvider`) and attach the attributes on-call will filter by. Propagate context across every
async boundary — HTTP headers, queue message metadata — or the trace dies at the gap. Sample
head-based at a low rate by default; keep 100% of errors if your backend supports tail sampling.

"Must be imported before anything else" is literal: auto-instrumentation patches modules at import
time, so a `tracing.ts` imported after the HTTP client registers has nothing left to patch, and the
traces come back empty with no error anywhere.

---

## 8. Step 6 — Alerting

Alert on **symptoms users feel**, not on causes:

```
SYMPTOM (page-worthy):           CAUSE (dashboard, not a page):
error rate > 1% for 5 min        CPU at 85%
p99 latency > 2s                 one pod restarted
queue age > 10 min               disk at 70%
```

Cause-based alerts fire when nothing is wrong and miss failures you didn't predict. Symptom-based
alerts fire exactly when users are hurt, regardless of the cause.

Rules for every alert you create:

1. **It must be actionable.** If the response is "ignore it, it self-heals", delete the alert.
2. **It links to a runbook** — even three lines: what it means, first query to run, escalation path.
3. **It has a threshold and duration** justified by the SLO or by historical data, not by a guess.
4. Use two severities only: **page** (user-facing, act now) and **ticket** (degradation, act this
   week). A third tier becomes noise that trains people to ignore everything.

The duration is what separates a page from a flapping nuisance: a threshold crossed for one scrape
interval is noise, the same threshold crossed for five minutes is a signal. Pick the window from the
SLO's error budget or from how long the condition has historically taken to self-resolve.

---

## 9. Step 7 — Verify the Telemetry Itself

Instrumentation is code; it can be wrong. Before calling the work done, trigger the paths and look at
the actual output:

- Force an error in staging → find it in the logs by `requestId`, confirm fields are structured (not `[object Object]`)
- Send test traffic → confirm metric series appear with the expected labels and sane values
- Follow one request across services in the tracing UI → no broken spans
- Fire each new alert once (lower the threshold temporarily) → confirm it reaches the right channel and the runbook link works

The final test is the one that matters: **an induced failure in staging must be diagnosable from
telemetry alone, without reading the source.** If you have to open the code to understand what
happened, the instrumentation is incomplete.

---

## 10. Common Rationalizations

| Rationalization | Reality |
|---|---|
| "I'll add logging after it works" | "After" becomes "after the first incident", which is the most expensive moment to discover you're blind. Instrument as you build. |
| "More logs = more observability" | Unstructured noise makes incidents slower, not faster. Three queryable events beat three hundred prose lines. |
| "console.log is fine for now" | Unstructured output can't be filtered, correlated, or alerted on. The structured logger costs five extra minutes once. |
| "We can just look at the dashboards when something breaks" | Dashboards built without defined questions show you everything except the answer. Start from on-call questions. |
| "Alert on everything important, we'll tune later" | A noisy pager trains people to ignore it. The tuning never happens; the missed real page does. |
| "User ID as a metric label makes debugging easier" | It also makes your metrics backend fall over. High-cardinality lookups belong in logs and traces. |
| "Tracing is overkill for our two services" | Two services already means cross-service latency questions logs can't answer. Auto-instrumentation makes the cost trivial. |
| "The correlation ID is enough to attribute a run" | It identifies the run, not the entry point. With three callers writing one sink, attribution becomes elimination across records that may not exist tomorrow. |

---

## 11. Red Flags

- A feature PR with retries, queues, or external calls and zero new telemetry
- Log lines built by string interpolation instead of structured fields
- No correlation/request ID — each log line is an orphan
- One log stream fed by a scheduler, a webhook, and manual runs, with no field naming which one produced the line
- Metrics labeled with user IDs, raw URLs, or error message text (cardinality bomb)
- Latency tracked as an average with no percentiles
- Alerts that fire daily and get acknowledged without action
- Alerts on causes (CPU, memory) paging humans while user-facing error rate is unmonitored
- Secrets, tokens, or full request bodies appearing in logs
- "It works on my machine" as the only evidence a production feature is healthy
- A tracing setup imported after the libraries it is supposed to instrument
- An alert with no runbook link

---

## 12. Verification Checklist

After instrumenting a feature, confirm:

- [ ] The on-call questions for this feature are written down, and each signal maps to one
- [ ] All log output is structured (JSON), with stable event names and a correlation ID on every line
- [ ] Every log sink written by more than one entry point carries an entry-point field, set where the run starts and propagated with the correlation ID rather than inferred downstream
- [ ] No secrets, tokens, or unredacted PII in any log line (spot-check actual output)
- [ ] RED metrics exist for every new endpoint and every external dependency, with bounded label sets
- [ ] Latency is a histogram; p95/p99 are queryable
- [ ] A single request can be followed end-to-end in the tracing UI without broken spans
- [ ] Every new alert is symptom-based, has a runbook link, and was test-fired once
- [ ] An induced failure in staging was located via telemetry alone, without reading the source

### Pre-Launch Instrumentation Gate

The at-a-glance version of the list above, as a gate a feature must clear before it ships:

- [ ] On-call questions written down and findable (runbook / ADR / setup comment)
- [ ] Structured logging on every new path, with correlation ID and entry-point field
- [ ] RED metrics on every new endpoint and external dependency
- [ ] USE metrics on every new queue, pool or resource
- [ ] Labels bounded to small fixed sets; no high-cardinality label anywhere
- [ ] Latency histograms in place; no metric is an average
- [ ] Tracing initialized first, context propagated across every async boundary
- [ ] Symptom-based alerts defined with thresholds, durations and runbook links
- [ ] Two severities only — page and ticket
- [ ] Telemetry verified end to end: induced error found by `requestId`, spans unbroken, alerts test-fired
- [ ] No secrets or full PII in any emitted payload
