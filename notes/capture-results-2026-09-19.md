# Device capture results — 2026-09-19

Ran all five groups in `capture-plan.md`, plus repeated linked-input swaps
and 100 ms status queries to check the unexpected results. No application
implementation was changed. The automated suite passed: **48 tests**, with
two dependency deprecation warnings, under Python 3.14.2.

## Evidence and environment

Raw event JSON, lossless concatenated `.bin` replies, before/after status,
and the scripts used are in [the capture directory](test_fixtures/captures/2026-09-19/).
JSON preserves command-write timestamps, burst-start timestamps, and Base64
bytes; binary files alone do not preserve timing. Every binary file was
checked against its JSON, and every requested command had its expected echo.
These are recorded fixtures, not yet new automated regression tests.

The live container was `tv-commercial-detector-hdmi-matrix-control-1`, serving
port 11344, with the Portta checkout mounted at `/app`. Its four application
source files matched the local checkout by SHA-256; checkout HEAD was
`8d6e3c8121f60e2d7938deca20f9eebd4aeadcad`. The separate container named
`hdmi-matrix-control` was stopped and was not the service being tested.

Initial routing: A single/input 1, B input 4, copy-A off. Inputs 1 and 4 were
linked; 2 and 3 were not. Capture 5 explicitly set B to 4 before alternating
1/4, verified every run was a real switch, and checked both links before each
run. Otherwise its first command would have repeated capture 4's B1 routing.

## Planned captures

Times below are relative to the first command write. **Read timestamps mark
the first byte of a grouped burst, not reply completion or video settling.**
The capture implementation groups bytes until 20 ms of silence, or the next
write deadline, so one read event is not necessarily one response.

| Capture | Observations |
| --- | --- |
| 1: 100 ms pair | A acknowledgement burst at 6.1 ms; B written at 100.4 ms, acknowledgement burst at 320.3 ms. Both echoes received separately. B4 was already selected, so this alone was not a two-output switch test. |
| 2: zero-gap pair | Writes at 0.1/0.3 ms; both echoes in one 140-byte burst beginning at 3.4 ms. However, capture 3's subsequent status still showed A2 rather than the requested A1. |
| 3: B1 then STA | B acknowledgement at 1.7 ms, STA write at 100.7 ms, status burst at 321.7 ms. Correct B1, with A still at 2. The hypothesized delayed-set-reply-after-STA failure did **not** reproduce in this run. |
| 4: mode then B1 | Writes at 0.1/100.5 ms; both echoes in one 165-byte burst beginning at 338.1 ms, after the second write. This directly exercises the need to drain both outstanding replies. |

Capture 5 returned the newly commanded B input at **every** tested gap:

| Requested gap | Target B | First status byte | Status B |
| --- | --- | --- | --- |
| 100 ms | 1 | 343.1 ms | 1 |
| 150 ms | 4 | 376.8 ms | 4 |
| 250 ms | 1 | 335.7 ms | 1 |
| 400 ms | 4 | 413.5 ms | 4 |
| 600 ms | 1 | 613.9 ms | 1 |
| 1000 ms | 4 | 1015.4 ms | 4 |

The 100 ms status reply split into a 1-byte event and a 1748-byte event.
Accumulate bytes across reads; event boundaries are not response boundaries.
No old-input status was observed, so this sweep did not locate a settling
failure boundary. In particular, a query *sent* at 100 ms but *answered* around
343 ms does not prove the device was already settled at 100 ms.

## Repeats: acknowledgements do not prove application

Each linked-input swap started from verified A1/B4, then requested A4/B1:

| Gap | Repeats | Both echoes received | Final A4/B1 |
| --- | --- | --- | --- |
| 0 ms | 3 | 3/3 | **0/3** — all ended at A1/B1 |
| 100 ms | 3 | 3/3 | **3/3** |

The repeated zero-gap result confirms a routing failure even when neither
acknowledgement is missing. It does not establish the device's internal cause.
Retain pacing and independent status verification. The plan's proposed test
of counting echoes alone would miss this failure.

Four more B switches at a 100 ms STA gap, alternating directions with A in
single mode, all returned the requested input. Status bursts started at
284.9–293.1 ms. Combined with the planned sweep, 100 ms worked in all five
capture-5-style trials; this is a small sample, not a worst-case guarantee.

## Recommended next steps

1. Implement `/apply` and remove `quick`, keeping the **100 ms inter-command
   gap**. Drain and match every expected echo across accumulated bytes under
   one monotonic deadline; handle rejection and missing replies explicitly.
   Preserve status verification because even a full set of echoes can mask
   an unapplied command. The current 1-second serial budget is a reasonable
   initial overall reader budget for these pairs, not a measured maximum.
2. Turn these recordings into reader regression tests: separately delayed
   replies (01), merged replies after the second write (04), split status
   bytes (05 at 100 ms), and successful echoes with wrong observed routing
   (06 zero-gap, for verification behavior). Cover stale/unexpected echoes,
   `<s>?</s>`, and missing replies synthetically; the requested capture 3
   failure was not observed and should not be described as reproduced.
3. Fix status freshness: use a cache TTL below lrc's 300 ms verification
   interval (for example 200 ms), or add caller-controlled `max_age`. Retain
   single-flight reads. Add `observed_at`, `age_ms`, and a monotonic observation
   version, then `if_version`/409 on `/apply`. Those changes are justified by
   the existing cache/client behavior, rather than a stale-status failure
   reproduced here.
4. Do not invent a longer settling floor from this sweep. Retain the tested
   100 ms command-to-STA pacing initially; characterize shorter gaps, repeated
   mode changes, and unstable links if a minimum or worst-case bound is needed.
   Recheck this when removing `quick`, since an early acknowledgement could
   otherwise let the next status write occur earlier than the tested 100 ms.
5. Integrate one combined backend call into lrc's swap/set/apply operations,
   preserving observation-based verification. Ship Python first, then lrc.

## Cleanup and test command

Restored A1/B4 with copy-A off after each capture batch and verified it again
after cleanup. Removed the temporary hardcoded
`HDMI_MATRIX_ENABLE_RAW_COMMAND=1` line from the live deployment's
`/home/joe/Code/projects/tv-commercial-detector/docker-compose.yml`, then
recreated only its `hdmi-matrix-control` service with `--no-deps --no-build`.
The original Compose file was backed up on gmktec at
`/tmp/portta-capture-2026-09-19-compose.before.yml`.
Health returned 200/OK and `/debug/raw-command` returned 404. The post-restart
status retained the original routing but reported input 1 unlinked; signal
availability is separate from routing. See `cleanup-verification.json`.

Automated suite command (temporary dependencies, no project environment edits):

```sh
PYTHONDONTWRITEBYTECODE=1 UV_CACHE_DIR=/tmp/portta-test-uv-cache \
  uv run --no-project --with pytest --with fastapi --with httpx \
  --with pyserial --with uvicorn pytest -p no:cacheprovider
```

The warnings concern Starlette's deprecated httpx TestClient integration and
AnyIO BlockingPortal alias. `capture-plan.md` remains because `/apply` has
not yet been implemented.
