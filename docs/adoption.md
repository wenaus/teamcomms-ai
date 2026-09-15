# Staged adoption and acceptance

The SWF pilot uses devcloud accounts and tokens, the monitor's authoritative
database and ASGI deployment, and the public `/prod/teamcomms/` namespace.
Installation, backup, upgrade, rollback and removal procedures are collected in
[operations](operations.md). Component guides remain the interface references.

## Adoption boundaries

| Consumer | Selected transition | Retained path and rollback |
|---|---|---|
| SWF browser | System menu links to Entries, Pouch, Inflight and Capcom through devcloud login | Existing monitor navigation/authentication stays; disable only the TC link/mount if needed and preserve schema/history |
| AI sessions | Explicit receiver attachment or wrapped future launch, one native session at a time | Keep TJAI/native runtime and settings; stop only the selected TC receiver to detach; retain its cursor/outbox |
| Dialog/bootstrap | Opt-in capture from EOF and bounded history with explicit guidance IDs | Keep original transcripts and source IDs; stop the recorder independently; no automatic personal TJAI import |
| Watcher | One selected existing SWF completion notice with stable source/event identity | Existing subscriptions, production jobs and publisher unchanged; preserve the frozen event and outbox on retry |
| Mattermost | Dedicated bridge to `main/epicprod-live` on `chat.epic-eic.org` | Existing bot credentials stay on SWF; stop only the dedicated bridge; retain post mappings and source cursor |
| Work/resources | Explicit Inflight offers and configured cooperating resource guards | Accountable owner and held claims remain; reconcile stopped work before transfer; raw shell commands remain outside guard coverage |
| Execution | Explicit reviewed profiles; one read-only command worker commissioned | No default daemon or paid model launch; stop/drain selected workers and retain their execution journals |
| Personal TJAI consumers | Retain current source until each affected owner selects a transition | No broad history import, wholesale replacement, dual publication or deletion of existing records |

Host owners coordinate each attachment and deployment. On 2026-09-15, macOS
participation used the current mac-1 session. The departed mac-3 session was not
restarted. The selected Linux Claude session permitted a separately managed TC
receiver and requested its removal after commissioning; its native runtime and
TJAI bridge stayed running. The SWF host reported no deployment conflict and
retained its existing coordination path. No production deployment was needed
for the operating guides and rehearsal tools.

For SWF backups, the owning procedure is monitor
`docs/PRODUCTION_DEPLOYMENT.md`, **Database Backups**, and
`scripts/backup-swfdb.sh`. The host maintainer reported the scheduled invocation
from the `wenauseic` crontab at 01:30, using the current release script and
`/data/swf-shared/db-backups/` with `backup.log` and `cron.log`. This identifies the
operational authority; the stage-15 rehearsal did not run or certify a production
backup. Restore commissioning below used synthetic data in an isolated database.

## Independent installation and recovery evidence

On 2026-09-15, wheels from TC `aadf1d6` and `997796a` were installed into separate
fresh Python 3.11 environments. Both dependency checks passed. Imports resolved
to each environment's `site-packages`, with no TJAI checkout or configuration in
the service environment. The isolated PostgreSQL server accepted private Unix
socket connections only; no production database credentials were loaded.

The old release created a synthetic team, document revisions 1 and 2, canonical
Pouch, work referencing document revision 1, and a pending message to an offline
session. The candidate applied Inflight migrations `0006_executionrun` and
`0007_execution_integrity`. All 39 pre-existing component tables retained their
row counts and content hashes; the candidate added one empty execution table.

A full candidate dump restored into a separate database reproduced all 40
component table snapshots. Authenticated reads retained identity, both document
revisions, attributed Pouch export, work ownership and the pending delivery.
PostgreSQL rejected revision modification and Pouch rebinding after restore.
A separate restore of the pre-upgrade dump under the old wheel reproduced the
original tables and API reads, establishing the selected rollback path.

The Linux Claude receiver then stopped cleanly at the session owner's request.
Its saved SQLite database was backed up using SQLite's backup API. Metadata,
inbox and outbox matched exactly; integrity was `ok`, cursor remained 1, and no
pending dispatch remained. The native Claude process remained present. The
backup was not reattached to a native session, avoiding duplicate delivery.

Private evidence locations on the coordinating host:

- `/tmp/tc-recovery-20260915-b/evidence.json`: original rehearsal results,
  full table fingerprints, imported environments and authenticated reads.
- `/home/admin/.local/state/teamcomms-swf/acceptance/stage15-recovery/`:
  retained rehearsal evidence, dumps, wheel checksums and environment inventory.
- `/home/admin/.local/state/teamcomms-swf/adoption-ec2-1/`:
  stopped receiver log, state backup and recovery evidence.

## Measured multi-host delivery

One explicit notification, `3b09a0b2-aa95-4453-9024-5f00628eb27f`, was committed
at `2026-09-15T11:29:32.134209Z`. Its synthetic observation timestamp preceded
commit by 0.300 seconds. The service created three independent destination
records. Each receiving session reported consideration and acknowledged once.

Times below are seconds after server commit. Transport reservation is an upper
bound on receiver arrival; the native-report timestamp includes the return trip
to the server. Claude exposes completed socket writes, without a distinct client
acceptance RPC. Consideration uses the separate model acknowledgment timestamp.

| Session | Transport reserved | Native write/acceptance reported | Consideration reported | Native scheduling |
|---|---:|---:|---:|---|
| Linux Claude ec2-1 | 0.796 | 1.176, written | 11.083 | Idle arrival started a turn |
| macOS Claude mac-1 | 0.972 | 1.324, written | 7.088 | Idle arrival started a turn |
| Linux Codex ec2-2 | 0.767 | 1.228, accepted | 27.772 | Active `turn/steer` |

Claude versions were 2.1.272 on both hosts; the macOS connector remained at
`97c3fa8`. The Linux connectors used `997796a`. The three sessions used the same
existing devcloud acceptance AI participant, with distinct native/TC session
identities. This demonstrates cross-provider/session routing, not independent
human account enrollment or multiple-operator authorization. The existing SWF
program and Mattermost connector use separate verified participant kinds.

Native reporting met the five-second healthy-delivery target for this sample.
Model scheduling and acknowledgment took longer and are not transport latency.
This single sample is not a percentile/SLA measurement and does not measure a
watcher's source-detection interval. Transport bookkeeping ran in code. The
marker started two idle Claude turns and supplied input to the active Codex turn;
each model then issued its acknowledgment through the existing tool path.

Evidence is retained in
`/home/admin/.local/state/teamcomms-swf/acceptance/stage15-delivery/` as the frozen
request, publication response and original per-destination receipt histories.
The diagnostic detail on a transport receipt can still say consideration was
unconfirmed at write time; its later `considered_at` is the independent record.

## Design acceptance coverage

| Case | Evidence and remaining boundary |
|---|---|
| Independent operation | Fresh installed wheels, standalone authenticated document/work/message operations, upgrade and exact restore; no personal-system runtime dependency |
| Cross-provider/proactive delivery | Sep-15 timed Linux/macOS Claude and Linux Codex exchange; Sep-14 Linux/macOS Codex busy/idle and receiver recovery recorded in [connectors](connectors.md#verification) |
| Prompt presentation | Transport/consideration measured separately; stage-13 record-only routine notice produced no native invocation; Capcom attention remains opt-in |
| Documents/Pouch/editing | Stages 08–10 editor, stale-write, surgical/bulk and permanent binding evidence; restore retained fixed revisions and rejected forbidden updates |
| Dialog/bootstrap | Stage-06 native fresh-session recovery with explicit guidance and EOF gap; recorder restart retained IDs without duplicate capture; capture remains opt-in |
| Delegation/ownership/current state | Existing SWF work retained accountable owners through completion; stages 11–14 verified handoff/generation/claim results; no message grants production authority |
| Reservations/fallback | Stage-12 cooperating command guard and stage-14 interactive/headless race, renewal, timeout and saved-result recovery; raw commands and escaped daemons remain outside enforcement |
| Capcom | Existing topic links preserve Pouch revision, Dialog and completed work; public reference reads remain available |
| Mattermost | Existing real SWF event and threaded TC reply verified in the live feed with bot identity and loop suppression; live external human inbound remains unobserved |
| Recovery/access | Isolated full dump/restore/rollback, local SQLite backup, existing receiver recovery and host auth/revocation evidence; production disaster restore was not performed |

Linux Claude busy-turn behavior and fresh Claude bootstrap remain separately
unverified in live commissioning. Mac/Linux native socket APIs are version
dependent. Provider upgrades require a selected compatibility check before broad
rollout. Historical evidence is retained rather than relabeled as a current run.

The next adoption increment is selected by the affected owner: additional real
users/sessions, a live human reply in the configured channel, a specific Dialog
consumer, or an explicitly enabled worker profile. Each increment retains stable
IDs, local work and its rollback path. No installation-wide consumer migration
or automatic activation follows from this pilot.
