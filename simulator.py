# Licensed under the BSD 3-Clause License. See LICENSE.
#
# color_palettes.py and osd.py come from Critter & Guitari's EYESY OS and carry their
# copyright under the same terms — see LICENSE-EYESY_OS.txt.
"""
Local development harness for EYESY OS v3 modes.

Runs a mode's setup()/draw() in a desktop pygame window with a stand-in for the `eyesy`
object the device passes in, so a mode can be written and reworked locally before it
goes onto the card. Knobs are driven from the keyboard.

The stand-in mirrors the device rather than approximating it: the same two surfaces, the
device's own cosine palettes, and an audio buffer built the way sound.py builds it —
16-sample averages landing in a circular buffer that is handed to modes without
rotation, seam and all.

Usage:
    .venv/bin/python simulator.py examples/ink-rings/main.py
    .venv/bin/python simulator.py /path/to/your-mode/main.py

Keys, and the hardware control each one stands in for:
    1-5     select the active knob, of the five on the middle row
    left/right
            select the knob to either side, wrapping at the ends
    up/down turn the active knob
    A       Audio In jack, back panel. Plugs the Mac's own output into the mode, so it
            draws to whatever is playing. Press again to unplug
    O       On Screen Display button, top left — mode, screen size, knob meters, MIDI
            grid, gain and VU, trigger, palette strips
    T       Trigger button, bottom right on the panel. Tap fires once; holding swaps the
            input for a sweeping sine and keeps firing
    P       Persist button, top right — stops the screen clearing between frames, so
            drawing accumulates (auto_clear)
    G       Screenshot button, writes grab-N.png in the repo root
    [ ]     foreground palette back / forward (Shift + Mode Back/Fwd)
    - =     background palette back / forward (Shift + Scene Back/Fwd)
    , .     audio gain down / up (Shift + Knob 1)
    Esc     quit

See:
* https://docs.critterandguitari.com/EYESY/ey_os_3/
* https://github.com/critterandguitari/EYESY_OS
"""
import importlib.util
import math
import os
import random
import sys

import pygame
from pygame.locals import *

import osd

XRES, YRES = 1280, 720
AUDIO_SAMPLES = 100

# device audio path, from engines/python/sound.py
SAMPLE_RATE = 32000
AVERAGE_WINDOW = 16          # samples averaged into one buffer entry
TRIGGER_THRESHOLD = 20000    # main.py fires eyesy.trig above this peak
TRIGGER_HOLD_FRAMES = 10     # eyesy.py:1086 counts this before a held Trigger repeats

HERE = os.path.dirname(os.path.abspath(__file__))


def load_palettes():
    """The device's palette table, vendored alongside this file.

    Set EYESY_OS to a checkout of the OS to read the table from there instead, which is
    worth doing if the palettes ever change upstream.
    """
    os_root = os.environ.get('EYESY_OS')
    if os_root:
        path = os.path.join(os_root, 'engines', 'python', 'color_palettes.py')
        try:
            spec = importlib.util.spec_from_file_location('color_palettes', path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module.abcd_palettes
        except Exception as e:
            print(
                f'EYESY_OS is set but {path} did not load ({e}); '
                'using the vendored table')

    # loaded by path rather than by name: a mode's own folder goes on sys.path ahead of
    # this one, and the working directory is wherever it is
    spec = importlib.util.spec_from_file_location(
        'color_palettes', os.path.join(HERE, 'color_palettes.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.abcd_palettes


class SilentSignal:
    """An unplugged input: the converter's noise floor and nothing else.

    This is what `audio_in` holds on the device with no cable in the jack. Peaks stay
    four orders of magnitude below the 20000 trigger threshold, so a mode sits still
    until real signal or the Trigger button gives it one.
    """

    label = 'silent'

    def sample(self, n):
        v = random.uniform(-120, 120)
        return v, v


class SynthSignal:
    """A tone with a repeating transient, so triggers fire on their own."""

    label = 'synth'

    def sample(self, n):
        """Return one stereo frame at the device's sample rate.

        The tone carries a phase offset and a frequency that is not a multiple of the
        envelope rate. Line them up and every transient onset lands on a zero crossing,
        which makes the loudest moment silent and nothing ever crosses the trigger
        threshold.
        """
        t = n / SAMPLE_RATE
        env = math.exp(-((t * 2) % 1.0) * 6)              # decay twice a second
        phase = 2 * math.pi * 97 * t + 1.0
        tone = math.sin(phase) + .5 * math.sin(2 * phase) + .25 * math.sin(3 * phase)
        # levels chosen so transients clear the trigger threshold at the unit's default
        # gain of 0.25 while the sustain stays well under it
        v = (2500 + 7000 * env) * tone * .5 + random.uniform(-120, 120)
        return v, v


def _is_live(signal):
    """True for a capture source, which holds OS resources until closed.

    The generated signals are pure functions of the sample index and need no teardown,
    so `close` is what separates a cable from a simulation.
    """
    return hasattr(signal, 'close')


class AudioSource:
    """Stands in for the ALSA capture process in sound.py.

    Takes a signal at the device's sample rate, averages it in blocks of 16, and writes
    those averages into a 100-entry circular buffer. Modes get a straight copy of that
    buffer, so the write seam drifts across the array exactly as it does on hardware.
    """

    def __init__(self, signal=None):
        self.signal = signal or SilentSignal()
        self.buffer = [0] * AUDIO_SAMPLES
        self.buffer_r = [0] * AUDIO_SAMPLES
        self.write_index = 0
        self.peak = 0
        self.peak_r = 0
        self._max_peak = 0
        self._max_peak_r = 0
        self.sample_pos = 0        # absolute sample index
        self.writes_pending = 0.0  # fractional carry between frames

    def advance(self, frames_per_second, gain_setting):
        """Produce one video frame's worth of buffer writes."""
        gain = (gain_setting * gain_setting * 50) + 1

        writes_per_second = SAMPLE_RATE / AVERAGE_WINDOW
        self.writes_pending += writes_per_second / frames_per_second

        while self.writes_pending >= 1:
            self.writes_pending -= 1

            block = [
                self.signal.sample(self.sample_pos + i) for i in range(AVERAGE_WINDOW)]
            self.sample_pos += AVERAGE_WINDOW

            avg = sum(s[0] for s in block) / AVERAGE_WINDOW * gain
            avg_r = sum(s[1] for s in block) / AVERAGE_WINDOW * gain

            avg = max(-32768, min(32767, avg))
            avg_r = max(-32768, min(32767, avg_r))

            if avg > self._max_peak:
                self._max_peak = avg
            if avg_r > self._max_peak_r:
                self._max_peak_r = avg_r

            self.buffer[self.write_index] = avg
            self.buffer_r[self.write_index] = avg_r
            self.write_index = (self.write_index + 1) % AUDIO_SAMPLES

            # peak commits once per wrap, as it does on the device
            if self.write_index == 0:
                self.peak = self._max_peak
                self.peak_r = self._max_peak_r
                self._max_peak = 0
                self._max_peak_r = 0


class Eyesy:
    """Stand-in for the object EYESY OS v3 hands to setup() and draw()."""

    def __init__(self, mode_root='./'):
        self.knob1 = 0.5
        self.knob2 = 0.5
        self.knob3 = 0.5
        self.knob4 = 0.5
        # the background knob starts at zero, not centred: mid-scale on the slot 0
        # picker is a flat mid grey that every mode then draws over
        self.knob5 = 0.0

        self.xres = XRES
        self.yres = YRES

        # 100 signed 16-bit samples per channel, as the device provides
        self.audio_in = [0] * AUDIO_SAMPLES
        self.audio_in_r = [0] * AUDIO_SAMPLES
        self.audio_peak = 0
        self.audio_peak_r = 0
        self.trig = False

        self.midi_notes = [0] * 128
        self.midi_note_new = False

        self.mode = os.path.basename(os.path.normpath(mode_root)) or 'local'
        self.mode_root = mode_root

        self.bg_color = (0, 0, 0)
        self.auto_clear = True
        self.audio_gain = 0.25   # the OSD's gain bar reads this

        self.VERSION = '3.1'
        self.LGRAY = (200, 200, 200)
        self.GREEN = (0, 255, 0)
        self.RED = (255, 0, 0)
        self.BLACK = (0, 0, 0)

        self.palettes = load_palettes()
        self.fg_palette = 0
        self.bg_palette = 0
        self.color_lfo_inc = 0
        self.color_lfo_index = 0

    # -- color, ported from engines/python/eyesy.py --

    def get_color_from_phase(self, val, palette_index):
        t = float(val)
        p = self.palettes[palette_index % len(self.palettes)]
        a, b, c, d = p["a"], p["b"], p["c"], p["d"]
        color = [a[i] + b[i] * math.cos(6.283185 * (c[i] * t + d[i])) for i in range(3)]
        return tuple(max(0, min(1, ch)) * 255 for ch in color)

    def color_picker_original(self, val):
        """The v2.3 picker the device still uses for palette slot 0."""
        c = float(val)

        rando = random.randrange(0, 2)
        color = (rando * 255, rando * 255, rando * 255)

        if c > .02:
            rando = random.randrange(0, 255)
            color = (rando, rando, rando)
        if c > .04:
            color = (50, 50, 50)
        if c > .06:
            color = (100, 100, 100)
        if c > .08:
            color = (150, 150, 150)
        if c > .10:
            color = (150, 150, 150)
        if c > .12:
            color = (200, 200, 200)
        if c > .14:
            color = (250, 250, 250)
        if c > .16:
            r = math.sin(c * 2 * math.pi) * .5 + .5
            g = math.sin(c * 4 * math.pi) * .5 + .5
            b = math.sin(c * 8 * math.pi) * .5 + .5
            color = (r * 255, g * 255, b * 255)
        if c > .96:
            color = (
                random.randrange(0, 255), random.randrange(0, 255),
                random.randrange(0, 255))
        if c > .98:
            color = (
                random.randrange(0, 2) * 255, random.randrange(0, 2) * 255,
                random.randrange(0, 2) * 255)
        return color

    def color_picker_bg_original(self, val):
        c = float(val)
        r = (1 - (math.cos(c * 3 * math.pi) * .5 + .5)) * c
        g = (1 - (math.cos(c * 7 * math.pi) * .5 + .5)) * c
        b = (1 - (math.cos(c * 11 * math.pi) * .5 + .5)) * c
        return (r * 255, g * 255, b * 255)

    def color_picker(self, val):
        if self.fg_palette == 0:
            return self.color_picker_original(val)
        return self.get_color_from_phase(val, self.fg_palette)

    def color_picker_bg(self, val):
        self.bg_color = self.color_picker_bg_preview(val)
        return self.bg_color

    def color_picker_bg_preview(self, val):
        if self.bg_palette == 0:
            return self.color_picker_bg_original(val)
        return self.get_color_from_phase(val, self.bg_palette)

    def color_picker_lfo(self, knob_val, inc_amt=.1):
        self.color_lfo_index = (self.color_lfo_index + self.color_lfo_inc) % 2
        if knob_val <= .5:
            return self.color_picker((knob_val * 2) % 1)
        self.color_lfo_inc = (knob_val - .5) * 2 * inc_amt
        if self.color_lfo_index <= 1:
            return self.color_picker(self.color_lfo_index)
        return self.color_picker(2 - self.color_lfo_index)


def main(setup, draw, mode_root, signal=None):
    pygame.init()
    hwscreen = pygame.display.set_mode((XRES, YRES))
    pygame.display.set_caption('EYESY local harness')

    # the device draws to its own surface and blits it up, which is what makes Persist
    # survive a mode change — the surface is never rebuilt
    mode_screen = pygame.Surface((XRES, YRES))

    eyesy = Eyesy(mode_root=mode_root)
    # what `A` falls back to when the cable comes out again
    unplugged_signal = signal if signal and not _is_live(signal) else SilentSignal()
    idle_signal = signal or unplugged_signal
    hold_signal = SynthSignal()
    audio = AudioSource(idle_signal)
    print(f'audio source: {audio.signal.label}')
    setup(hwscreen, eyesy)

    clock = pygame.time.Clock()
    active_knob = 1
    gain = 0.25          # config.json default on the unit
    fps = 30
    grab_index = 0
    trigger_held = False
    trigger_td = 0
    show_osd = True     # `O` hides it; screengrabs never contain it either way
    osd_font = pygame.font.Font(None, 22)
    arrow_td = 0        # frames an arrow has been held, as the device counts them

    def turn_knob(step):
        name = f'knob{active_knob}'
        value = min(1.0, max(0.0, getattr(eyesy, name) + step))
        setattr(eyesy, name, value)
        return name, value

    def toggle_audio_input():
        """`A` is the Audio In jack: pressing it plugs the Mac into the mode.

        The tap is built on the first press and torn down on the second, so an idle
        simulator holds no capture device.
        """
        nonlocal idle_signal
        if _is_live(idle_signal):
            idle_signal.close()
            idle_signal = unplugged_signal
            print(f'audio in: unplugged ({idle_signal.label})')
            return

        from system_audio import SystemAudioError, SystemAudioSignal
        try:
            idle_signal = SystemAudioSignal(exclude_pids=[os.getpid()])
        except SystemAudioError as exc:
            print(f'audio in: {exc}')
            return
        print(f'audio in: {idle_signal.label}')

    def shutdown():
        if _is_live(idle_signal):
            idle_signal.close()
        pygame.quit()
        sys.exit()

    def report():
        print(
            f'persist {"on" if not eyesy.auto_clear else "off"}  '
            f'fg {eyesy.fg_palette}:{eyesy.palettes[eyesy.fg_palette]["name"]}  '
            f'bg {eyesy.bg_palette}:{eyesy.palettes[eyesy.bg_palette]["name"]}  '
            f'gain {gain:.2f}')

    while True:
        for event in pygame.event.get():
            if event.type == QUIT:
                shutdown()
            if event.type == KEYUP:
                if event.key == K_t:
                    trigger_held = False
            if event.type == KEYDOWN:
                if event.key == K_ESCAPE:
                    shutdown()
                if K_1 <= event.key <= K_5:
                    active_knob = event.key - K_0
                    print(f'knob{active_knob} active')
                if event.key in (K_LEFT, K_RIGHT):
                    # the row wraps, the way Mode Fwd/Back does on the panel
                    step = 1 if event.key == K_RIGHT else -1
                    active_knob = (active_knob - 1 + step) % 5 + 1
                    print(f'knob{active_knob} active')
                if event.key == K_t:
                    eyesy.trig = True
                    trigger_held = True
                    trigger_td = 0
                if event.key == K_a:
                    toggle_audio_input()
                if event.key == K_o:
                    show_osd = not show_osd
                if event.key == K_p:
                    eyesy.auto_clear = not eyesy.auto_clear
                    report()
                if event.key == K_g:
                    path = os.path.join(HERE, f'grab-{grab_index}.png')
                    pygame.image.save(mode_screen, path)
                    print(f'grabbed {path}')
                    grab_index += 1
                if event.key in (K_LEFTBRACKET, K_RIGHTBRACKET):
                    step = 1 if event.key == K_RIGHTBRACKET else -1
                    eyesy.fg_palette = (eyesy.fg_palette + step) % len(eyesy.palettes)
                    report()
                if event.key in (K_MINUS, K_EQUALS):
                    step = 1 if event.key == K_EQUALS else -1
                    eyesy.bg_palette = (eyesy.bg_palette + step) % len(eyesy.palettes)
                    report()
                if event.key in (K_COMMA, K_PERIOD):
                    step = 0.05 if event.key == K_PERIOD else -0.05
                    gain = min(1.0, max(0.0, gain + step))
                    eyesy.audio_gain = gain
                    report()
                if event.key in (K_UP, K_DOWN):
                    name, value = turn_knob(0.05 if event.key == K_UP else -0.05)
                    print(f'{name} = {value:.2f}')
                    arrow_td = 0

        held = pygame.key.get_pressed()
        if held[K_UP] or held[K_DOWN]:
            arrow_td += 1
            if arrow_td > 10:
                name, value = turn_knob(0.02 if held[K_UP] else -0.02)
                if arrow_td % 6 == 0:
                    print(f'{name} = {value:.2f}')
        else:
            arrow_td = 0

        # A tap on Trigger is one trigger, fired on the keypress. Keep the key down past
        # the device's repeat count and the jack picks up a tone that peaks twice a
        # second, so the meter pulses and triggers keep arriving.
        trigger_td = trigger_td + 1 if trigger_held else 0
        audio.signal = hold_signal if trigger_td > TRIGGER_HOLD_FRAMES else idle_signal

        audio.advance(fps, gain)
        eyesy.audio_in[:] = audio.buffer
        eyesy.audio_in_r[:] = audio.buffer_r
        eyesy.audio_peak = audio.peak
        eyesy.audio_peak_r = audio.peak_r
        if audio.peak > TRIGGER_THRESHOLD or audio.peak_r > TRIGGER_THRESHOLD:
            eyesy.trig = True
        # eyesy.py:1086 — a held Trigger keeps firing once the repeat count is up,
        # whatever the input is doing
        if trigger_td > TRIGGER_HOLD_FRAMES:
            eyesy.trig = True

        if eyesy.auto_clear:
            mode_screen.fill(eyesy.bg_color)
        draw(mode_screen, eyesy)
        hwscreen.blit(mode_screen, (0, 0))
        if show_osd:
            osd.render(hwscreen, eyesy, osd_font, active_knob)
        pygame.display.update()

        eyesy.trig = False
        eyesy.midi_note_new = False
        clock.tick(fps)


if __name__ == "__main__":
    import argparse
    import importlib

    parser = argparse.ArgumentParser()
    parser.add_argument('file', type=argparse.FileType('r'))
    parser.add_argument(
        '--signal', choices=('silent', 'synth', 'system'), default='silent',
        help='input on the audio jack. "silent" is an unplugged input, as the '
             'device idles; "synth" is a tone that peaks twice a second so '
             'triggers fire unattended; "system" captures whatever the Mac is '
             'playing, so a mode can be developed against real music.')
    args = parser.parse_args()

    if args.signal == 'system':
        from system_audio import SystemAudioError, SystemAudioSignal
        try:
            signal = SystemAudioSignal(exclude_pids=[os.getpid()])
        except SystemAudioError as exc:
            raise SystemExit(f'system audio capture unavailable: {exc}')
    elif args.signal == 'synth':
        signal = SynthSignal()
    else:
        signal = SilentSignal()

    module_path = os.path.splitext(args.file.name)[0]
    mode_root = os.path.dirname(os.path.abspath(args.file.name)) + os.sep

    # the mode's own folder has to be importable, so a mode anywhere on disk loads the
    # same way a mode sitting next to this script does
    sys.path.insert(0, mode_root)

    importlib.invalidate_caches()
    module = importlib.import_module(os.path.basename(module_path))

    main(module.setup, module.draw, mode_root, signal)
