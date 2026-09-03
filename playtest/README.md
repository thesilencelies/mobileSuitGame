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

### Starting a game

Pick a squad a deck at a time (tapping a deck twice fields two of that frame),
then **a battlefield for each side**: the archetype pairs in `decks/`, each one
ten terrain cards and the five objectives it can deal. The chips list those
five, because two of them end up on your half of the board and they are about
half the victory points — picking blind from four words is a coin toss, not a
choice. *Random* is the default and is the old behaviour: the engine deals a
pair from the game's own rng, so a seed still reproduces the battlefield.

At the bottom of the setup screen (and in the drawer) is the **build marker** —
`build 3f9a2c1 · commit d5ba8e8`. There is no packaging step, the app runs
straight out of a clone, so this is the only way to tell whether the phone is
running what you just pulled: it is a hash of every source file the app runs
and every static file the browser loaded, identical on two machines holding the
same code. Tap it to copy.

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

A decision that is *about* cards shows them: Kuwagata's once-per-game mulligan
("discard your hand and draw a new one") lays the seven cards out in the sheet
and opens the Plan tab on the same hand, because nobody can answer that
question without seeing what they would be throwing away.

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
  actually have to tap one. Panning stops when the board's edge reaches the
  view's, *plus* however much the panels floating over the board cover
  (`_insets` measures them off the DOM rather than listing them, so a new
  panel is accounted for by itself, capped at 45% of the view so the board
  cannot be lost). Without that slack the outermost tiles sit permanently
  under the tool column or the acting-card panel, where they can be neither
  read nor tapped.
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
* **Line of sight from where you are going.** Propose a move — the first of
  the two taps — and the board shades what that frame would see *from the tile
  it is thinking about*, not from where it stands. Half of movement is deciding
  where you will be able to shoot from next turn. It needs no toggle: it only
  appears in answer to a deliberate tap and goes with it. The LoS toggle in the
  drawer still shades the selected frame's own sight lines.
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
(green). A shove, a drone's move and a reflex step are *walks* — the thing
covers the ground, each tile carries what it costs — so they are green. The
cards that pick a tile up and put it down are orange even though what they move
is already on the board: Teleport, Displace and Suplex ignore walls, cost
nothing and have no route, so a green tile promising a movement cost would be
telling you something untrue.

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

### Who may stand where

Two rules that read as one and are not, so the engine keeps them apart
(`GameState`):

* **What may not be crossed** (`move_blockers`) — enemy frames, and the tokens
  that are solid terrain: a barricade, a cage wall, an Ephemeral Image. A
  *friendly* frame may be walked through and costs nothing extra.
* **What may not be stopped on** (`unit_tiles`) — every unit. "Tokens that move
  and Frames are collectively called units. A tile that has a unit on it is
  occupied and another unit cannot end its move on that tile" (rules.tex
  Tokens), so a drone or a Riverside gang blocks a landing but not a route,
  and the Shiny Thing blocks neither — you have to be able to stand on a
  carried token, since standing on it is how it is picked up.

`walk_options` is the one place the two meet, and every movement offer in the
engine goes through it: the move step, a shove, a reflex step, a drone's turn,
an objective token's turn, and the threat overlay the client draws. Staying put
is always in the list — it is a legal answer, and the only one a bound frame
has.

`occupied()` is neither of those: it is what stops a *line of sight*, which is
frames (a frame "counts as one elevation higher") plus the solid tokens.
Drones and gangs do not adjust line of sight, so they are not in it, and
shooting over one is fine.

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

A drone acts on the turn it is summoned, and on that turn the frame does not
swing (`delegates_attack`). What a drone does is the *drone's*, which the
attack carries as `via_token` beside the summoner's `attacker_id`: the frame
whose card it is takes the kill, but everything the defender does back is done
to the drone. A Chain catching a gun tower's shot dazes the gun tower — which,
being a token, has no status counters to take, so the debuff fizzles — rather
than reaching across the board for the frame that built it, and a Parry hits
the drone for one. A carried token knocked loose drops toward the drone that
fired, not toward its owner. Two more things follow the card rather than the
drone: its printed blocks stop blocking the moment the card resolves — the card
stays on the table for the drone's health bar, not as a shield — and a drone
may shoot an objective token, which it does through the ordinary attack
pipeline so damage reduction and destruction are the same code a frame's shot
uses.

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

#### "Other actions"

Accelerate ("other actions this turn get +3 mv") and Relentless Assault ("all
other actions this frame takes this turn resolve twice at -2mv") both modify
movement for actions *other than themselves* — and both are actions with a
movement step of their own, which the controller may order after the effect
step. So the modifier is banked when the effect resolves and only joins
`turn_flags["movement_bonus"]` once the granting card has finished
(`after_card_resolved`). That is the one moment "other" is unambiguous: what
has already moved is not charged, and everything still to come gets it.

#### Readings the cards did not settle themselves

Some card text leaves a real question open. Each of these is a decision the
engine had to make, not something the card says:

* **Psychic Storm** hits "every unit within 5" — read as everything that acts
  on its own: frames, drones, and the objective tokens that move (a Riverside
  gang, a Car Park refugee). The Tower and the reactors are buildings and the
  Shiny Thing is luggage, so the weather leaves them alone. It hits both sides.
* **Doom** collects from a frame that has "not moved more than 3 spaces" at the
  end of the next turn, and *any* displacement counts — its own movement, a
  shove, a knockback. The card wants the frame to leave; who did the pushing is
  not the point. `state.record_movement` is the one place that counts.
* **The Lake Ritual** hands the relic to a frame of the winner's choosing
  ("held by one of those frames"), and the choice matters — the relic is
  dropped the moment its carrier is damaged, so it is a question of which
  machine you are willing to have shot. It is asked at the end of the turn the
  ritual completes, which means cleanup has to be able to park on a decision:
  `_finish_cleanup` rolls the turn over only once `cleanup_decision` has
  nothing left to ask, and the phase stays `"cleanup"` until then. Asking in
  the next turn's planning phase instead would have skipped it entirely when
  the ritual is completed on the last turn of the game.
* **Extraction** (the Fugitive) takes its token off the board when the
  defenders score it — it has reached the point, so there is nothing left to
  shoot, carry or take back. `ON_LATCH` is the hook; every other objective
  leaves its pieces where they are, because a destroyed reactor is still
  rubble in the way. Only on the *defender's* score: when the attackers take
  it at the end of the game the fugitive is still sitting wherever it was
  stopped, and the client tells the two apart from that (`settled` for the
  owner, with no token left) rather than calling an extraction a destruction.
* **Bind** is stored against the Bruiser, not its victim: "as long as that
  frame is adjacent" is the grappler's job to maintain, so the hold lifts by
  itself when the Bruiser dies, is moved off, or the card leaves play.
* **Suplex** throws to "the other side of this frame", which is read by sign:
  a destination counts when it is not back in the direction the target came
  from. Straight sideways is allowed; the tile it was thrown from is not.
* **Sensory Overload** and **System Override** print no range at all, so they
  have none — reading a missing "within N" as the usual 3 would be inventing a
  restriction. System Override is spent on the next movement decision that
  frame is *offered*: an action with no movement in it has no tile to choose.
* **Showboating** filters the attacker's target list rather than punishing a
  choice afterwards — including tokens, since shooting a reactor instead would
  be exactly the dodge the card forbids. If the showboater cannot legally be
  targeted (Utter darkness), nothing is forced.
* **Parallel Action** fires between cards, from `followup_decision`: that is
  the one moment "would take an action" is well defined and the swap can still
  change what happens next. Being attacked only sets a flag — an attack cannot
  stop half way to ask a question — and the flag is read at the same seam.
  Nothing fires on the turn the card is played: it is printed "Next turn:" for
  the same reason Hyper and Fog of war are, and asked the same way (the card is
  only in the `aside` pile from the turn after it resolved), because otherwise
  the opponent takes the card off you by swinging once — a redraw with nothing
  left to change is no card at all. For the same reason it does not fire when
  there is nothing left to swap; it stays armed for a trigger it can use. Its
  "face down actions" are read as the actions still to resolve, so a frame
  under `Revealed` — whose whole plan is face up — can still use it. The extra
  hand is discarded when the swapping stops; nothing on the card says it is
  kept. Each action is settled once per run (swapped, or looked at and kept),
  which is what stops "drop it / actually keep it" going round for ever.
* **The order of a card's steps** is the controller's to pick, so it is asked
  as `resolve_order` over the permutations of the steps the card has — but a
  card can narrow the list, and when one order is left there is nothing to ask
  and the card simply takes it. `effects.step_orders` is the one place that
  decides. `Booster_Explosive Exit` prints its constraint ("Must attack before
  moving"), and it is read off the text rather than the card key so the mirror
  of it needs no engine change. The other two are the engine's reading of
  cards whose effect has to be in force before the frame moves:
  `Booster_Jump`, because "all movement this turn" has to include the move the
  card came with, and `Booster_Boomerang`, because "the position they started
  this action at" has to be noted before the action carries the frame off it.
* **Jump** is half of Flying and not the other half. "Ignores elevation
  penalties" makes a climb cost what flat ground costs
  (`step_cost(..., climb_free=True)`), but obstacles still stop it — Flying is
  the keyword that says otherwise, and a booster is not one. Like
  `flying_target` on the line-of-sight call, `climb_free` is an addition to the
  frozen `BoardProtocol`, so a board that has not got it is asked the old
  question instead of failing.
* **Boomerang** returns the frame at the start of the next turn, from
  `effects.start_of_turn` — the hook `_begin_planning` calls once the turn
  flags are cleared, so the anchor has to live in the `fx` bag rather than in
  `turn_flags`. Nothing says what happens when somebody has parked on the tile
  it was snapping back to; it takes the nearest tile it can stand on within
  `BOOMERANG_SEARCH`, which keeps the card doing what it says without either
  deleting the effect or stacking two frames on one tile. The return is
  displacement like a knockback — it is recorded (so Doom counts it) but it
  claims nothing on the tile it lands on, which is how knockback already
  behaves.
* **Splash text** — "Hits all adjacent enemies", "Also hits any enemies
  adjacent to the target", "Hits all targets in range" — catches enemy *and*
  neutral tokens, not only frames: an enemy is anything the attack could have
  been aimed at, so a barricade or a gun tower beside you is swept up like a
  mech, and only the attacker's own tokens are spared. `combat.hostile_targets`
  is the one list all three read, and `legal_targets` is that same list with
  range and line of sight applied — which is what `Chain_Tangle` wants, since
  "hits all targets in range" is read as "the card does not choose" and hits
  everything the attack could have been declared against. None of the three
  consults Showboating: that card says who an attack may be *declared* on, and
  once declared the card does what it prints. Each target still gets its own
  block decision, and an Ephemeral Image caught by splash resolves the same way
  as one that was aimed at — hitting the real one is an attack on the frame.

  When the text names a shape, that shape *replaces* the weapon's reach rather
  than being filtered by it (`attack_zones_against(..., reach=False)`). The far
  side of `Kinetic Hammer_Slam`'s target is two tiles from a melee attacker, so
  re-checking the range would delete exactly the enemies the card was printed
  to catch. Elevation shift still applies to each of them: reach is what the
  splash overrides, and elevation is about the ground. "Hits all targets in
  range" is the exception that keeps the check, because there the range *is*
  the shape — a target at 3 is only hit by the zones that reach 3.
* **Cage Fight** does not ask where the box goes. Both fighters have to end up
  inside the 3x3 the walls enclose and they are at most two apart, so the
  centre is their midpoint and the only question is who is locked in. The walls
  are ordinary impassable tokens (`GameState.occupied` names `cage` beside
  `barricade`), and `sync_cages` takes them down from the advance loop rather
  than from each of the ways a frame can leave a tile.

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
GET    /api/game/{id}/threat?frame=…[&x=&y=]  reach + line of sight, public only
                                    x/y = what it would see from a tile it is
                                    only considering; reach stays where it is
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
| `resolve_order` | `{"order": ["movement", "attack"]}` — only offered when the card allows more than one order (see `effects.step_orders`) |
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
* **`static/tokens/`** — the pieces from `tts_assets/`, 96 px PNG8 with their
  transparency intact, plus one per **drone group**. A drone has no piece of
  its own: it is whatever its card summoned, so `drone_token_sources()` reads
  the artwork off the drone cards themselves and writes it under
  `slug(group)` — which is what `api.js` builds from the card key the view
  puts on a drone token. A Gun Tower is drawn as a gun tower, and a new drone
  group in `Drone actions.csv` needs no change on either side of that.
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
