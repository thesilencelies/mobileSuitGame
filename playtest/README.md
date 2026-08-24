# NetFrame playtest app

Play NetFrame against the AI on your phone. It runs **entirely on the device**
— the rules engine, the AI and the web client are all local, the server binds
loopback, and nothing it needs is on a network. Works on a train, on a plane,
in a basement.

There is no build step, no npm, no bundler, no CDN, and — on the shipped path —
**no third-party Python packages at all**. The HTTP layer is `http.server` plus
`json`, because that is what makes `pkg install python` a complete install on
Android.

```
playtest/
  engine/    rules engine              (workstream B)   stdlib only
  ai/        the AI seat               (workstream D)   stdlib only
  server/    HTTP layer + web client   (workstream C)   stdlib only
    httpd.py     http.server plumbing            <- what ships
    routes.py    the routing table
    app.py       optional FastAPI adapter        <- desktop dev only, never required
    static/      index.html, app.css, js/*.js, cards/*.jpg
  netframe.sh  one-tap launcher
  tests/
```

---

## On Android, with Termux

### 1. Install Termux

Get it from **F-Droid**, not the Play Store — the Play Store build is
abandoned and years out of date.

<https://f-droid.org/packages/com.termux/>

Optional but worth it:

* **Termux:Widget** (also F-Droid) — the home-screen one-tap launcher.
* **Termux:API** + `pkg install termux-api` — lets the launcher open your
  browser and hold a wake lock so Android does not suspend the server while you
  are reading a card.

### 2. Install Python

```bash
pkg update
pkg install python
```

That is the whole dependency list. No `pip install`, no wheels, no compilers.
(This matters: FastAPI needs pydantic, whose core is a compiled Rust extension
with no Android wheel — it would have to be built from source on the phone, if
it built at all. That is why the shipped server is stdlib.)

### 3. Copy the repo across

The app reads the card data from the repo-root CSVs at runtime, so `playtest/`
alone is not enough. Copy:

| Path | Why | Size |
|---|---|---|
| `playtest/` | the app, including `server/static/cards/` | ~3.5 MB |
| `*.csv` at the repo root | card, frame and terrain data the engine parses | ~48 KB |
| `decks/` | deck lists, terrain decks, objective decks | ~140 KB |

**About 3.7 MB in total.** You do **not** need `AllCardImages/` (24 MB of
print-density PNGs) — `playtest/server/static/cards/` already holds every card
at phone size. Nor `build/`, `pictures/`, `rules/` or `simulation/`.

Simplest is to bring the whole repo minus the art:

```bash
pkg install git
git clone <your repo url> mobileSuitGame
```

or, over USB / a cable / a share, copy the folder and delete `AllCardImages/`
on the phone. Termux can read your Downloads folder after
`termux-setup-storage` (`~/storage/downloads`).

> `playtest/server/static/cards/` is **generated** but must travel with the app
> — do not add it to `.gitignore`, or a `git clone` on the phone will arrive
> with no card art.

### 4. Run it

```bash
cd mobileSuitGame
python -m playtest.server
```

```
NetFrame playtest — http://127.0.0.1:8000/
  Ctrl-C to stop.
```

Open that URL in Chrome/Firefox on the phone. Then **Add to Home Screen** from
the browser menu: it gets an icon and opens fullscreen with no browser chrome,
which is the difference between a web page and something that feels like a
game.

### 5. One tap

```bash
mkdir -p ~/.shortcuts
ln -s ~/mobileSuitGame/playtest/netframe.sh ~/.shortcuts/netframe
chmod +x ~/.shortcuts/netframe
```

Add the **Termux:Widget** to your home screen and pick `netframe`. One tap
starts the server, waits for the port, opens the browser at
`http://127.0.0.1:8000/` and takes a wake lock. Set `NETFRAME_PORT` to use a
different port.

If the widget refuses to run it, edit the shortcut to say `sh
~/mobileSuitGame/playtest/netframe.sh` — Termux has no `/usr/bin/env`, so
shebangs are the usual culprit.

### Battery and lifecycle notes

* Android may kill the Termux process when it is backgrounded for a long time.
  Termux:API's wake lock (which the launcher takes automatically) makes that far
  less likely; disabling battery optimisation for Termux removes it entirely.
* Games live in memory. Stopping the server loses them. The client remembers the
  last game id and offers **Resume last game**, which works as long as the
  server has not been restarted.

---

## On a desktop

```bash
python -m playtest.server            # http://127.0.0.1:8000/
python -m playtest.server --port 9000 --quiet
```

Optional development extras (never required):

```bash
pip install fastapi uvicorn
python -m playtest.server --dev-server     # FastAPI + uvicorn, auto-docs
```

`--host 0.0.0.0` exists and prints a warning. The supported configuration is
loopback: there is no authentication and no encryption, so do not expose it.

---

## Using the client

Four tabs along the bottom, all reachable one-handed with a thumb:

| Tab | What it is |
|---|---|
| **Board** | The 15×16 battlefield. |
| **Plan** | Your hand, and the two actions you are committing face down. |
| **Initiative** | Every committed card in initiative order, highest first. |
| **Log** | The full event log, newest first. |

The **decision sheet** at the bottom always shows what the engine is waiting
for. Drag its handle to collapse it when you want more board.

### The board on a phone

15×16 tiles do not fit a phone at a readable size, so the board is a `<canvas>`
with its own camera rather than a table the browser scrolls:

* **FIT** shows all 240 tiles at once, about 24 px per tile — enough to read the
  shape of the fight.
* **One finger pans, two fingers pinch.** **Double-tap** toggles between FIT and
  a tactical zoom centred on the tile you tapped, at which a tile is a
  comfortable finger-width — so a tile is always big enough to hit when you
  actually have to tap one.
* A **minimap** appears bottom-right whenever you are zoomed past FIT, showing
  the viewport rectangle, the terrain and every frame, so you cannot get lost.
* **◎** centres on whichever frame is acting; **FIT** always gets you back.
* Elevation is a lightness ramp with a lit top edge and a digit; impassable
  tiles are crossed out; obstacles are blocks; objective tiles are outlined
  gold; tokens are discs with their remaining HP.

Tapping:

* a **frame** selects it, with damage, statuses and cards in the readout;
* a **green tile** during a `move` decision moves there — green tiles are the
  engine's own `pending.options`, never a client guess about reachability;
* a **red pulsing frame** during an `attack_target` decision attacks it.

Drawer toggles: line-of-sight shading for the selected frame, enemy reach
shading, terrain-card outlines, tile coordinates.

### What the UI is honest about

* **26 cards have unimplemented text** — all 24 pilot cards and both drone
  cards. They load, block and deal damage correctly, but their printed effect
  does nothing in v1. Each carries a `TEXT NOT IMPLEMENTED` ribbon on its
  thumbnail, a warning in its detail view, and a note in the initiative list.
  Do not read a pilot card's text as active when judging balance.
* **Blocking is compulsory**, and each block option states whether the card is
  discarded (normal block) or kept (super block), and whether its own action is
  forfeit.
* The **initiative list shows printed values**. The engine also applies −1 for a
  High zone on its last hit and ∓2 for Stunned/Stimmed; those are not shown on
  the card.
* Your own face-down cards read *"face down — the AI cannot see it"*; the AI's
  are blank card backs, because the server never sends their identity.

## AI settings

AI parameters come from `GET /api/ai/params` and the controls are generated
from that response — the client hardcodes no parameter names. Workstream D
added parameters after this client was written and they appeared with no change
here. Difficulty presets show as chips. Changing values in the drawer and
pressing **Apply to this game** retunes the running AI immediately.

## HTTP API

```
GET    /api/health                 counts of cards, frames, decks, images
GET    /api/cards                  catalogue keyed by "{Group}_{Name}"
GET    /api/frames                 frame stats from Frames.csv
GET    /api/decks                  decks in decks/, with legality
GET    /api/ai/params              AI parameter schema
GET    /api/images                 card keys art exists for
GET    /api/card-image/{key}?w=240 card art, phone-sized (URL-encode the key)

POST   /api/game                   {seed?, playerDecks[], aiDecks[], framesPerSide?, aiParams{}}
GET    /api/games                  every live game
GET    /api/game/{id}              current redacted view for the human seat
DELETE /api/game/{id}              forget a game
POST   /api/game/{id}/command      {kind, payload{}} -> new view
POST   /api/game/{id}/undo         step back one human decision
POST   /api/game/{id}/ai-params    {aiParams{}} retune the AI mid-game
GET    /api/game/{id}/log          full event log
GET    /api/game/{id}/threat?frame=a0   reach + line of sight, public info only
```

`/?game=<id>` deep-links straight into a running game; `&view=board|plan|order|log`
picks the tab.

### Command payloads

| kind | payload |
|---|---|
| `commit_actions` | `{"uids": ["c17", "c22"]}` |
| `resolve_order` | `{"order": ["movement", "attack"]}` |
| `move` | `{"x": 7, "y": 12}` |
| `attack_target` | `{"kind": "frame", "id": "b1"}` |
| `choose_block` | `{"uid": "c04"}` |
| `effect_choice` | the option the engine offered, e.g. `{"mulligan": true}` |
| `echo_card` | `{"dead": "a2", "host": "a0"}` or `{"decline": true}` |

The flat form `{"kind": "move", "x": 7, "y": 12}` also works **except for
`attack_target`**, whose own payload has a `kind` field that collides with the
command kind — use the nested `payload` form (or the `targetKind` alias). The
client always uses the nested form.

An illegal command is a **400** with `{"error": "illegal_command", "detail":
"..."}`; an unknown game is a 404. Neither is ever a 500.

### The server loop

`POST /command` applies your decision, then keeps asking the AI for commands
while `pending` belongs to the AI seat, and only then returns. The view you get
back is always one you can act on — the client never polls or waits.

Every response goes through the engine's `view_for(state, seat)`. No endpoint
serialises a `GameState`, so none can leak the AI's hand, deck order or
face-down cards.

## Where the client talks to the server

`static/js/api.js` is the **only** module that touches the network — no view
calls `fetch` itself, and a test enforces that. If the engine ever has to run
inside the browser instead (Pyodide, a JS port), replacing that one file is the
whole job; everything else knows only its method names and the JSON shapes.

## Card art

Workstream E renders one PNG per card into `AllCardImages/`, named exactly
`{Group}_{Name}.png` — the same string as the engine's card `key`. Those are
378×537 print-density files, ~90 kB each, 24 MB for the set: four times more
pixels than a phone will ever draw and far too much to carry.

So the app ships **`playtest/server/static/cards/`**: the same 158 cards as
240 px-wide JPEGs, ~12 kB each, **2.7 MB total**. That is what the phone reads,
and it needs no ImageMagick to do it — bundled files are served untouched.

Regenerate after new art lands (needs ImageMagick, so do it on a desktop):

```bash
python -m playtest.server.images                 # rebuild changed cards
python -m playtest.server.images --force         # rebuild everything
python -m playtest.server.images --width 360     # bigger, for a tablet
```

If a card is missing from the bundle the server falls back to the original in
`AllCardImages/`, downscaling on demand when ImageMagick is available.
Filenames contain spaces, so clients must URL-encode the key.

Override paths with `NETFRAME_CARD_IMAGES` (originals), `NETFRAME_CARD_THUMBS`
(bundle) and `NETFRAME_THUMB_CACHE` (on-demand cache; defaults under the system
temp dir so nothing generated lands in the repo).

## Tests

```bash
python -m pytest playtest/tests -q
```

`playtest/tests/test_server.py` covers the endpoints, that `IllegalCommand`
becomes a 400, a whole game played through the API, the real socket layer, the
AI bridge's schema normalisation and its fallback, that the shipped modules
import nothing but the standard library, that the client references no external
host — and, above all, the hidden-information regression test, which re-checks
at every single decision of a complete game that the human's view never
contains the AI's hand, deck order or face-down card identities.
