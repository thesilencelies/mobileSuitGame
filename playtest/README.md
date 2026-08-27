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
| `playtest/` | the app, including its bundled art | ~4.1 MB |
| `*.csv` at the repo root | card, frame and terrain data the engine parses | ~48 KB |
| `decks/` | deck lists, terrain decks, objective decks | ~140 KB |

**About 4.3 MB in total.** You do **not** need `AllCardImages/` (24 MB of
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

> `playtest/server/static/cards/`, `.../terrain/`, `.../tokens/` and
> `.../frames/` are
> **generated** but must travel with the app — do not add them to
> `.gitignore`, or a `git clone` on the phone will arrive with no art.

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

Six tabs along the bottom, all reachable one-handed with a thumb:

| Tab | What it is |
|---|---|
| **Board** | The 15×16 battlefield. |
| **Plan** | Your hand, and the actions you are committing face down. |
| **Field** | The tableau: every card standing in front of every frame. |
| **Initiative** | Every committed card in initiative order, highest first. |
| **Goals** | The objective cards on this battlefield: what each one asks, in its own printed words, who brought it (and so defends it), what it is worth to each side, how it stands right now, and a button that centres the board on it. Objectives are about half the victory points on offer and the cards are face down on a table you cannot pick up, so this is the card. |
| **Log** | The full event log, newest first. |

Tapping a frame — on the board or in the strip along the top — opens its
read-out **in the ribbon above the board**, not over it: its frame card, its
ability text (which is live: Hector's free block, Adam's pierce initiative,
Kamikiri's bonus cut), its damage by zone and the cards in front of it. Tapping
it again closes it. A decision selects the frame it is about, but does not open
the panel — the board stays visible.

The **decision sheet** at the bottom always shows what the engine is waiting
for. Drag its handle to collapse it when you want more board.

### The board on a phone

15×16 tiles do not fit a phone at a readable size, so the board is a `<canvas>`
with its own camera rather than a table the browser scrolls:

* **FIT** shows all 240 tiles at once, about 24 px per tile — enough to read the
  shape of the fight.
* **ART** switches between two readings of the same board. With art on, the
  dealt terrain cards are drawn *behind* the grid, each clipped to its own 3×4
  tile block and turned 180° for the half its owner laid out facing themselves
  — the board as it sits on a table — and the objective tokens use their piece
  art, whose numbered variants *are* the damage state (`Tower4`…`Tower1`,
  `PowerPlant2`…`PowerPlant1`). With it off you get the same markings on a flat
  dark ground, which is still the faster read at FIT. Neither replaces the
  other.
* **One finger pans, two fingers pinch.** **Double-tap** toggles between FIT and
  a tactical zoom centred on the tile you tapped, at which a tile is a
  comfortable finger-width — so a tile is always big enough to hit when you
  actually have to tap one.
* A **minimap** appears bottom-right whenever you are zoomed past FIT, showing
  the viewport rectangle, the terrain and every frame, so you cannot get lost.
* **◎** centres on whichever frame is acting; **FIT** always gets you back.
* **Terrain is drawn the way the card prints it**, so a rooftop or a blocked
  tile looks the same on the phone as on the table. Everything comes from
  `terrain_cards.py` (`TERRAIN_STYLE = "full"`):
  * **Elevation** — the `cityblue!N!citysteel` colour ramp laid on at half
    opacity, so height reads as *blue* and the ground still shows through; the
    per-edge **walls**, drawn 3.5 pt wider for each level this tile stands above
    the neighbour across that edge, which is what fakes the perspective onto a
    building's flank; and the stacked-cube glyph in the corner.
  * **Impassable** — black at half opacity under a 5 pt *red* border, and a
    boxed X. A tile you cannot enter is not shaded like one you can.
  * **Obstacle** — a yellow crosshatch, **objective** — a green field of
    vertical lines, **token spawn** — a purple field of horizontal ones. All
    three are pgf patterns at `fill opacity=0.5`, so they show through each
    other where a tile carries more than one, and none of them sets a border —
    an obstacle standing on a rooftop keeps the rooftop's blue wall and reads
    as both at once, which is what the "should not set the line style cause
    they can appear at any elevation" comment in `terrain_cards.py` is
    protecting. The obstacle adds the card's dashed yellow outline on top.

  The glyphs are the card's own `icons/{e1,e2,e3,imp,obs,obj,tkn}.png`, bundled
  into `static/tiles/`; a tile carrying two of them stacks the second one width
  to the left, as the card does. The pt-to-tile conversions, the mixed colours
  and the xcolor base colours behind the hatches are hard-coded in `board.js`
  because a canvas cannot share TikZ's constants —
  `test_the_board_draws_elevation_the_way_the_card_does` and
  `test_the_board_hatches_a_tile_the_way_the_card_does` re-derive them from
  `terrain_cards.py` and are what stop the two drifting apart.
* **Terrain that hurts** carries no marking of its own: the Railway's rails are
  its `tkn` cells, marked exactly as the printed card marks them. Tapping the
  tile reads the rule out — "the rails: 1 energy Low at the end of a turn" —
  from `hazard` on the tile, which the engine writes; the client never restates
  a rule it could get wrong.
* Tokens are discs with their remaining HP — the one board marking that is the
  client's own, because nothing is printed for them.

Tapping:

* a **frame** selects it, with damage, statuses and cards in the readout;
* a **green tile** during a `move` decision proposes moving there — green tiles
  are the engine's own `pending.options`, never a client guess about
  reachability;
* a **red pulsing frame** during an `attack_target` decision proposes attacking
  it;
* a **blue tile** during setup proposes deploying the chosen frame there;
* an **orange tile** during a card effect marks somewhere to *put* something —
  see below.

### Putting things on the board

Barricade places up to three tokens, Portal two ends, an Attack Dog card two
dogs, a Gun Tower one within 3, and the objectives put their own tokens down
after deployment — the Fugitive anywhere in the enemy back row, Riverside's
three gangs anywhere off their creator's back row of cards, Car Park's three
refugees anywhere in the enemy half. All of them are answered on the board, in
**orange** rather than movement's green: the question is not "where do I go"
but "where does this go", and confusing the two costs an action.

Which colour a tile decision gets is the engine's answer, not a guess from the
option shape: `pickKind` on the decision is `"place"` (orange) or `"move"`
(green). A shove, a drone's move, a reflex step and a Teleport all send
something that is already on the board somewhere, so they are green.

The engine asks for one tile at a time, which would make Barricade three
separate confirmations. So each decision carries `pickMin`/`pickMax` — how many
tiles the effect still wants in total — and the client uses that to let you
mark them all first: each tap adds a numbered orange square, tapping a marked
one takes it back, and **Place 3** commits the set. `commitPlacements` then
walks the exchange, sending a tile and reading the decision that comes back. A
tile the engine stops offering ends the run rather than being forced through:
the engine's answer is the authority on what is legal, never the client's list.
Cards that place exactly one thing (a Teleport, a gravity well) keep the
ordinary tap-to-propose, tap-to-commit instead.

Barricade's "up to 3" also ships a `{done: true}` option alongside its tiles, so
the client must treat *some* options as tiles rather than requiring them all to
be — getting that wrong is what once turned the whole decision into an
unreadable list of raw grid coordinates.

### Who first, then where

"Move a frame within 5 up to 2" (Set the trap, Call of Nature) used to be one
list of every (frame, destination) pair — dozens of rows of raw coordinates on
a 15x16 board, and nothing the map could show. It is two questions now: which
frame, then that frame's own reachable tiles in movement green. With only one
frame in range it skips straight to "where".

### Which of your own actions goes first

"In the event of a tie the cards are resolved alternately, moving clockwise
from the player with the priority marker" (rules.tex:442) settles *whose* turn
it is and stops there. Which of one seat's own tied cards resolves first is the
player's call — and it matters, since closing the distance with one card can
put a target in reach of the other. So when the alternation lands on a seat
holding more than one tied card, the engine raises a `choose_actor` decision
instead of picking. `next_actors()` returns the whole tied set for that seat;
`next_actor()` is still there and returns the first of them.

### What is running on a frame

Statuses and persistent cards get pips along the top edge of a frame's tile.
Every status is ±2 to one stat, so the letter names the **stat** and the colour
says which way — `I` initiative, `C` cards drawn, `M` movement, green for the
buff of a pair and red for the debuff, so there is one thing to learn rather
than seven. `R` in gold is Revealed, which has no opposite. A violet `P` means
a card is still in play in front of that frame (Utter darkness, Fog of war);
the Plan tab names them.

### Tap twice to mean it

A move cannot be taken back, and a thumb on a phone is a much easier thing to
misfire than a mouse. So every board tap that spends something irreversible —
a move, an attack, a deployment — is **two taps**: the first proposes it and
shows you exactly what it would do (a gold ring on the tile, a panel in the
sheet saying "Move to (7, 12) · 3 movement"), the second, on the same thing,
commits it. **Cancel** and **Confirm** buttons in the sheet do the same job for
anyone who would rather press a button, and any other tap — selecting a frame,
reading a tile, changing the decision — throws the proposal away.

Taps that only *look* at something are still one tap. Making those cost two
would be the same disease.

### Setup: putting your squad on the board

Before turn 1 each player places their frames one at a time, alternating, on
the near edge of their own terrain. The engine offers the whole
frame × tile cross product in a single `deploy` decision, so the screen splits
it: the **sheet** picks the frame (a chip each, with its standee and its
movement) and the **board** picks the tile (blue = the engine's own options for
whichever frame is chosen). Placement is a two-tap confirm like everything else
irreversible. The AI answers between your placements, so you watch its squad
appear opposite yours as you build your own line.

Drawer toggles: terrain and piece art, line-of-sight shading for the selected
frame, enemy reach shading, terrain-card outlines, tile coordinates.

### Choosing a target

The target list is the read the game turns on, so it states, per zone, what the
defender can still cover:

* how many of the cards still standing in front of them block that zone, how
  many of those are **super** blocks (kept, not discarded), and which cards
  they are;
* how many **face-down** cards they have left — any of which might cover
  anything, which is why the count is given whole and never broken down by
  zone;
* which zones nothing they can see covers, summed up on the attack button
  ("2 zones uncovered").

All of it comes from the engine's own `combat.block_options`, so Close Quarters
barring already-resolved cards, and Guard Break letting one wide card cover
several zones at once, are the engine's answers rather than the client's guess.

### Watching the AI move

`POST /command` runs the AI until you are on the clock again, so one tap can
cover three frames moving, an attack, a compulsory block and a death. The
server records a snapshot at each **beat** of that and the client plays them
through the board at the pace you pick — **Instant / Brisk / Steady / Slow** in
the drawer, with a **Skip** button and an `n/total` counter on the playback bar.
The camera follows the action — the frame that took the damage, else the tile
that was moved to — unless you turn that off. Skipping always lands on exactly
the same state as watching, and **Instant** skips the playback entirely.

A beat is not the same thing as an AI *decision*. Plenty of what the AI does
needs no decision at all: a card with one legal target, an effect with no
choices, a card that only ever blocks. Those used to be folded silently into
the next decision's snapshot, which is the part that read as the AI acting off
screen. The engine is watched instead (`engine.watching`, a per-thread
observer) and a beat is recorded whenever an AI frame **reveals a card**,
**moves**, **resolves an effect** or **lands an attack** — as well as after
each AI decision. The human's own cards are deliberately *not* recorded: they
resolve under your own hand and are already on screen, and replaying them would
only put a delay between a tap and its result.

Each snapshot carries what *changed* since the one before it, because the marks
are a difference rather than a still:

| `beat` key | What it is |
|---|---|
| `event` | `card` / `move` / `effect` / `attack` / `decision` |
| `moves` | `{id, from, to}` per frame that moved |
| `hits` | `{id, zone, amount}` per zone that took damage |
| `dead` | `{id, pos}` per frame destroyed — with the tile it was on, since a destroyed frame has no position any more |

The board draws those with the rulebook's own vocabulary (`rules/rules.tex`,
the play-example helpers), not a second invented language: a dotted ring where
a frame was and a dashed arrow to where it is; a red arrow attacker → target,
solid at melee reach and dotted for a shot; a burst carrying the number of
marks that landed; and the block shield itself — the same shape
`card_macros.tex` prints on the cards — on each zone that was stopped.

A corner card on the board says which frame is acting, with which card, at what
initiative, and which step of it is running — during your turn and during the
playback alike. It floats over the bottom-left of the board, which is sometimes
exactly the tile you want, so **tapping it folds it down to a stub** and
tapping the stub brings it back.

That "which step" is `Resolution.step`, and it is deliberately not the head of
`Resolution.steps`. An effect takes itself off the remaining list *before* it
runs, so a card resolved effect-first read `["movement"]` while it was asking
where to put a gravity well and `["movement"]` again for the move that
followed: two completely different questions with identical readouts, which
made the green movement tiles look like part of the orange placement. For the
same reason a `move` prompt names the card ("Gravity Well: move Blue Hector
MkI"), since the corner panel says "Gravity Well" throughout.

**Play back my own actions too** (off by default) records the player's frames
as well as the AI's. Normally there is no point: your cards resolve under your
own hand and a delay between a tap and its result is worse than no animation.
But an action with no choices left in it — one legal target, one legal tile —
resolves inside the same call with nothing asked, so you see the aftermath and
never the act. The client sends `replayMine` on the command; the server passes
it to `Session.watcher(mine=True)`.

### Ordering the steps

A card with more than one step asks how to order them. Since it is usually the
same answer twice running, the last order you accepted comes back as a single
**Same as last time** tap (remembered per frame, and only offered when it still
answers the question being asked). Every order the engine offers is one tap
below that, and the step-by-step picker is still there under *Build the order
step by step*.

### What the UI is honest about

* **Every card's text is implemented.** `effects.deferred_effects()` is empty,
  and the machinery for saying otherwise is still in place: a card the engine
  does not understand gets a `TEXT NOT IMPLEMENTED` ribbon on its thumbnail, a
  warning in its detail view and a note in the initiative list, and
  `test_all_pilot_and_drone_text_is_implemented_or_flagged` fails the build if
  one appears. Add a card to a CSV and that is the test that tells you.
* **Blocking is compulsory**, and each block option states whether the card is
  discarded (normal block) or kept (super block), and whether its own action is
  forfeit.
* The **initiative list shows printed values**. The engine also applies −1 for a
  High zone on its last hit and ∓2 for Stunned/Stimmed; those are not shown on
  the card.
* Your own face-down cards read *"face down — the AI cannot see it"*; the AI's
  are blank card backs, because the server never sends their identity.
* **Persistence and Echoes of the fallen are different rules** and the tableau
  keeps them apart, because they look alike on the table and the client used to
  describe one as the other. A card with a **persistence** marker stays in play
  for its duration, is *set aside*, and neither resolves again nor blocks — the
  engine's `aside` pile. An **echo** is the opposite: when a frame is
  destroyed, its controller may reveal the top card of that dead frame's deck
  and set it sideways with a surviving frame's actions, where it can **block**
  for that frame — a flag on a committed card, not the aside pile.

### Two of the same frame

Bringing two Kuwagatas is legal — one deck per frame, one faction per squad,
and nothing says the frames must differ — so the squad picker lets you: tapping
a deck **adds a copy**, the squad row above shows the slots it fills, and each
slot has an × to drop it.

Two frames of the same model then have to be told apart everywhere, and the
engine does that rather than the client: **a frame's id is its name**. It is
`"<Team> <Model>"` — `Blue Kuwagata`, `Red Hector MkI` — with an ordinal added
only where a team fields more than one of the model: `Blue Kuwagata 1`,
`Blue Kuwagata 2`. See `engine.types.frame_id_for`.

That one decision settles what used to be several problems:

* the **log** names frames by id, so "Blue Kuwagata 2 takes 2 Mid damage" is
  unambiguous even though it names the *defender* while the *attacker* is the
  one resolving. The client picks the known ids out of each line and tints them
  by team; it infers nothing.
* the same is true of every `prompt` the engine writes.
* the client's label for a frame *is* its id, so the ribbon, the tableau, the
  target list, the initiative ladder and the frame card cannot disagree. The
  board marker still shows just the ordinal, as a badge, because a tile is
  small.

Nothing in the client identifies a frame from prompt text. The engine's
`prompt` is shown as its own explanation, but *who* a decision is about is read
from `pending.frameId` (or, for a deploy, from the frame you picked).

### How many actions

`commit_actions` is the one decision that takes a *set* of options rather than
one, and the size of that set is not a constant: `Wunderkid_Hyper` ("next turn:
play 1 extra action") raises it to three. So the decision carries the range —
`pickMin` / `pickMax` on the view, `pick_min` / `pick_max` on
`PendingDecision` — and everything downstream reads it rather than assuming
two: the Plan screen builds that many slots, the sheet enables **Commit** over
that range, and the AI commits the ceiling. A client that assumed two made the
card's whole effect unspendable.

### Card text, and adding a card

Every pilot and drone card's printed text is implemented, and each one is
registered in `engine/effects.py` by its key — except drones, which are matched
on their **type**. Every drone card does the same thing (summon tokens that
repeat the card each turn), and the two numbers that differ are read off the
printed text:

* `_count_from_text` — "Summon **two** attack dogs" → two tokens, one placement
  decision each;
* `_reach_from_text` — "Summon one Gun Tower **within 3**" → placed up to three
  tiles away, rather than beside the frame.

So a new drone in `Drone actions.csv` works with no engine change at all. The
same two helpers now drive Barricade's count and Gravity Well's placement
reach, so a balance edit to those numbers is a CSV edit too.

Each placement option carries the drone's printed `reach`, which is what stops
the AI building an immobile, two-hit-point, range-8 Gun Tower in the enemy's
face: `Agent._pick_tile` aims for the distance the thing wants to be at rather
than always closing.

Pilot cards are not like this — each one's text is its own — so an unlisted
pilot card is deferred on purpose and
`test_all_pilot_and_drone_text_is_implemented_or_flagged` says so.

### Ephemeral Images

`Mystic_Ephemeral Images` replaces a frame with three tokens, one of which is
secretly the frame. The frame does not leave the board — it stands on one of
the three tiles — but while the images are up:

* `view_for` sends another seat **no position at all** for that frame
  (`"pos": null`, `"cloaked": true`), and no way to tell one image from
  another. Only the owning seat's view marks the real one.
* the frame is in no target list, no enemy card's option list and no threat
  overlay (`GET /api/game/{id}/threat` answers **409** for it). The images are
  what may be attacked.
* attacking the real image is an ordinary attack on the frame, blocks and all,
  and finding it ends the trick. Attacking a fake removes it.
* the images move with the frame wherever it is moved from — movement,
  knockback, Teleport, Ace Reflexes, a portal — because `engine.effects
  .sync_images` runs from the engine's advance loop rather than from each of
  those places.
* an image blocks movement like the frame under it would, so an enemy cannot
  find the frame by noticing which of the three tiles it may not walk into.

The AI stands a hidden frame in the middle of its own images so it keeps
weighing it as a threat, and prices an attack on an image at its share of a
real hit plus what narrowing the guess is worth.

## AI settings

AI parameters come from `GET /api/ai/params` and the controls are generated
from that response — the client hardcodes no parameter names. Workstream D
added parameters after this client was written and they appeared with no change
here. Difficulty presets show as chips. Changing values in the drawer and
pressing **Apply to this game** retunes the running AI immediately.

## HTTP API

```
GET    /api/health                 counts of cards, frames, decks, images, art
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
GET    /api/game/{id}/threat?frame=…   reach + line of sight, public info only
```

`/?game=<id>` deep-links straight into a running game;
`&view=board|plan|field|order|goals|log` picks the tab.

### What a game view carries beyond `view_for`

`Session.view()` adds four things the client cannot work out for itself, all of
them assembled in `readouts.py` from engine calls rather than restated rules:

| Key | What it is |
|---|---|
| `resolving` | The card mid-resolution: frame, card key, effective initiative, remaining steps, and the attack in flight. `PendingDecision` names only the frame, and during a compulsory block that frame is the *defender* — so this is not `pending.frameId`. |
| `defence` | Per frame, per zone: how many cards still in front of it block that zone, how many are super blocks, which ones (when this seat may know), and how many cards are face down. |
| `initiative` | `{uid: initiative}` as the engine will queue it, for cards this seat can identify. |
| `replay` | Snapshots of what the AI did since you last acted, one per beat — see [Watching the AI move](#watching-the-ai-move). Present on a command response, absent from a plain `GET` — a refresh must not re-animate. |
| `kills` | `{seat: n}` — "1 point per opposing frame defeated". Credited when the frame dies, so it cannot be recounted from the board afterwards; the final score itemises this half against the objectives. |

Redaction is the same rule as the rest of the view: a card this seat may not
identify contributes to counts and nothing else. Replay snapshots go further
and carry **no card uids at all** — a card that was face up while it resolved
can be discarded, reshuffled and drawn again, so its uid in a snapshot would be
a handle on a card that is in the AI's hand now.

### Command payloads

| kind | payload |
|---|---|
| `commit_actions` | `{"uids": ["c17", "c22"]}` |
| `choose_actor` | `{"uid": "c17"}` — which of your tied cards resolves next |
| `resolve_order` | `{"order": ["movement", "attack"]}` |
| `move` | `{"x": 7, "y": 12}` |
| `attack_target` | `{"kind": "frame", "id": "b1"}` |
| `choose_block` | `{"uid": "c04"}` |
| `effect_choice` | the option the engine offered, e.g. `{"mulligan": true}` |
| `echo_card` | `{"dead": "Blue Adam", "host": "Blue Kuwagata 1"}` or `{"decline": true}` |
| `deploy` | `{"frame": "Blue Kuwagata 1", "x": 7, "y": 15}` (setup, before turn 1) |
| `place_objective` | `{"token": "t12", "x": 6, "y": 0}` — where an objective's token goes down (setup) |
| `choose_frame` | `{"frame": "Red Fenrir"}` — one of your frames, for what an objective needs one for (Dome Campus's bomb carrier) |
| `move_token` | `{"token": "t12", "x": 6, "y": 1}` — a gang or a refugee taking its move, at its own initiative |

`POST /command` also accepts `replayMine: true` alongside the command, which
records the player's own frames in the response's `replay` — see
[Watching the AI move](#watching-the-ai-move).

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

## Board art

The board needs three more sets, built by `playtest/server/assets.py` and served
as plain static files (the board asks for twenty terrain cards at once; they
belong in the browser cache, not behind an API route):

```bash
python -m playtest.server.assets            # terrain, tokens and standees
python -m playtest.server.assets --what terrain --force
python -m playtest.server.assets --what frames
```

* **`static/terrain/`** — 31 cards, 0.57 MB. The printed terrain art in
  `terrain/` is a 640×890 card; what the board wants is the 3×4 tile grid
  inside it, so the builder crops to exactly the grid `terrain_cards.py` draws
  (2.06 cm tiles from (0.1, 0.2), art placed 6.35 cm wide at (3.25, 4.45)) and
  scales that to 240×320. The crop coming out 3:4 to within a pixel is the
  check that the geometry is right — `test_terrain_crop_is_the_playable_grid`.
* **`static/tokens/`** — 14 pieces from `tts_assets/`, 0.03 MB, 96 px PNG8 with
  their transparency intact.
* **`static/frames/`** — the **standees**: 12 frames, 0.05 MB. `Frames.csv`
  points each frame at its artwork in `pictures/foreground/`; the builder
  `-trim`s the mech out of its transparent canvas and scales it to 128 px, so
  the board can stand the actual machine on its tile instead of a coloured box
  with three letters on it. Turning **ART** off still gives you the abstract
  counter, which is the faster read at FIT.

**All of this is committed**, like the card bundle and for the same reason: the
phone clones the repo and can neither regenerate it nor install ImageMagick.
`.gitignore` has the negations (`!playtest/server/static/**/*.png` already
covers the standees). Total art carried to the phone: **3.14 MB**
(2.46 cards + 0.57 terrain + 0.05 frames + 0.03 tokens + icons).

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
