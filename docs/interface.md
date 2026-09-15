# Browser interface

The authenticated TeamComms root opens Entries. Shared navigation also exposes
the canonical [Pouch](pouch.md), structured [Inflight](inflight.md) work and
read-only session and Dialog views. All pages,
assets, reads and writes use the installation's existing authentication boundary
and URL prefix.

## Editor

The editor adapts TJAI's CodeMirror 5.65.18 controls and document rendering.
Source is the canonical text. **Rendered** displays the current buffer without
rewriting it or resetting selection, scroll position or undo history. Returning
to Source restores the same editor. Plain-text paste preserves whitespace;
structured HTML paste uses Turndown for headings, lists, links and code. HTML
tables retain their table structure, including spanning cells, in the source.
The server sanitizes rendered HTML. Prism highlights code; KaTeX renders math.
These browser dependencies are bundled with their licenses, without CDN calls.

Controls cover bold, italic, headings, lists, links, tables, fenced code,
indentation, dedent, undo/redo, literal search and replacement of a selected
match. Enter continues list markers. Ctrl/Cmd S saves, Ctrl/Cmd F opens editor
search, Ctrl/Cmd B/I format and Ctrl/Cmd K inserts a link. Escape then Tab leaves
the editor. The source pane is vertically resizable. Theme selection supports
system, light and dark; theme, source/rendered view and autosave preference
persist in the browser for the installation path.
The saved theme is applied by a blocking same-origin script before styles and
the editor load, so navigation does not briefly render the light theme. System
preference is the fallback when no valid saved preference exists.

Entries can be searched, opened by permanent UUID link, created with a readable
slug, edited, archived and inspected by revision. The editor writes title,
content, tags, status and priority. Other metadata and relationships remain
intact. Existing Entries limits apply: 40,000 content characters and 48,000
encoded state bytes. Reads request the complete supported content size.

## Save and recovery

Typing saves a local recovery draft, namespaced by installation path, team,
participant, entry and browser tab. Drafts contain the loaded revision and base
fields as well as the working fields. Reload offers divergent unsaved drafts
for explicit recovery or download. Browser-storage errors remain visible and
download remains available. These drafts contain document text: the browser
profile must be appropriate for that team's data.

Save state distinguishes saved, saving, unsaved, conflicted and failed. Optional
autosave waits for a pause and applies only to existing entries. Creation is
explicit. A stable new-entry slug prevents duplicate creation after a lost
response; if its result is uncertain, search that slug before retrying.

Only one save runs at a time. The submitted buffer is snapshotted separately
from later typing; a successful save advances its base revision without replacing
newer local text. A network error retains the draft. Navigation while dirty
warns and retains recovery state. On page hiding or closing, the local draft is
retained; server delivery during unload is not assumed.

A stale save returns the existing revision conflict. The editor preserves the
buffer and displays the latest revision, author and time. **Compare with
latest** shows both texts and their unified diff. **Keep draft on latest
revision** explicitly advances the base after confirmation; subsequent saving
still validates that revision. **Load latest** retains a separate recovery copy
of the displaced draft. No automatic text union or overwrite occurs.

Version history is paginated and records authorship. Comparison reads a pinned
revision and compares it with the current buffer. Restoration requires a clean
buffer, confirmation and the current expected revision; it restores the complete
saved state as a new attributed revision. Historical rows are unchanged.

The Pouch opens at `/pouch` using this same editor. **Link to revision** and the
history links open `/pouch?revision=N` (or `/entries/<UUID>?revision=N`) read-only.
Pinned views disable saving, recovery into the buffer and restoration; **Open
current document** returns to the editable current revision. Pouch saves keep
the canonical route. **Export saved revision** downloads its attributable JSON
package; **Download draft** remains a separate copy of the local text.

## Host integration

Pass `browser_csrf_url` to `create_app` when the host supplies browser-session
CSRF tokens. It must be a same-origin absolute path. A credentialed, uncached GET
returns `{"header_name":"X-CSRFToken","token":"<masked host token>"}` and the
host sets its own cookie when needed. The frontend sends that header and
same-origin cookies on each POST. Host callbacks still validate the original
request; the endpoint supplies no new identity or permission. Tokens are not
stored in browser storage. The UI expects a host browser session; it supplies
no standalone credential issuance or browser bearer-token manager.

Pages are `/`, `/pouch`, `/entries`, `/entries/<UUID>`, `/sessions` and `/dialog` under the
configured mount. Assets are under `/assets/` in the wheel. The same authenticated
ASGI route serves them, including when a parent router supplies `root_path`.
The asset-serving layer needs no Django static alias or template setup.
Pouch requires its [Django app and migrations](pouch.md#initialization-and-integrity).
Proxies must preserve content type, CSP, nosniff and referrer-policy headers.
CSP permits only same-origin scripts and fonts, disallows active embedded
objects and limits framing to the same origin. Markdown images may use HTTPS.

Two read-only operations supplement the existing Entries API:

- `POST /api/entries/render`: `{content}` returns sanitized `{html}`.
- `POST /api/entries/compare`: `{entry_id, revision, content}` returns `{diff}`
  from the selected authorized revision to the supplied draft.

Both require `entries:read` and ordinary host CSRF for cookie POSTs. Rendering
never saves an entry. Comparison checks entry access through the shared service.

## Source and checks

UI controls derive from TJAI `tjai_app/templates/tjai_app/entry_detail.html`;
Markdown sanitization and linkification derive from `scripts/md_render.py`.
TeamComms retains its explicit revision semantics. See NOTICE and the bundled
`assets/licenses/` notices. `tests/check_interface.py` runs a bounded local
browser exercise against synthetic Entries responses; no production data or
PostgreSQL cluster is used. It covers rendering, source retention, failed saves,
concurrent edits, recovery, revision restoration and prefixed assets. Full-suite
runs require the separate approval specified in AGENTS.md.

## Structured work

[Inflight](inflight.md) at `/inflight` reuses this editor and its local recovery,
rendering and fixed-revision comparison. Live/Done lists show owner, state and
blockers; internal work has an explicit visibility filter. Pouch offers **Create
work from this revision**, which preselects a saved source reference without
creating anything until Save. Work controls record criteria, progress, executor
assignment, accepted handoffs, completion evidence and explicit reopening.
Generic Entries restore cannot alter work ownership or lifecycle.

## Topics and attention

[Capcom](capcom.md) at `/capcom` combines linked current work, fixed-revision
documents, attributed Dialog excerpts and immutable notices. Its attention,
following and all-topic filters share the existing theme and authentication.
Open decisions require an explicit resolution; topic and exact-message read
markers never acknowledge delivery. Canonical conversations preserve private
audiences and show each authorized destination's transport and consideration
state. Routine notices fold visually while retaining their original rows.

An owned-session panel edits opt-in routine presentation and quiet periods.
Unsupported receivers are labeled and retain immediate delivery. Alarms and
human instructions bypass routine policy after routing admission. New AI
notifications require the separate **Notify LLM** selection, regardless of severity.
Topic creation, reference edits and
notice publication retain stable mutation IDs for uncertain browser retries.
