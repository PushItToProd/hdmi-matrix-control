# Pending: device captures for the `/apply` reader

Captures completed 2026-09-19; see [results and next steps](capture-results-2026-09-19.md).
`/apply` is still pending. The results revise the assumptions about zero-gap
acknowledgements and the proposed settling-time boundary below.

Five captures to run against the real matrix via `POST /debug/raw-command`,
once someone is free to have the TV inputs flipped around. They answer the
questions that the `/apply` design is blocked on. Delete this file once the
captures are recorded and `/apply` is written.

## Why these are needed

`/apply` will write both outputs' commands back to back and then read both
replies, replacing the `quick` fire-and-forget path that currently leaves an
unread reply on the port for the next command to trip over. Writing its reader
means knowing what the device actually emits when a second command arrives
while it is still working on the first — which is not in `test_fixtures/`,
because every fixture was captured one command at a time with a `sleep 1`
between.

## Preconditions

The service on gmktec must be running this checkout with the flag set:

```
HDMI_MATRIX_ENABLE_RAW_COMMAND=1 docker compose up -d hdmi-matrix-control
```

Check it took: `curl -sS http://gmktec.zane.network:11344/health` should be OK
and the capture endpoint should stop returning 404.

Note the routing before starting so it can be put back:

```
curl -sS http://gmktec.zane.network:11344/status
```

## The captures

Each holds the serial port exclusively for its window, so lrc's status polls
stall behind it for up to `read_seconds`. Run them one at a time.

### 1. A swap at the current 100 ms gap

```
curl -sS -X POST http://gmktec.zane.network:11344/debug/raw-command \
  -H 'content-type: application/json' \
  -d '{"commands": ["spoasi02", "spobsi04"], "gap": 0.1, "read_seconds": 2.0}'
```

Answers: does the device ack both commands; how long it defers each reply;
whether the two replies arrive as separate bursts or run together. This is the
baseline `/apply` has to handle.

### 2. The same swap with no gap

```
curl -sS -X POST http://gmktec.zane.network:11344/debug/raw-command \
  -H 'content-type: application/json' \
  -d '{"commands": ["spoasi01", "spobsi02"], "gap": 0.0, "read_seconds": 2.0}'
```

Tests whether the 100 ms gap is actually required. Recollection says a command
arriving too soon after the previous one gets dropped. If only one
`<s>…</s>` line comes back, that is confirmed and `/apply` keeps the gap.

### 3. A command followed immediately by `sta`

```
curl -sS -X POST http://gmktec.zane.network:11344/debug/raw-command \
  -H 'content-type: application/json' \
  -d '{"commands": ["spobsi01", "sta"], "gap": 0.1, "read_seconds": 3.0}'
```

Reproduces the production failure deterministically: the set command's reply
lands after the `STA` write, and the current reader takes it as the `STA`
reply. Gives an exact byte sequence to build a regression fixture from.

### 4. Mode change plus B input

```
curl -sS -X POST http://gmktec.zane.network:11344/debug/raw-command \
  -H 'content-type: application/json' \
  -d '{"commands": ["spoa2plr14", "spobsi01"], "gap": 0.1, "read_seconds": 2.0}'
```

The general `/apply` case, and the slowest command pair — a mode change makes
the device do more work than a plain input switch, so this is where a deferred
reply is most likely.

### 5. How long the device takes to report a switch it has made

Several runs, varying `gap`, each switching output B and then asking for
status once:

```
# Two inputs that GET /status reports as linked. Alternating between them
# means every run is a real switch.
a=1 b=4
for delay in 0.10 0.15 0.25 0.40 0.60 1.00; do
  echo "--- gap=$delay input=$a"
  curl -sS -X POST http://gmktec.zane.network:11344/debug/raw-command \
    -H 'content-type: application/json' \
    -d "{\"commands\": [\"spobsi0$a\", \"sta\"], \"gap\": $delay, \"read_seconds\": 2.0}"
  t=$a a=$b b=$t
done
```

Two things the inputs have to satisfy, or the measurement reads as a success
that means nothing:

- **Every run must be a real switch.** Setting B to the input it is already on
  is a no-op, and `STA` will show the expected value at every delay whether
  the device has settled or not.
- **Both inputs must be linked.** Routing B to an input with no signal is
  reported as "no display" with no input number at all (fixture
  `09_spob no display_status.bin`), so there is nothing to compare against.
  Check `GET /status` first and pick two inputs whose `linked` is true.

Answers: the shortest delay at which `STA` reports the input just commanded.
That is the settling time, and it is distinct from the reply latency the other
captures measure -- the device acknowledges a command well before its own
status text reflects it.

A run whose `STA` still shows the old input is the failure being measured, not
a broken capture; record those too, since the boundary is the answer.

Afterwards, restore the routing noted above and unset the flag.

## What the captures feed into

Decisions already made; the captures inform the reader's tolerances, not these:

- Drop `quick` entirely. The invariant is that no command returns while its
  reply is unread — `quick` is the only thing that breaks it, and `/apply` is
  what makes holding it affordable for swaps.
- `POST /apply` takes the same body as `outputs` in `GET /status`, e.g.
  `{"A": {"mode": "single", "input": 2}, "B": {"input": 4}}`, plus a shorthand
  `{"A": 2, "B": 4}` meaning the same thing.
- `copy_a` stays out of it: `SPOBCOPYOUTAON/OFF` is non-idempotent and
  sometimes silent, so it does not batch cleanly.
- Keep an inter-command gap as a pacing knob, defaulting to 0.1.
- The reader resynchronizes by echo rather than by timing: loop
  `read_until(RESPONSE_TERMINATOR)` against one overall deadline, accumulate
  every chunk, and tick off each command whose `<s>CMD</s>` appears anywhere in
  the accumulation. Matching against the accumulation rather than per chunk
  makes it insensitive to ordering and to interleaving at chunk boundaries.
  Treat `<s>?</s>` as terminal too, or a bad command spins until the deadline.
- Use a monotonic deadline and shrink `self._serial.timeout` toward it before
  each read. pyserial's timeout is per read call, so a reply that never comes
  otherwise costs a full second per outstanding command.

The settling time from capture 5 sets two numbers that are wrong today:

- **A floor on the first post-command device read.** A read issued before the
  device has settled returns the pre-command routing, which reads as a failed
  switch. `cache.invalidate()` currently guarantees the next `/status` goes to
  the device, so the earliest possible read is exactly the one most likely to
  be wrong.
- **`STATUS_CACHE_TTL`, which is currently 1.0s against lrc's 300ms
  verification interval.** lrc's `verify()` (`internal/matrix/module.go:435`)
  polls immediately and then every 300ms for 5 attempts, but the first read
  completes at roughly 300ms and is then cached for a second, so attempts 2
  through 4 are all served that same too-early reading. Five attempts yield
  two device readings, four of them evaluated against a reading taken before
  the switch could have landed. The TTL needs to be shorter than the verify
  interval, or `/status` needs a `max_age` parameter so a caller can say how
  fresh a reading it needs. The cache is there to collapse concurrent clients,
  not to throttle one, and lrc's 3s/30s poll intervals -- not the TTL -- are
  what keep the device from being hammered.

Deliberately not being built yet, both discussed and deferred:

- **Optimistic "switching_to" state in `/status`.** lrc refuses to infer state
  from sent commands on purpose (`internal/matrix/module.go:58`); that refusal
  is what makes the device's silent command drops surface as `CodeIgnored`. A
  prediction in the status response would make `verify()` match on its first
  poll every time and destroy that detection. If it is ever built, the
  observation stays the primary value and the prediction hangs off it as a
  clearly additive annotation -- never the other way round. Note also that
  `U`/`D` step inputs are unpredictable, and no command predicts `resolution`.
- **Coalescing or debouncing rapid commands.** The lrc agent already
  serializes every command through `execMu` and holds it across verification,
  so there is one HTTP client and the race needs two. Worth building when a
  second appears -- as coalescing (keep the newest desired state, apply it when
  the port frees), not debouncing, since dropping the newest command discards
  exactly the one the person wanted.

Worth building alongside `/apply`, in this order:

1. `observed_at`/`age_ms` and a monotonic version on `/status`. Purely
   additive, and the prerequisite for the next item. No client can currently
   tell how old a reading is.
2. `if_version` on `/apply`, rejecting with 409 on mismatch. `OpSwap`
   (`internal/matrix/module.go:378`) computes absolute commands from lrc's
   cached view of both inputs, and the device does not announce front-panel or
   IR remote changes, so that view can be up to 30 seconds stale while idle.
   A swap issued in that window writes stale values to both outputs. Version
   matching narrows the window to under a second; it cannot close it, which is
   the right place to stop for this device.

Then on the lrc side: `internal/matrix/backend.go` gets one call for the
combined set, and `OpSwap`, `OpSetInput`, `OpSetMode` and `OpApply` in
`internal/matrix/module.go` each collapse to a single step. FastAPI ignores
unknown query params, so the existing `?quick=true` keeps working against the
new server and the Python side can ship first.
