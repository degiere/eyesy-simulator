"""
Keyframes: a scene file built from knob settings picked out of a prospect library.

`explore.py prospect` renders a mode at many knob settings and keeps them in a
library. A keyframe spec names some of those settings as keyframes, in story order,
and this turns them into an ordinary scene file for scene.py: each keyframe holds, and
the transit to the next takes as long as the slowest knob needs at its speed limit.

Tempo is part of the composition. Each keyframe's tempo scales its hold and the
transit leaving it, so a scene can open slowly, hurry into a climax and broaden
out again. A cap on each knob's speed still applies at any tempo, because some
knobs (a morph, a zoom) strobe when they move too fast whatever the story wants.

A mode may have a stepped knob, one whose travel is cut into bands where crossing
a band edge changes the picture in a single frame. A spec names it under
"steps", and every crossing then happens in the middle third of its transit
under a veil: a second knob raised to hide the jump. A keyframe marked "snap" is
arrived at in the open instead, for when the jump is the point.

    python keyframes.py keyframes/tide-1.json scenes/tide-1.json

The spec, in JSON:

    name, mode, fps, persist, notes     copied into the scene file
    library        path to library.json, relative to the spec
    keyframes      [{"id", "name", "hold": s, "tempo": x, "snap": bool}, ...]
    limits         {"knob1": units/s, ...}   speed at tempo 1, per moving knob
    caps           {"knob2": units/s}        never exceeded, at any tempo
    min_transit    seconds
    steps          {"knob": "knob3", "count": 4,
                    "veil": {"knob": "knob4", "value": 0.82, "min_third": 2,
                             "direction": "up"}}
                   count may be fractional: a knob scaled before it is floored
                   (floor(4.5 * k)) has a count of 4.5. A veil goes "up" to its
                   value (raised trails) or "down" to it (a dip to dark)
    held           {"knob5": 0.1}            knobs left to the hand
    free           {"knob5": {"center": 0.5,
                              "waves": [{"depth": 0.4, "per_loop": 1}]}}
                   knobs that ride their own waves, whatever the keyframes say
    drift          {"knob1": {"depth": 0.03, "per_loop": 12}, ...}
                   a slight wave on a knob, a whole number of cycles per loop
"""
import json
import math
import os
import sys

KNOBS = [f'knob{i}' for i in range(1, 6)]


def _band(value, count):
    return math.floor(min(value * count, count - 0.001))


def build(spec, base_dir='.'):
    """Return the scene file's dict for a keyframe spec."""
    with open(os.path.join(base_dir, spec['library'])) as f:
        library = {e['id']: e['knobs'] for e in json.load(f)['entries']}

    held = spec.get('held', {})
    free = spec.get('free', {})
    moving = [k for k in KNOBS if k not in held and k not in free]
    index = {k: KNOBS.index(k) for k in KNOBS}
    limits = spec['limits']
    caps = spec.get('caps', {})
    min_transit = float(spec.get('min_transit', 3.0))
    steps = spec.get('steps')

    keyframes = spec['keyframes']
    for s in keyframes:
        if s['id'] not in library:
            raise SystemExit(f'keyframe {s["name"]!r}: {s["id"]} is not in the library')

    keys = {k: [] for k in moving}
    sections = []
    t = 0.0
    for i, s in enumerate(keyframes):
        a = library[s['id']]
        tempo = float(s.get('tempo', 1.0))
        hold = float(s.get('hold', 4.0)) * tempo
        sections.append([round(t, 2), f'{s["name"]} ({s["id"]})'])
        for k in moving:
            keys[k].append([round(t, 2), a[index[k]]])
            keys[k].append([round(t + hold, 2), a[index[k]]])
        t += hold

        nxt = keyframes[(i + 1) % len(keyframes)]
        b = library[nxt['id']]
        need = {
            k: abs(b[index[k]] - a[index[k]]) / limits[k]
            for k in moving if k in limits
        }

        veil = None
        if steps:
            sk = steps['knob']
            n = steps['count']
            crossing = _band(a[index[sk]], n) != _band(b[index[sk]], n)
            if crossing and not nxt.get('snap'):
                veil = steps['veil']
                vk = veil['knob']
                # the stepped knob moves hidden, so its own limit does not apply
                need[sk] = 3 * float(veil.get('min_third', 2.0))
                v = veil['value']
                rise = abs(v - a[index[vk]]) + abs(v - b[index[vk]])
                if veil.get('direction', 'up') == 'down':
                    toward = min
                else:
                    toward = max
                need[vk] = max(need.get(vk, 0.0), rise / limits[vk] * 1.5)

        span = max([min_transit] + [n * tempo for n in need.values()])
        for k, cap in caps.items():
            span = max(span, abs(b[index[k]] - a[index[k]]) / cap)
        span = math.ceil(span)

        if veil:
            one, two = t + span / 3, t + 2 * span / 3
            sk, vk, value = steps['knob'], veil['knob'], veil['value']
            keys[sk] += [[round(one, 2), a[index[sk]]], [round(two, 2), b[index[sk]]]]
            keys[vk] += [
                [round(one, 2), toward(value, a[index[vk]])],
                [round(two, 2), toward(value, b[index[vk]])],
            ]
        t += span

    length = round(t)
    first = library[keyframes[0]['id']]
    knobs = {}
    for k in KNOBS:
        if k in held:
            knobs[k] = held[k]
            continue
        if k in free:
            knobs[k] = {'center': free[k]['center'], 'waves': [
                {
                    'shape': w.get('shape', 'sine'),
                    'depth': w['depth'],
                    'period': length / w['per_loop'],
                    'phase': w.get('phase', 0.0),
                }
                for w in free[k]['waves']
            ]}
            continue
        ks = sorted(keys[k], key=lambda kv: kv[0]) + [[length, first[index[k]]]]
        drift = spec.get('drift', {}).get(k)
        if drift:
            knobs[k] = {'keys': ks, 'waves': [{
                'depth': drift['depth'], 'period': length / drift['per_loop']}]}
        else:
            knobs[k] = ks

    return {
        'name': spec['name'],
        'mode': spec['mode'],
        'fps': spec.get('fps', 30),
        'length': length,
        'persist': spec.get('persist', False),
        'notes': spec.get('notes', ''),
        'sections': sections,
        'knobs': knobs,
    }


def main(argv):
    if len(argv) != 2:
        print('usage: keyframes.py SPEC OUT', file=sys.stderr)
        return 2
    spec_path, out = argv
    with open(spec_path) as f:
        spec = json.load(f)
    scene_file = build(spec, os.path.dirname(os.path.abspath(spec_path)))
    with open(out, 'w') as f:
        json.dump(scene_file, f, indent=4)
    print(f'{out}  {scene_file["length"]} s')
    for start, label in scene_file['sections']:
        print(f'  {start:6.1f}  {label}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
