# OCR Queue Runners (dual-Mac, dev + prod)

Two Macs drain both OCR job queues (`upload-images-dev-ocr-queue` and
`upload-images-prod-ocr-queue`) on a schedule, so re-OCR jobs queued by the
agentic review loop get processed without anyone babysitting a terminal.

These Macs are the **only** Vision OCR consumers that exist — there is no
cloud worker. Before the prod agents were added (2026-08-04) nothing had ever
drained the prod queue, and its REGIONAL_REOCR jobs sat PENDING forever.

| Machine | Hostname | Checkout used for the binary | dev schedule | prod schedule |
|---|---|---|---|---|
| MacBook Pro | `Tylers-MacBook-Pro` (local) | `/Users/tnorlund/Portfolio/.claude/worktrees/backfill-main` | `:00 :15 :30 :45` | `:05 :20 :35 :50` |
| Mac mini | `Tylers-Mac-mini`, `mini` (ssh alias) | `/Users/tnorlund/ocr-runner-main` | `:07 :22 :37 :52` | `:12 :27 :42 :57` |

The offset schedules interleave the two machines so they do not contend for the
same SQS messages, and stagger dev from prod on each machine so a host rarely
runs two drains at once (the per-env locks make an overlap harmless, just
slow).

`StartCalendarInterval` is used rather than `StartInterval`, because launchd has
no way to phase-offset an interval timer: the phase of a `StartInterval` agent
depends on when it happened to be loaded and drifts across reboots. Fixed
minutes are the only way to hold a durable 7-minute offset between the machines.

## What runs

Both machines run the same command through per-env wrapper scripts (the prod
agent is identical except `--env prod`):

```bash
receipt-ocr --env dev --continuous --log-level info
```

`--continuous` means "process until the queue is empty, then exit". Each
scheduled start is a fresh process, so a wedged run dies with its own
invocation instead of being resurrected forever. That is also why the agents set
`KeepAlive` to false.

## Files

| Purpose | Path (same on both machines) | Canonical copy in this repo |
|---|---|---|
| Wrapper script (dev) | `~/receipt_ocr_runner/run-dev.sh` | `scripts/ocr_runner/run-dev.sh` |
| Wrapper script (prod) | `~/receipt_ocr_runner/run-prod.sh` | `scripts/ocr_runner/run-prod.sh` |
| LaunchAgent (dev) | `~/Library/LaunchAgents/com.tnorlund.receipt-ocr-dev.plist` | `scripts/ocr_runner/com.tnorlund.receipt-ocr-dev.{macbook,mini}.plist` |
| LaunchAgent (prod) | `~/Library/LaunchAgents/com.tnorlund.receipt-ocr-prod.plist` | `scripts/ocr_runner/com.tnorlund.receipt-ocr-prod.{macbook,mini}.plist` |
| Log | `~/Library/Logs/receipt-ocr-{dev,prod}.log` | — |
| Overlap lock | `/tmp/receipt-ocr-{dev,prod}.lock` (directory, created atomically) | — |

The wrappers are byte-identical on both machines: each picks the first checkout
path that exists on the host, so there is nothing per-machine to keep in sync.
The two envs' wrappers differ only in the env flag, lock, and log; the plists
differ only in their four scheduled minutes. Both envs run the **same binary**
from the same checkout — `--env` selects the Pulumi stack outputs (queue URLs,
Dynamo table) at startup, so one `update_ocr_workers.sh` run refreshes dev and
prod alike.

## Updating after a merge

**The binaries do not auto-update.** The agents run a binary built from a
checkout pinned to `main`, and nothing rebuilds it. After merging anything under
`receipt_ocr_swift/`, the workers keep draining the queue with stale code until
you run:

```bash
./scripts/update_ocr_workers.sh
```

For each host this fetches `origin/main`, re-pins the checkout, runs
`swift build --configuration release`, proves the new binary runs, and
kickstarts the LaunchAgent.

| Flag | Effect |
|---|---|
| `--host local\|mini\|both` | Which worker to update (default `both`) |
| `--dry-run` | Print what would run, change nothing |
| `--skip-build` | Re-pin and kickstart without rebuilding |

The script is idempotent — re-running it on an up-to-date machine reports
`already current`, rebuilds in a few seconds, and kickstarts again. It exits
non-zero if any host fails, and prints the last 20 lines of build output when a
`swift build` breaks.

The checkouts are detached worktrees, so the script uses
`git fetch` + `git checkout --detach origin/main` rather than `git pull`. That
preserves untracked files and *refuses* rather than clobbering if someone left
tracked edits on the box.

## Two gotchas that will silently break this

**PATH.** `--env dev` resolves queue URLs, the Dynamo table name, and the
LayoutLM model bucket/key by shelling out to `/usr/bin/env pulumi stack output`.
`PulumiLoader.loadOutputs` returns an empty dict on any non-zero exit rather
than raising, so if `pulumi` is not on `PATH` the worker starts up with no queue
URL and quietly does nothing. launchd's default `PATH` does not include the
pulumi install, so the wrapper exports it explicitly. The install location
differs per machine — `~/.pulumi/bin/pulumi` on the MacBook,
`/opt/homebrew/bin/pulumi` on the mini — so the wrapper puts both on `PATH`.

**Working directory.** The LayoutLM CoreML bundle is cached at the relative
path `.models/layoutlm`. The wrapper `cd`s into `receipt_ocr_swift` first so the
~400 MB model is downloaded once and reused, rather than re-fetched from S3 into
whatever directory launchd happened to pick.

## Xcode vs Command Line Tools

Both machines run the same Swift toolchain version (6.3.3), but they get it from
different places:

| Machine | `xcode-select -p` |
|---|---|
| MacBook Pro | `/Library/Developer/CommandLineTools` |
| Mac mini | `/Applications/Xcode.app/Contents/Developer` |

Command Line Tools is enough to build and run this CLI, so the MacBook is fine
as a runner. **Do Swift development on the mini**: only it has full Xcode, and
therefore simulators, Instruments, `docc`, and anything that needs an SDK beyond
the macOS one. A change that builds under CLT on the MacBook can still fail on a
machine that resolves a different SDK, so treat the mini's build as the
authoritative one.

## Install (first time on a new machine)

```bash
mkdir -p ~/receipt_ocr_runner
for env in dev prod; do
  cp scripts/ocr_runner/run-$env.sh ~/receipt_ocr_runner/run-$env.sh
  chmod +x ~/receipt_ocr_runner/run-$env.sh
  cp scripts/ocr_runner/com.tnorlund.receipt-ocr-$env.mini.plist \
     ~/Library/LaunchAgents/com.tnorlund.receipt-ocr-$env.plist
  plutil -lint ~/Library/LaunchAgents/com.tnorlund.receipt-ocr-$env.plist
  launchctl load -w ~/Library/LaunchAgents/com.tnorlund.receipt-ocr-$env.plist
done
launchctl list | grep receipt-ocr        # confirm both registered
```

Pick the plist whose schedule that machine should own, and give the machine a
checkout pinned to `main` (`git worktree add --detach ~/ocr-runner-main origin/main`).
Do not point a runner at a checkout anyone works in: `~/Portfolio` on the mini
carries ~100 uncommitted modifications, and pointing the runner there would
either fail to update or clobber work in progress.

Kick off a run immediately instead of waiting for the next quarter hour:

```bash
launchctl kickstart -k gui/$(id -u)/com.tnorlund.receipt-ocr-dev
```

Installing is idempotent: re-running `load -w` on an already-loaded agent is a
no-op, and the wrapper's lock directory makes a double start harmless.

Note that `launchctl load` does **not** run the job, and `RunAtLoad` is false —
so a freshly loaded agent sits at `runs = 0` until the first scheduled minute.
`launchctl list | grep receipt` showing the label is *not* evidence it has ever
run; check `launchctl print gui/$(id -u)/com.tnorlund.receipt-ocr-dev` for
`runs =` and confirm the log file exists.

## Disable

```bash
launchctl unload -w ~/Library/LaunchAgents/com.tnorlund.receipt-ocr-dev.plist
```

To remove it permanently, delete the plist and `~/receipt_ocr_runner/`. If a run
was killed mid-flight the lock can survive; clear it with
`rmdir /tmp/receipt-ocr-dev.lock`.

**Do not touch the `actions.runner.tnorlund-Portfolio.*` agents** in the same
LaunchAgents directory. Those are the GitHub Actions self-hosted runners, they
are managed by `svc.sh`, and they are unrelated to OCR.

## Verifying it works

```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/681647709217/upload-images-dev-ocr-queue \
  --attribute-names ApproximateNumberOfMessages
tail -f ~/Library/Logs/receipt-ocr-dev.log
```

A healthy run logs `job_start` / `regional_reocr_crop_complete` / `ocr_run` /
`job_complete` / `sqs_delete_batch` lines per job and then exits when the queue
empties.

Vision OCR runs fine headless over SSH with the screen locked; no TCC prompt is
involved for image-buffer requests. This is confirmed on the mini.

### A failing job aborts the rest of the batch

If a single job throws, the worker prints `Error: NotFound: Not Found` and the
whole drain stops early rather than skipping that message and continuing. The
message then returns to the queue after its visibility timeout and blocks the
next drain the same way, on whichever machine picks it up. Symptom: queue depth
that will not go to zero, and both logs ending at the same `job_start`. Until
the worker isolates per-job failures, find the offending `image_id` in the log
and deal with that message directly.

### Reading job status in DynamoDB

Check the `status` attribute, not the GSI partition. `OCRJob` rows never
rewrite `GSI1PK`/`GSI2PK` on a status transition, so a completed job still sits
in the `OCR_JOB_STATUS#PENDING` partition. `receipt_dynamo/entities/ocr_job.py`
documents this and tolerates the stale partition on read. Querying GSI1 for
`OCR_JOB_STATUS#PENDING` therefore massively overcounts pending work — in dev it
returns ~1250 rows against a queue depth of a few dozen. The SQS queue depth is
the honest signal for "how much work is left".
