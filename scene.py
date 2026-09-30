"""
EYESY scenes: the card's scene format, the knob sequencer's playback, and scene files.

A scene on the card is a folder under /sdcard/Scenes/ holding scene.json, a 320x240
scene.jpg thumbnail, and optionally knob_seq.json. scene.json carries the mode's folder
name, knobs 1-5, Persist and the two palettes (eyesy.py:590). knob_seq.json is a bare
list of frames, each a list of the five knob values, one per video frame; recalling the
scene plays it on a loop (eyesy.py:782, 1146).

The unit only fills knob_seq.json by recording, which stops at 1001 frames. The loader
checks nothing but the shape (eyesy.py:1205), so a written file can be any length.

A scene file describes a sequence in seconds rather than frames. Each knob is one of:

    0.4                                     held; a held knob stays playable by hand
    [[0, 0.2], [30, 0.6], [60, 0.2]]        keyframes, [seconds, value, ease]
    {"keys": [...], "waves": [...]}         keyframes with waves summed on top
    {"waves": [...], "center": 0.5}         waves around a centre

A key's ease shapes the segment arriving at it: "smooth" (the default), "linear", "in",
"out" or "step". A wave is {"shape": "sine" | "triangle", "depth": d, "period": s,
"phase": 0..1}; depth is the swing either side. Values clamp to 0..1.

A scene file is a loop: every knob must end where it starts, or the wrap jumps. Build
turns it into frames at its fps, which should be the rate the mode actually runs at
on the unit, since the sequencer steps once per drawn frame, not once per 1/30 s.

    python scene.py build scenes/tide-1.json out/
    python scene.py show out/"Tide 1"
"""
import json
import math
import os
import sys

RECORD_LIMIT = 1001     # eyesy.py:1160 stops a recording past MAX_FRAMES = 1000
EASES = ('smooth', 'linear', 'in', 'out', 'step')
SHAPES = ('sine', 'triangle')
WRAP_TOLERANCE = 0.005  # the recorder's own threshold for a knob having moved


class SceneError(ValueError):
    pass


# -- scene files -------------------------------------------------------------------


def _ease(kind, x):
    if kind == 'linear':
        return x
    if kind == 'in':
        return x * x
    if kind == 'out':
        return 1 - (1 - x) * (1 - x)
    if kind == 'step':
        return 0.0 if x < 1 else 1.0
    return x * x * (3 - 2 * x)


def _wave(w, t):
    x = (t / w['period'] + w.get('phase', 0.0)) % 1.0
    if w.get('shape', 'sine') == 'triangle':
        # in phase with the sine: zero at 0, peak at a quarter, trough at three
        v = 1 - 4 * abs((x + 0.25) % 1.0 - 0.5)
    else:
        v = math.sin(2 * math.pi * x)
    return w['depth'] * v


class Curve:
    """One knob's value over time."""

    def __init__(self, spec, where):
        self.keys = []
        self.waves = []
        self.center = None
        if isinstance(spec, (int, float)):
            self.center = float(spec)
        elif isinstance(spec, list):
            self._keys(spec, where)
        elif isinstance(spec, dict):
            unknown = set(spec) - {'keys', 'waves', 'center'}
            if unknown:
                raise SceneError(f'{where}: unknown fields {sorted(unknown)}')
            if 'keys' in spec:
                self._keys(spec['keys'], where)
            if 'center' in spec:
                self.center = float(spec['center'])
            if self.keys and self.center is not None:
                raise SceneError(f'{where}: give keys or a center, not both')
            for i, w in enumerate(spec.get('waves', [])):
                self.waves.append(self._check_wave(w, f'{where} wave {i + 1}'))
            if not self.keys and self.center is None:
                raise SceneError(f'{where}: waves need keys or a center to ride on')
        else:
            raise SceneError(f'{where}: expected a number, a key list or an object')

    def _keys(self, keys, where):
        last = None
        for key in keys:
            if not isinstance(key, list) or len(key) not in (2, 3):
                raise SceneError(f'{where}: a key is [seconds, value] or with an ease')
            t, v = float(key[0]), float(key[1])
            ease = key[2] if len(key) == 3 else 'smooth'
            if ease not in EASES:
                raise SceneError(f'{where}: ease {ease!r} is not one of {EASES}')
            if last is not None and t <= last:
                raise SceneError(f'{where}: key times must increase ({t} after {last})')
            last = t
            self.keys.append((t, v, ease))
        if not self.keys:
            raise SceneError(f'{where}: no keys')

    @staticmethod
    def _check_wave(w, where):
        if not isinstance(w, dict):
            raise SceneError(f'{where}: expected an object')
        unknown = set(w) - {'shape', 'depth', 'period', 'phase'}
        if unknown:
            raise SceneError(f'{where}: unknown fields {sorted(unknown)}')
        if w.get('shape', 'sine') not in SHAPES:
            raise SceneError(f'{where}: shape is one of {SHAPES}')
        if float(w.get('period', 0)) <= 0:
            raise SceneError(f'{where}: needs a positive period in seconds')
        return {
            'shape': w.get('shape', 'sine'),
            'depth': float(w.get('depth', 0.1)),
            'period': float(w['period']),
            'phase': float(w.get('phase', 0.0)),
        }

    def base(self, t):
        if self.center is not None:
            return self.center
        keys = self.keys
        if t <= keys[0][0]:
            return keys[0][1]
        for (t0, v0, _), (t1, v1, ease) in zip(keys, keys[1:]):
            if t <= t1:
                return v0 + (v1 - v0) * _ease(ease, (t - t0) / (t1 - t0))
        return keys[-1][1]

    def raw(self, t):
        return self.base(t) + sum(_wave(w, t) for w in self.waves)

    def value(self, t):
        return min(1.0, max(0.0, self.raw(t)))

    @property
    def held(self):
        return self.center is not None and not self.waves


class SceneFile:
    """A scene and its knob sequence, described in seconds."""

    FIELDS = {
        'name', 'mode', 'fps', 'length', 'persist', 'fg_palette', 'bg_palette',
        'knobs', 'sections', 'notes', 'thumbnail',
    }

    def __init__(self, data, source='scene file'):
        unknown = set(data) - self.FIELDS
        if unknown:
            raise SceneError(f'{source}: unknown fields {sorted(unknown)}')
        for field in ('name', 'mode', 'length', 'knobs'):
            if field not in data:
                raise SceneError(f'{source}: missing {field!r}')
        self.name = str(data['name'])
        if '/' in self.name or self.name.startswith('.'):
            raise SceneError(f'{source}: name is a card folder name, so no slashes')
        self.mode = str(data['mode'])
        self.fps = float(data.get('fps', 30))
        self.length = float(data['length'])
        if self.fps <= 0 or self.length <= 0:
            raise SceneError(f'{source}: fps and length must be positive')
        self.persist = bool(data.get('persist', False))
        self.fg_palette = int(data.get('fg_palette', 0))
        self.bg_palette = int(data.get('bg_palette', 0))
        self.sections = [
            (float(t), str(label)) for t, label in data.get('sections', [])]
        self.notes = data.get('notes', '')
        # seconds into the loop that scene.jpg shows; the card's menus display it
        self.thumbnail = float(data.get('thumbnail', min(10.0, self.length / 2)))

        knobs = data['knobs']
        self.curves = []
        for i in range(1, 6):
            spec = knobs.get(f'knob{i}', knobs.get(str(i)))
            if spec is None:
                raise SceneError(f'{source}: knob{i} is missing; hold it at a value')
            self.curves.append(Curve(spec, f'{source} knob{i}'))
        names = {f'knob{i}' for i in range(1, 6)} | {str(i) for i in range(1, 6)}
        extra = set(knobs) - names
        if extra:
            raise SceneError(f'{source}: unknown knobs {sorted(extra)}')

        for i, c in enumerate(self.curves):
            start, end = c.value(0.0), c.value(self.length)
            if abs(start - end) > WRAP_TOLERANCE:
                raise SceneError(
                    f'{source}: knob{i + 1} starts at {start:.3f} and ends at '
                    f'{end:.3f}, so the loop jumps; end where it starts, and give '
                    f'waves periods that divide the length ({self.length:g} s)')

    @classmethod
    def load(cls, path):
        with open(path) as f:
            return cls(json.load(f), os.path.basename(path))

    @property
    def frame_count(self):
        return max(1, round(self.length * self.fps))

    def knobs_at(self, t):
        return [c.value(t) for c in self.curves]

    def section_at(self, t):
        label = ''
        for start, name in self.sections:
            if t >= start:
                label = name
        return label

    def frames(self):
        """The knob sequence, one row per frame, rounded to the recorder's grain."""
        return [
            [round(v, 4) for v in self.knobs_at(n / self.fps)]
            for n in range(self.frame_count)
        ]

    def scene(self):
        """scene.json, as eyesy.py:590 writes it. Knobs are the sequence's first row."""
        k = [round(v, 4) for v in self.knobs_at(0.0)]
        return {
            'mode': self.mode,
            'knob1': k[0],
            'knob2': k[1],
            'knob3': k[2],
            'knob4': k[3],
            'knob5': k[4],
            'auto_clear': not self.persist,
            'bg_palette': self.bg_palette,
            'fg_palette': self.fg_palette,
        }

    def animated(self):
        return any(not c.held for c in self.curves)


def build(scene_file, out_dir):
    """Write the scene folder for `scene_file` under `out_dir`, and return its path."""
    folder = os.path.join(out_dir, scene_file.name)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, 'scene.json'), 'w') as f:
        json.dump(scene_file.scene(), f, indent=4)
    seq = os.path.join(folder, 'knob_seq.json')
    if scene_file.animated():
        with open(seq, 'w') as f:
            json.dump(scene_file.frames(), f, separators=(',', ':'))
    elif os.path.exists(seq):
        os.remove(seq)                  # a held scene has no sequence on the card
    return folder


# -- the card's format, read back ------------------------------------------------


def load_scene(folder):
    """Read a scene folder the way eyesy.py:687 does. Returns (scene, frames or None).

    Validation matches the unit's, so a folder this rejects is one the unit skips.
    """
    with open(os.path.join(folder, 'scene.json')) as f:
        data = json.load(f)
    for i in range(1, 6):
        if not 0 <= float(data[f'knob{i}']) <= 1:
            raise ValueError(f'knob{i} invalid in {folder}')
    if not isinstance(data['auto_clear'], bool):
        raise ValueError(f'auto_clear invalid in {folder}')
    if not isinstance(data['mode'], str):
        raise ValueError(f'mode invalid in {folder}')
    if int(data['bg_palette']) < 0 or int(data['fg_palette']) < 0:
        raise ValueError(f'palette invalid in {folder}')

    frames = None
    seq = os.path.join(folder, 'knob_seq.json')
    if os.path.isfile(seq):
        with open(seq) as f:
            loaded = json.load(f)
        # eyesy.py:1205 checks the shape and nothing else
        rows = isinstance(loaded, list) and all(
            isinstance(r, (list, tuple)) for r in loaded)
        if rows:
            frames = loaded
        else:
            print(f'{seq}: invalid structure, so the unit plays no sequence')
    return data, frames


class KnobSequence:
    """The sequencer's playback, from eyesy.py:1163.

    Each frame it takes the next row and writes only the knobs whose value differs from
    the last one it wrote, then steps and wraps. A knob held for the whole sequence is
    written once and then left alone, so the hand can take it.
    """

    def __init__(self, frames):
        self.frames = frames or []
        self.index = 0
        self.last = [-1] * 5            # eyesy.py:198
        self.playing = bool(self.frames)

    def run(self, eyesy):
        if not self.playing:
            return
        row = self.frames[self.index]
        for i, value in enumerate(row):
            if value != self.last[i]:
                self.last[i] = value
                eyesy.knob[i] = value
        self.index += 1
        if self.index >= len(self.frames):
            self.index = 0


def recall(eyesy, data, frames):
    """recall_scene (eyesy.py:782) on the simulator's stand-in; returns the sequence.

    The mode is not switched here: the simulator runs the mode it was started with.
    """
    for i in range(5):
        eyesy.knob[i] = float(data[f'knob{i + 1}'])
    eyesy.auto_clear = data['auto_clear']
    eyesy.fg_palette = int(data['fg_palette'])
    eyesy.bg_palette = int(data['bg_palette'])
    if not 0 <= eyesy.fg_palette < len(eyesy.palettes):
        eyesy.fg_palette = 0
    if not 0 <= eyesy.bg_palette < len(eyesy.palettes):
        eyesy.bg_palette = 0
    seq = KnobSequence(frames)
    seq.index = 0                       # knob_seq_play; last values carry over
    return seq


def open_scene(path):
    """A scene file (.json) or a scene folder, as (scene, frames, SceneFile or None)."""
    if os.path.isdir(path):
        data, frames = load_scene(path)
        return data, frames, None
    scene_file = SceneFile.load(path)
    frames = scene_file.frames() if scene_file.animated() else None
    return scene_file.scene(), frames, scene_file


# -- command line ----------------------------------------------------------------


def _describe(data, frames, fps=None):
    lines = [
        f'mode      {data["mode"]}',
        'knobs     ' + '  '.join(f'{data[f"knob{i}"]:.3f}' for i in range(1, 6)),
        f'persist   {"on" if not data["auto_clear"] else "off"}',
        f'palettes  fg {data["fg_palette"]}  bg {data["bg_palette"]}',
    ]
    if frames:
        n = len(frames)
        rates = [fps] if fps else [30, 18]
        span = ', '.join(f'{n / r:.1f} s at {r:g} fps' for r in rates)
        lines.append(f'sequence  {n} frames ({span})')
        if n > RECORD_LIMIT:
            lines.append(
                f'          longer than a recording can be ({RECORD_LIMIT} frames)')
        lo = [min(r[i] for r in frames) for i in range(5)]
        hi = [max(r[i] for r in frames) for i in range(5)]
        lines.append(
            'range     ' + '  '.join(
                'held ' if a == b else f'{a:.2f}-{b:.2f}' for a, b in zip(lo, hi)))
    else:
        lines.append('sequence  none')
    return '\n'.join(lines)


def main(argv):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[1])
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build', help='write the card folder for each scene file')
    b.add_argument('files', nargs='+')
    b.add_argument('out', help='directory the scene folders go in')
    s = sub.add_parser('show', help='describe a scene file or a scene folder')
    s.add_argument('path')
    args = parser.parse_args(argv)

    if args.command == 'build':
        failed = 0
        for path in args.files:
            try:
                scene_file = SceneFile.load(path)
            except (SceneError, OSError, json.JSONDecodeError) as exc:
                print(f'{path}: {exc}', file=sys.stderr)
                failed += 1
                continue
            folder = build(scene_file, args.out)
            data, frames = load_scene(folder)
            print(folder)
            print('  ' + _describe(data, frames, scene_file.fps).replace('\n', '\n  '))
        return 1 if failed else 0

    try:
        data, frames, scene_file = open_scene(args.path)
    except (SceneError, OSError, ValueError, KeyError) as exc:
        print(f'{args.path}: {exc}', file=sys.stderr)
        return 1
    print(_describe(data, frames, scene_file.fps if scene_file else None))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
