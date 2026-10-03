"""
Look at a mode without watching it: knob sweeps and scene filmstrips, rendered headless.

Everything runs on a virtual clock. pygame.time.get_ticks is replaced before the mode
is imported, and each frame advances it by exactly 1/fps, so a mode that reads the
clock sees the time it would see on the unit at that frame rate, however fast or slow
the laptop renders. Frames are drawn through the same steps as the simulator's loop:
audio, the knob sequencer, set_knobs, clear, draw.

Knobs change the way a hand turns them, in one continuous run, so feedback modes carry
their history from one setting into the next. Each setting gets a settle time before
anything is captured.

    python explore.py sweep MODE --knob 2 --out sweep.png
    python explore.py grid MODE --knobs 1,3 --out grid.png
    python explore.py film MODE SCENE --every 10 --out film.png
    python explore.py thumb MODE SCENE --at 20 --out scene.jpg
    python explore.py prospect MODE --count 96 --out library/
    python explore.py prospect MODE --stops 11 --keep 100 --out library/
    python explore.py keep library/ --weights colour=0.1
    python explore.py board library/ p012=calm,p040=storm

MODE is a mode's main.py. SCENE is a scene file (.json) or a scene folder. Every sheet notes
under each frame how much it moves (mean change per frame, 0-255, over the second
before) and how bright it is, so a stall or a dead patch shows as a number too.
"""
import argparse
import importlib
import os
import sys

os.environ.setdefault('SDL_VIDEODRIVER', 'dummy')

import pygame  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import scene  # noqa: E402
import simulator  # noqa: E402

CELL = (256, 144)
LABEL = 34              # label strip under each cell
PROBE = (64, 36)        # size the motion and brightness numbers are measured at


class Clock:
    """Virtual milliseconds, standing in for pygame.time.get_ticks."""

    def __init__(self):
        self.ms = 0.0

    def ticks(self):
        return int(self.ms)


class Rig:
    """One mode, running headless, stepped a frame at a time."""

    def __init__(self, mode_path, fps=30.0, signal='silent'):
        self.fps = fps
        self.clock = Clock()
        pygame.time.get_ticks = self.clock.ticks

        pygame.init()
        self.hwscreen = pygame.display.set_mode((simulator.XRES, simulator.YRES))
        self.screen = pygame.Surface((simulator.XRES, simulator.YRES))

        mode_root = os.path.dirname(os.path.abspath(mode_path)) + os.sep
        sys.path.insert(0, mode_root)
        importlib.invalidate_caches()
        name = os.path.splitext(os.path.basename(mode_path))[0]
        self.mode = importlib.import_module(name)

        self.eyesy = simulator.Eyesy(mode_root=mode_root)
        sig = simulator.SynthSignal() if signal == 'synth' else simulator.SilentSignal()
        self.audio = simulator.AudioSource(sig)
        self.gain = 0.25
        self.sequence = scene.KnobSequence(None)
        self.frame = 0
        self.history = []           # (mean change, brightness) per frame
        self._prev = None
        self.mode.setup(self.hwscreen, self.eyesy)

    @property
    def seconds(self):
        return self.frame / self.fps

    def recall(self, path):
        data, frames, scene_file = scene.open_scene(path)
        self.sequence = scene.recall(self.eyesy, data, frames)
        return data, frames, scene_file

    def set_knobs(self, values):
        for i, v in enumerate(values):
            if v is not None:
                self.eyesy.knob[i] = float(v)

    def step(self):
        e = self.eyesy
        self.audio.advance(self.fps, self.gain)
        e.audio_in[:] = self.audio.buffer
        e.audio_in_r[:] = self.audio.buffer_r
        e.audio_peak = self.audio.peak
        e.audio_peak_r = self.audio.peak_r
        if max(self.audio.peak, self.audio.peak_r) > simulator.TRIGGER_THRESHOLD:
            e.trig = True
        self.sequence.run(e)
        e.set_knobs()
        if e.auto_clear:
            self.screen.fill(e.bg_color)
        self.mode.draw(self.screen, e)
        e.trig = False
        self.frame += 1
        self.clock.ms = self.frame * 1000.0 / self.fps
        self._measure()

    def _measure(self):
        small = pygame.transform.smoothscale(self.screen, PROBE)
        raw = pygame.image.tobytes(small, 'RGB')
        change = 0.0
        if self._prev is not None:
            change = sum(abs(a - b) for a, b in zip(raw, self._prev)) / len(raw)
        self._prev = raw
        self.history.append((change, sum(raw) / len(raw)))

    def run(self, seconds):
        for _ in range(max(0, round(seconds * self.fps))):
            self.step()

    def recent(self, seconds=1.0):
        """Mean change and brightness over the last `seconds`."""
        n = max(1, round(seconds * self.fps))
        tail = self.history[-n:]
        return (
            sum(c for c, _ in tail) / len(tail),
            sum(b for _, b in tail) / len(tail),
        )

    def grab(self):
        return pygame.transform.smoothscale(self.screen, CELL)

    def probe(self):
        """The last frame at PROBE size, as RGB bytes."""
        return self._prev


class Sheet:
    """A grid of labelled frames, saved as one PNG."""

    def __init__(self, cols, rows, title):
        self.cols, self.rows = cols, rows
        self.font = pygame.font.Font(None, 20)
        self.title_font = pygame.font.Font(None, 26)
        self.top = 34
        self.surface = pygame.Surface(
            (cols * CELL[0], self.top + rows * (CELL[1] + LABEL)))
        self.surface.fill((24, 24, 24))
        self.surface.blit(
            self.title_font.render(title, True, (230, 230, 230)), (8, 8))

    def put(self, col, row, image, lines):
        x = col * CELL[0]
        y = self.top + row * (CELL[1] + LABEL)
        self.surface.blit(image, (x, y))
        for i, line in enumerate(lines[:2]):
            text = self.font.render(line, True, (210, 210, 210))
            self.surface.blit(text, (x + 4, y + CELL[1] + 2 + i * 15))

    def save(self, path):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        pygame.image.save(self.surface, path)
        print(path)


def _floats(text, count=None):
    values = [None if v in ('', '-') else float(v) for v in text.split(',')]
    if count is not None and len(values) != count:
        raise SystemExit(f'expected {count} comma-separated values, got {text!r}')
    return values


def _name(mode_path):
    """A mode's folder name, which is what the unit calls it."""
    return os.path.basename(os.path.dirname(os.path.abspath(mode_path)))


def _steps(n):
    return [i / (n - 1) for i in range(n)] if n > 1 else [0.5]


def _stats(rig):
    change, light = rig.recent()
    return f'move {change:4.1f}  light {light:5.1f}'


def cmd_sweep(args):
    rig = Rig(args.mode, args.fps, args.signal)
    if args.scene:
        rig.recall(args.scene)
        rig.sequence.playing = False
    if args.base:
        rig.set_knobs(_floats(args.base, 5))
    values = _floats(args.values) if args.values else _steps(args.steps)
    times = _floats(args.times)
    k = args.knob - 1
    fixed = ' '.join(
        f'k{i + 1} {rig.eyesy.knob[i]:.2f}' for i in range(5) if i != k)
    sheet = Sheet(
        len(times), len(values), f'{_name(args.mode)}  knob{args.knob}  ({fixed})')
    rig.run(args.settle)
    for row, v in enumerate(values):
        rig.eyesy.knob[k] = v
        rig.run(args.settle)
        start = rig.seconds
        for col, t in enumerate(times):
            rig.run(start + t - rig.seconds)
            sheet.put(col, row, rig.grab(), [
                f'k{args.knob} {v:.2f}  +{t:g} s', _stats(rig)])
    sheet.save(args.out)


def cmd_grid(args):
    rig = Rig(args.mode, args.fps, args.signal)
    if args.scene:
        rig.recall(args.scene)
        rig.sequence.playing = False
    if args.base:
        rig.set_knobs(_floats(args.base, 5))
    a, b = (int(x) - 1 for x in args.knobs.split(','))
    values = _steps(args.steps)
    sheet = Sheet(
        len(values), len(values),
        f'{_name(args.mode)}  rows knob{a + 1}, columns knob{b + 1}')
    rig.run(args.settle)
    for row, va in enumerate(values):
        # snake through the columns so each move is one step, as a hand would make it
        cols = range(len(values)) if row % 2 == 0 else reversed(range(len(values)))
        for col in cols:
            rig.eyesy.knob[a] = va
            rig.eyesy.knob[b] = values[col]
            rig.run(args.settle)
            sheet.put(col, row, rig.grab(), [
                f'k{a + 1} {va:.2f}  k{b + 1} {values[col]:.2f}', _stats(rig)])
    sheet.save(args.out)


def cmd_film(args):
    rig = Rig(args.mode, args.fps or 30.0, args.signal)
    data, frames, scene_file = rig.recall(args.scene)
    if args.fps is None and scene_file is not None:
        rig.fps = scene_file.fps
    length = len(frames) / rig.fps if frames else args.length
    times = []
    t = 0.0
    while t < length - 1e-6:
        times.append(t)
        t += args.every
    rows = (len(times) + args.cols - 1) // args.cols
    name = os.path.basename(os.path.normpath(args.scene))
    sheet = Sheet(
        args.cols, rows,
        f'{name}  {data["mode"]}  {length:g} s at {rig.fps:g} fps, one frame every '
        f'{args.every:g} s')
    moves = []
    for i, t in enumerate(times):
        rig.run(t - rig.seconds)
        if rig.frame == 0:
            rig.step()
        knobs = ' '.join(f'{v:.2f}' for v in rig.eyesy.knob)
        section = scene_file.section_at(t) if scene_file else ''
        sheet.put(i % args.cols, i // args.cols, rig.grab(), [
            f'{t:5.1f} s  {section}'[:34], f'{knobs}  mv {rig.recent()[0]:.1f}'])
    rig.run(length - rig.seconds)
    sheet.save(args.out)

    # the whole run, a second at a time: where it stalls, and where it lurches
    per = max(1, round(rig.fps))
    for s in range(0, len(rig.history), per):
        chunk = [c for c, _ in rig.history[s:s + per]]
        moves.append(sum(chunk) / len(chunk))
    spikes = []
    for s in range(0, len(rig.history), per):
        chunk = sorted(c for c, _ in rig.history[s:s + per])
        med = chunk[len(chunk) // 2] or 1e-6
        spikes.append(sum(1 for c in chunk if c > 2 * med) / len(chunk))
    print('move per second:')
    for s in range(0, len(moves), 10):
        print(f'  {s:4d} s  ' + ' '.join(f'{m:4.1f}' for m in moves[s:s + 10]))
    jerky = [i for i, f in enumerate(spikes) if f > 0.2]
    if jerky:
        print('seconds where >20% of frames jump past twice the median: '
              + ' '.join(str(i) for i in jerky))


def cmd_thumb(args):
    rig = Rig(args.mode, args.fps or 30.0, args.signal)
    data, frames, scene_file = rig.recall(args.scene)
    if args.fps is None and scene_file is not None:
        rig.fps = scene_file.fps
    at = args.at if args.at is not None else (scene_file.thumbnail if scene_file else 10.0)
    rig.run(at)
    # scene.jpg is 320x240, the screen squeezed to 4:3 (eyesy.py:611)
    thumb = pygame.transform.smoothscale(rig.screen, (320, 240))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    pygame.image.save(thumb, args.out)
    print(args.out)


def _halton(index, base):
    """The index-th point of a Halton sequence: spreads samples evenly, no clumps."""
    f, r = 1.0, 0.0
    while index > 0:
        f /= base
        r += f * (index % base)
        index //= base
    return r


PRIMES = (2, 3, 5, 7, 11)


def cmd_prospect(args):
    """Many knob settings, one frame each, kept as a library to compose from.

    The settings come from a Halton sequence over the knobs being varied, so a hundred
    samples cover the space evenly instead of clumping. Each gets its own settle time
    in one continuous run, and is saved as a frame plus a line in library.json.
    """
    import json

    rig = Rig(args.mode, args.fps, args.signal)
    base = _floats(args.base, 5) if args.base else [None] * 5
    base = [rig.eyesy.knob[i] if v is None else v for i, v in enumerate(base)]
    if args.vary is None:
        args.vary = '1,2,3,4,5' if args.stops else '1,2,3,4'
    vary = [int(k) - 1 for k in args.vary.split(',')]
    lo, hi = _floats(args.low, 5), _floats(args.high, 5)
    if args.stops:
        _pool(args, rig, base, vary, lo, hi)
        _keep(args.out, args.keep, {}, args.cols, args.rows)
        return
    frames_dir = os.path.join(args.out, 'frames')
    os.makedirs(frames_dir, exist_ok=True)

    entries = []
    rig.set_knobs(base)
    rig.run(args.settle)
    for n in range(args.count):
        knobs = list(base)
        for j, k in enumerate(vary):
            u = _halton(n + 1 + args.seed, PRIMES[j])
            knobs[k] = round(lo[k] + (hi[k] - lo[k]) * u, 3)
        rig.set_knobs(knobs)
        rig.run(args.settle)
        change, light = rig.recent()
        pid = f'p{n + 1:03d}'
        image = pygame.transform.smoothscale(rig.screen, (320, 180))
        pygame.image.save(image, os.path.join(frames_dir, pid + '.png'))
        entries.append({
            'id': pid, 'knobs': knobs,
            'move': round(change, 2), 'light': round(light, 1),
        })
        print(f'{pid}  ' + ' '.join(f'{v:.2f}' for v in knobs)
              + f'  move {change:4.1f}  light {light:5.1f}')

    with open(os.path.join(args.out, 'library.json'), 'w') as f:
        json.dump({
            'mode': _name(args.mode), 'fps': rig.fps, 'settle': args.settle,
            'vary': [k + 1 for k in vary],
            # enough to render the same library again, frames included
            'args': {
                'count': args.count, 'vary': args.vary, 'base': args.base,
                'low': args.low, 'high': args.high, 'seed': args.seed,
                'signal': args.signal,
            },
            'entries': entries,
        }, f, indent=1)
    _boards(args.out, entries, args.cols, args.rows, _name(args.mode))


STRIP_CELL = (480, 270)     # one frame of a pool setting's strip
STRIP_GAP = 0.5             # seconds between a strip's frames
FLAT = 2.0                  # contrast under which a frame is an empty field

TEXTURE = (240, 120, 60, 30)    # widths a strip frame is reduced to, to read its grain

# How much each part of a frame's look counts when two frames are compared. Colour is
# held to a tenth: where a mode turns its palette by the clock, the colour in a frame
# belongs to the moment it was rendered, and no knob setting brings it back. Texture
# carries the most, because the probe is too small to tell one fine grain from another
# and whole regions of busy pictures would pass for one. Layout counts for little:
# two frames of one look differ there by where their broad shapes happen to sit. The
# knob settings count as part of the look, so a stretch of the knobs these numbers
# cannot tell apart still gets its share of frames.
WEIGHTS = {
    'layout': 0.05, 'detail': 0.10, 'texture': 0.25, 'tone': 0.10, 'contrast': 0.05,
    'move': 0.15, 'light': 0.05, 'colour': 0.10, 'knobs': 0.40,
}


def _array(stops, knobs):
    """Stop numbers for `knobs` knobs, stops**3 rows.

    An orthogonal array of strength three: any three knobs see every combination of
    their stops exactly once, and any two see each of theirs `stops` times. It needs
    a prime number of stops, no fewer than the knobs.
    """
    for c in range(stops):
        for b in range(stops):
            for a in range(stops):
                yield [(a + b * j + c * j * j) % stops for j in range(knobs)]


def _features(raw):
    """What a probe frame looks like, as numbers: layout, detail, contrast, colour."""
    import math

    w, h = PROBE
    lum = [
        (raw[i] * 299 + raw[i + 1] * 587 + raw[i + 2] * 114) / 1000
        for i in range(0, len(raw), 3)
    ]
    mean = sum(lum) / len(lum)
    contrast = math.sqrt(sum((v - mean) ** 2 for v in lum) / len(lum))

    def edges(cells, cw, ch):
        across = sum(
            abs(cells[y * cw + x] - cells[y * cw + x + 1])
            for y in range(ch) for x in range(cw - 1)) / (ch * (cw - 1))
        down = sum(
            abs(cells[y * cw + x] - cells[(y + 1) * cw + x])
            for y in range(ch - 1) for x in range(cw)) / ((ch - 1) * cw)
        return [math.log1p(across), math.log1p(down)]

    # where the light sits, in 4x4 blocks of the probe, with the overall level removed
    cw, ch = w // 4, h // 4
    blocks = [0.0] * (cw * ch)
    for y in range(h):
        for x in range(w):
            blocks[y // 4 * cw + x // 4] += lum[y * w + x]
    blocks = [v / 16 for v in blocks]

    # which hues carry the colour, and how much colour there is
    hues = [0.0] * 6
    for i in range(0, len(raw), 3):
        r, g, b = raw[i], raw[i + 1], raw[i + 2]
        top = max(r, g, b)
        chroma = top - min(r, g, b)
        if chroma < 16:
            continue
        if top == r:
            hue = ((g - b) / chroma) % 6
        elif top == g:
            hue = (b - r) / chroma + 2
        else:
            hue = (r - g) / chroma + 4
        hues[int(hue) % 6] += chroma
    total = sum(hues)

    return {
        'layout': [round(v - mean, 1) for v in blocks],
        'detail': [round(v, 3) for v in edges(lum, w, h) + edges(blocks, cw, ch)],
        'contrast': [round(contrast, 2)],
        'colour': [round(v / total, 3) if total else 0.0 for v in hues]
        + [round(total / (len(lum) * 255), 3)],
    }


def _texture(frame):
    """A frame's grain and tones, read from its strip at four times the probe's size.

    Texture is how much the picture changes between one scale and the next, from broad
    forms down to fine grain: a field of small cells and a field of wide folds differ
    here where the probe sees two even greys. Tone is how the brightness is shared out,
    in eight steps from dark to light.
    """
    import math

    w, h = frame.get_size()
    levels = [frame] + [
        pygame.transform.smoothscale(frame, (tw, tw * h // w)) for tw in TEXTURE]
    bands = []
    for fine, coarse in zip(levels, levels[1:]):
        up = pygame.transform.smoothscale(coarse, fine.get_size())
        over, under = fine.copy(), up.copy()
        over.blit(up, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        under.blit(fine, (0, 0), special_flags=pygame.BLEND_RGB_SUB)
        gap = sum(pygame.transform.average_color(over)[:3])
        gap += sum(pygame.transform.average_color(under)[:3])
        bands.append(round(math.log1p(gap / 3), 3))

    raw = pygame.image.tobytes(levels[3], 'RGB')
    tones = [0] * 8
    for i in range(0, len(raw), 3):
        tones[(raw[i] * 299 + raw[i + 1] * 587 + raw[i + 2] * 114) // 32000] += 1
    return {'texture': bands, 'tone': [round(v * 3 / len(raw), 3) for v in tones]}


def _pool(args, rig, base, vary, lo, hi):
    """Every three-knob combination of stops, a strip of frames each, into pool.json.

    The settings come from _array, each stop nudged a little so samples stay off band
    edges and the visits to one stop land on slightly different positions. They run
    shuffled, in one continuous run, each with its own settle time.
    """
    import json
    import math
    import random

    stops = args.stops
    if stops not in (5, 7, 11, 13) or len(vary) > stops:
        raise SystemExit('--stops takes 5, 7, 11 or 13, and no fewer than the knobs')
    rng = random.Random(args.seed)
    settings = []
    for n, row in enumerate(_array(stops, len(vary))):
        knobs = list(base)
        for j, k in enumerate(vary):
            u = row[j] / (stops - 1) + rng.uniform(-args.jitter, args.jitter)
            knobs[k] = round(lo[k] + (hi[k] - lo[k]) * min(1.0, max(0.0, u)), 3)
        settings.append((f'p{n + 1:04d}', knobs))
    order = list(range(len(settings)))
    rng.shuffle(order)

    strips_dir = os.path.join(args.out, 'strips')
    os.makedirs(strips_dir, exist_ok=True)
    strip = pygame.Surface((STRIP_CELL[0] * args.strip, STRIP_CELL[1]))
    entries = [None] * len(settings)
    rig.set_knobs(base)
    rig.run(args.settle)
    for done, n in enumerate(order):
        pid, knobs = settings[n]
        rig.set_knobs(knobs)
        rig.run(args.settle)
        features = _features(rig.probe())
        for i in range(args.strip):
            if i:
                rig.run(STRIP_GAP)
            strip.blit(
                pygame.transform.smoothscale(rig.screen, STRIP_CELL),
                (i * STRIP_CELL[0], 0))
        pygame.image.save(strip, os.path.join(strips_dir, pid + '.jpg'))
        change, light = rig.recent()
        features['move'] = [round(math.log1p(change), 3)]
        features['light'] = [round(light, 1)]
        entries[n] = {
            'id': pid, 'knobs': knobs,
            'move': round(change, 2), 'light': round(light, 1),
            'features': features,
        }
        if (done + 1) % 100 == 0 or done + 1 == len(order):
            print(f'{done + 1} of {len(order)} settings')

    with open(os.path.join(args.out, 'pool.json'), 'w') as f:
        json.dump({
            'mode': _name(args.mode), 'fps': rig.fps, 'settle': args.settle,
            'vary': [k + 1 for k in vary],
            # enough to render the same pool again, strips included
            'args': {
                'stops': stops, 'jitter': args.jitter, 'strip': args.strip,
                'vary': args.vary, 'base': args.base, 'low': args.low,
                'high': args.high, 'seed': args.seed, 'signal': args.signal,
            },
            'entries': entries,
        }, f)


def _keep(out, count, weights, cols, rows):
    """The `count` pool frames that look least like each other, as library.json.

    Each part of a frame's features is scaled so its spread across the pool equals its
    weight, and frames are compared by straight distance. Starting from the most
    ordinary frame, the one farthest from everything kept is added until there are
    enough. A kept frame's reach is how many pool frames it is the nearest to: a large
    reach is a plateau, a small one a nook.
    """
    import json
    import math

    with open(os.path.join(out, 'pool.json')) as f:
        pool = json.load(f)
    weights = {**WEIGHTS, **weights}
    live = [e for e in pool['entries'] if e['features']['contrast'][0] >= FLAT]
    flat = len(pool['entries']) - len(live)
    if not live:
        raise SystemExit('every frame in the pool is flat')
    for e in live:
        e['features'].update(_texture(_frame(out, e['id']).convert(24)))
        e['features']['knobs'] = e['knobs']

    vectors = [[] for _ in live]
    for group, weight in weights.items():
        columns = list(zip(*(e['features'][group] for e in live)))
        means = [sum(c) / len(c) for c in columns]
        spread = sum(
            sum((v - m) ** 2 for v in c) / len(c) for c, m in zip(columns, means))
        scale = math.sqrt(weight / spread) if spread > 0 else 0.0
        for vector, e in zip(vectors, live):
            vector.extend(
                (v - m) * scale for v, m in zip(e['features'][group], means))

    def gap(a, b):
        return sum((x - y) ** 2 for x, y in zip(a, b))

    # every vector is centred, so the most ordinary frame is the one nearest zero
    first = min(range(len(live)), key=lambda i: sum(x * x for x in vectors[i]))
    kept = [first]
    nearest = [gap(v, vectors[first]) for v in vectors]
    owner = [0] * len(live)
    while len(kept) < min(count, len(live)):
        far = max(range(len(live)), key=nearest.__getitem__)
        for i, v in enumerate(vectors):
            d = gap(v, vectors[far])
            if d < nearest[i]:
                nearest[i], owner[i] = d, len(kept)
        kept.append(far)

    entries = []
    for slot, i in enumerate(kept):
        e = {k: v for k, v in live[i].items() if k != 'features'}
        e['reach'] = owner.count(slot)
        entries.append(e)
    library = {k: v for k, v in pool.items() if k != 'entries'}
    library['keep'] = {'count': len(entries), 'weights': weights, 'flat': flat}
    library['entries'] = entries
    with open(os.path.join(out, 'library.json'), 'w') as f:
        json.dump(library, f, indent=1)
    print(f'kept {len(entries)} of {len(live)}; {flat} flat frames left out')
    _boards(out, entries, cols, rows, pool['mode'])


def cmd_keep(args):
    """Keep again from a rendered pool, with other weights or another count."""
    weights = {}
    for item in (args.weights or '').split(','):
        if item:
            group, _, value = item.partition('=')
            if group.strip() not in WEIGHTS:
                raise SystemExit(f'no such weight: {group.strip()}')
            weights[group.strip()] = float(value)
    pygame.init()
    pygame.display.set_mode((1, 1))
    _keep(args.pool, args.keep, weights, args.cols, args.rows)


def _frame(out, pid):
    """A library entry's frame: its own PNG, or the first frame of its strip."""
    png = os.path.join(out, 'frames', pid + '.png')
    if os.path.exists(png):
        return pygame.image.load(png)
    strip = pygame.image.load(os.path.join(out, 'strips', pid + '.jpg'))
    return strip.subsurface((0, 0, STRIP_CELL[0], STRIP_CELL[1]))


def _boards(out, entries, cols, rows, title, prefix='sheet'):
    per = cols * rows
    for start in range(0, len(entries), per):
        chunk = entries[start:start + per]
        sheet = Sheet(
            cols, (len(chunk) + cols - 1) // cols,
            f'{title}  {chunk[0]["id"]}-{chunk[-1]["id"]}')
        for i, e in enumerate(chunk):
            image = _frame(out, e['id'])
            label = e.get('name') or e['id']
            stats = f'move {e["move"]:4.1f}  light {e["light"]:5.1f}'
            if 'reach' in e:
                stats += f'  reach {e["reach"]}'
            sheet.put(i % cols, i // cols, pygame.transform.smoothscale(image, CELL), [
                f'{label}  ' + ' '.join(f'{v:.2f}' for v in e['knobs']), stats])
        sheet.save(os.path.join(out, f'{prefix}-{start // per + 1:02d}.png'))


def cmd_board(args):
    """A sheet of chosen library entries, in the order given, for a storyboard."""
    import json

    with open(os.path.join(args.library, 'library.json')) as f:
        library = json.load(f)
    by_id = {e['id']: e for e in library['entries']}
    chosen = []
    for item in args.ids.split(','):
        pid, _, name = item.partition('=')
        entry = dict(by_id[pid.strip()])
        if name:
            entry['name'] = f'{pid.strip()} {name.strip()}'
        chosen.append(entry)
    pygame.init()
    pygame.display.set_mode((1, 1))
    _boards(args.library, chosen, args.cols, 99, args.title or library['mode'], 'board')


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[1])
    sub = parser.add_subparsers(dest='command', required=True)

    def common(p, scene_required=False):
        p.add_argument('mode', help="the mode's main.py")
        if scene_required:
            p.add_argument('scene', help='a scene file (.json) or a scene folder')
        p.add_argument('--out', required=True, help='where the PNG goes')
        p.add_argument(
            '--fps', type=float, default=None,
            help="frames per virtual second; the scene file's by default, else 30")
        p.add_argument(
            '--signal', choices=('silent', 'synth'), default='silent',
            help='the audio jack; synth fires triggers twice a second')

    s = sub.add_parser('sweep', help='one knob through its travel')
    common(s)
    s.add_argument('--knob', type=int, required=True)
    s.add_argument('--steps', type=int, default=7)
    s.add_argument('--values', help='explicit values, comma-separated')
    s.add_argument('--base', help='all five knobs to start from; - leaves one')
    s.add_argument('--scene', help='start from a scene, its sequence stopped')
    s.add_argument('--settle', type=float, default=4.0, help='seconds per setting')
    s.add_argument(
        '--times', default='0,1,2',
        help='seconds after settling to capture, comma-separated')

    g = sub.add_parser('grid', help='two knobs against each other')
    common(g)
    g.add_argument('--knobs', required=True, help='row knob, column knob: 1,3')
    g.add_argument('--steps', type=int, default=5)
    g.add_argument('--base', help='all five knobs to start from; - leaves one')
    g.add_argument('--scene', help='start from a scene, its sequence stopped')
    g.add_argument('--settle', type=float, default=4.0)

    f = sub.add_parser('film', help='a scene, sampled through its loop')
    common(f, scene_required=True)
    f.add_argument('--every', type=float, default=5.0, help='seconds between frames')
    f.add_argument('--cols', type=int, default=6)
    f.add_argument(
        '--length', type=float, default=30.0,
        help='seconds to run a scene that has no sequence')

    t = sub.add_parser('thumb', help="a scene's 320x240 scene.jpg")
    common(t, scene_required=True)
    t.add_argument(
        '--at', type=float, default=None,
        help="seconds in; the scene file's thumbnail time by default, else 10")

    pr = sub.add_parser('prospect', help='a library of settings, one frame each')
    pr.add_argument('mode', help="the mode's main.py")
    pr.add_argument('--out', required=True, help='folder for frames and library.json')
    pr.add_argument('--fps', type=float, default=30.0)
    pr.add_argument('--signal', choices=('silent', 'synth'), default='silent')
    pr.add_argument('--count', type=int, default=96)
    pr.add_argument(
        '--vary', default=None,
        help='knobs to sample; 1,2,3,4 by default, all five with --stops')
    pr.add_argument(
        '--stops', type=int, default=None,
        help='render a pool instead: every three-knob combination of this many '
        'stops per knob (11 is every 10%%), then keep the most different frames')
    pr.add_argument('--keep', type=int, default=100, help='frames kept from a pool')
    pr.add_argument(
        '--jitter', type=float, default=0.04, help='how far a pool sample strays '
        'from its stop, as a fraction of the travel')
    pr.add_argument('--strip', type=int, default=4, help='frames per pool setting')
    pr.add_argument('--base', help='values for the knobs held still; - leaves one')
    pr.add_argument('--low', default='0,0,0,0,0', help='lower bound per knob')
    pr.add_argument('--high', default='1,1,1,1,1', help='upper bound per knob')
    pr.add_argument('--seed', type=int, default=0, help='offset into the sequence')
    pr.add_argument('--settle', type=float, default=4.0)
    pr.add_argument('--cols', type=int, default=6)
    pr.add_argument('--rows', type=int, default=4)

    kp = sub.add_parser('keep', help='keep again from a rendered pool')
    kp.add_argument('pool', help='a prospect --stops folder')
    kp.add_argument('--keep', type=int, default=100)
    kp.add_argument(
        '--weights', help='layout=0.05,detail=0.1,texture=0.25,tone=0.1,contrast=0.05,'
        'move=0.15,light=0.05,colour=0.1,knobs=0.4; any left out keep these values')
    kp.add_argument('--cols', type=int, default=6)
    kp.add_argument('--rows', type=int, default=4)

    bd = sub.add_parser('board', help='chosen library entries, in order')
    bd.add_argument('library', help='a prospect folder')
    bd.add_argument('ids', help='p012=name,p040=name,... in story order')
    bd.add_argument('--cols', type=int, default=4)
    bd.add_argument('--title')

    args = parser.parse_args(argv)
    if args.command in ('sweep', 'grid') and args.fps is None:
        args.fps = 30.0
    {
        'sweep': cmd_sweep, 'grid': cmd_grid, 'film': cmd_film, 'thumb': cmd_thumb,
        'prospect': cmd_prospect, 'keep': cmd_keep, 'board': cmd_board,
    }[args.command](args)


if __name__ == '__main__':
    main(sys.argv[1:])
