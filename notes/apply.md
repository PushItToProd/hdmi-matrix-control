# Batched routing and observed status

`POST /apply` sends one or both outputs' routing commands under one serial
lock, with a 100 ms gap, then drains all complete acknowledgements. Examples:

```json
{"A": 2, "B": 4}
```

```json
{"A": {"mode": "2plr", "left": 1, "right": 4}, "B": {"input": 1}}
```

Either output may be omitted. A supports `single`, `2x2`, `2plr`, `2pud`,
`1b3s`, and `pip`, with the same mode fields as `/status`. Input values are
integers 1–4. `resolution` is accepted as read-only metadata; `copy_a` is
rejected, because its hardware commands cannot safely join a routing batch.
When copying status into a request, omit B's `copy_a` field. Validate the whole
request before sending any command.

Success retains the existing `{"status":"success","response":"..."}`
envelope. It means every complete acknowledgement was received, **not** that
every route applied. Callers still verify `/status`; the hardware captures
showed both commands acknowledged while only B switched.

The reader accumulates bytes across merged, partial, delayed, and unexpected
replies. One monotonic read budget (default one second after the last write)
covers the whole batch. A rejection yields 502; missing replies yield 504;
other device errors yield 500. These outcomes may follow a partial switch and
are not automatically retried. Every attempted command invalidates status,
including failed commands. Pacing also applies across separate requests, so
an early acknowledgement cannot make the next STA write happen before 100 ms.
`HDMIMatrix(command_gap=..., timeout=...)` exposes the pacing and read budget.

The serial `quick` argument is removed. Legacy `/set-output-input` and
`/set-output-a-mode` endpoints use the same reader and transaction lock.
An old `?quick=true` query is ignored and the request now waits for its reply.
Help and copy-A commands retain their bounded unframed/silent-reply handling
outside `/apply`.

## Freshness and conditional writes

`GET /status` adds:

- `observed_at`: UTC timestamp when the underlying device read started.
- `age_ms`: elapsed monotonic milliseconds since that read started, including
  time spent reading and serving the cached observation.
- `version`: integer revision. It advances on changed observations and every
  attempted command, including uncertain outcomes. Identical fresh readings
  keep the revision and update the observation time.

The cache retains single-flight reads and reuses a completed read for **five
seconds** by default. Ordinary status clients share that observation even if
their requests arrive at different times. Explicit refresh and verification
callers can use `GET /status?max_age=0.2` to request the previous 200 ms reuse
budget, shorter than lrc's 300 ms verification interval. lrc requests that
freshness on its scheduled and post-command reads; its scheduling determines
how often it calls the service. Commands still invalidate the cache, and
conditional writes check against a reading no more than 200 ms past completion,
independently of the default display-cache TTL.

Age describes the observation; `max_age` and TTL describe reuse after its
completion. A supplied `max_age` can shorten, but not extend, the default TTL.
Versions increase within a service
instance and are seeded from epoch microseconds to avoid reusing small
counters on ordinary restarts. They are not a persisted device sequence.

Include the last observed `version` as `if_version` in `/apply` to reject a
stale calculation:

```json
{"A": 4, "B": 1, "if_version": 1789840000000001}
```

The service refreshes an expired observation, checks its revision, and sends
the batch under one transaction lock. A mismatch returns **409 before any
write**. All HTTP writes, including legacy routes and raw captures, use this
lock. Refresh and review the new routing on a conflict; do not replay a swap
calculated from old inputs. Front-panel/IR changes can still happen between
an observation and the write; this is not a hardware atomic compare-and-set.

## Clients and rollout

lrc uses one `/apply` request for set-input, set-mode, swap, and apply. Swaps
carry `if_version`; a 409 refreshes the UI and returns a refused result, even
if the new observation happens to match the old intent. Other write failures
still go through observation-based verification. Absolute routes do not need
a version precondition. lrc preserves the service's observation timestamp.

Passive lrc polling defaults to a five-second gap while watched and 30 seconds
while idle. This is an incremental reduction from three seconds after reports
of intermittent blackouts during status-only polling; five seconds has not yet
been validated as a safe interval on the device. Multiple tabs connected to one
lrc agent share its polling loop. The five-second default Portta cache also
reduces reads from independent status consumers; clients explicitly requesting
fresh readings still need to limit their own request rate.

The commercial detector also sends one shorthand `/apply` request for its
configured outputs, awaiting acknowledgement and logging failures without
replay. Its classification routing remains absolute; it does not gain lrc's
post-command verification.

Deploy the Portta service **first**, then the updated lrc agent and detector.
The live Portta service belongs to the detector Compose stack and bind-mounts
the Portta checkout. Restart it after updating that checkout. The updated
clients require `/apply`; older clients can use the retained legacy endpoints
during rollout. No hardware deployment is part of this implementation change.

The capture plan is complete and removed. Raw recordings and the historical
[capture results](capture-results-2026-09-19.md) remain. Reader regression tests
replay the measured bursts; lrc also tests the acknowledged-but-unapplied
swap recording. Longer settling delays, optimistic routing, and automatic
coalescing remain deferred.
