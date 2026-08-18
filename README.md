# EYESY Simulator

Runs Critter & Guitari EYESY modes on your computer, so you can iterate on a mode quickly and upload it to the device once it works. The knobs and panel buttons are simulated with the keyboard.

Targets **OS v3** — the `eyesy` object, Python 3. Modes written against v2's `etc` object will not run.

## Install

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

## Run

    .venv/bin/python simulator.py examples/ink-rings/main.py

Any mode folder on disk works. Point it at the `main.py`:

    .venv/bin/python simulator.py /path/to/your-mode/main.py

## Keys

The window needs focus — click it first; it often opens behind the terminal.

Press `1` through `5` to pick a knob, or walk the row with ← and →. The up and down arrows turn it: a tap nudges, a held key sweeps.

Hold Shift and the five knobs stop where they are while the arrows keep turning the hardware underneath. A mode that reads `eyesy.knob[i]` while `eyesy.key2_status` is true gets that as a second page of controls — the device does this, and knobs 2 through 5 are free for it. Knob 1 under Shift is the engine's audio input gain.

One knob serves both pages, so letting Shift go hands the ordinary knob whatever position you just left it in. That is the device's behaviour, and it is why a second page suits settings you choose once.

| Key | Does |
|---|---|
| `1`–`5` | select the active knob, the five on the middle row |
| ← → | select the knob to either side, wrapping at the ends |
| ↑ ↓ | turn the active knob: tap to nudge, hold to sweep |
| Shift | **Shift** button, middle left. Held, the five knobs freeze and the arrows turn a second page a mode can read |
| `A` | **Audio In** jack, back panel; press again to unplug |
| `O` | **On Screen Display** button, top left. Starts on; press to hide |
| `T` | **Trigger** button, bottom right. Tap to fire once; hold for a sweeping sine that keeps re-triggering |
| `P` | **Persist** button, top right. Stops the screen clearing between frames so drawing accumulates |
| `G` | **Screenshot** button; writes `grab-N.png` in the repo root |
| `[` `]` | previous / next foreground palette |
| `-` `=` | previous / next background palette |
| `,` `.` | audio gain down / up |
| Esc | quit |

## Audio

`A` puts whatever the Mac is playing on the jack, so a mode can be developed against real music. It goes through a Core Audio process tap, so nothing is installed and playback stays audible. Levels run low — raise the gain with `.` until the trigger indicator fires.

Use `--signal system` to come up already plugged in, or `--signal synth` for a tone that peaks twice a second.

## License

BSD 3-Clause. See `LICENSE`.

`color_palettes.py` and `osd.py` are Critter & Guitari's — see `LICENSE-EYESY_OS.txt`.

## Changelog

### 0.3 — 2026-08-15

- System audio capture on `A`, through a Core Audio process tap.
- Left and right walk the knob row, alongside the number keys.
- The On Screen Display starts visible.
- The background knob starts at zero, for black rather than mid grey.

### 0.2 — 2026-08-15

Rewritten for EYESY OS v3 — `eyesy` object, Python 3.11, pygame 2.6.1 — and split into its own project.

### 0.1 — 2021-09-25

Original harness, written for EYESY OS v2 and the `etc` object.
