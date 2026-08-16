import math
import pygame

# Knob1 - base radius
# Knob2 - how far audio pushes the edge
# Knob3 - ring expansion speed
# Knob4 - foreground color
# Knob5 - background color

POINTS = 100        # one per audio sample
MAX_RINGS = 24
FLASH_DECAY = .55   # how fast a hit fades, per frame
FLASH_ALPHA = 120   # peak veil over the frame a trigger lands on

xr = 0
yr = 0
rings = []
flash = 0.0
veil = None         # allocated once; the CM3 cannot afford a surface per frame


def setup(screen, eyesy):
    global xr, yr, rings, flash, veil
    xr = eyesy.xres
    yr = eyesy.yres
    rings = []
    flash = 0.0
    veil = pygame.Surface((xr, yr))


def draw(screen, eyesy):
    global rings, flash
    eyesy.color_picker_bg(eyesy.knob5)
    color = eyesy.color_picker(eyesy.knob4)

    cx = xr // 2
    cy = yr // 2
    base = int(eyesy.knob1 * yr * .35) + 20
    reach = eyesy.knob2 * yr * .3

    # every trigger drops a ring and lights the frame it landed on
    if eyesy.trig:
        flash = 1.0
        if len(rings) < MAX_RINGS:
            rings.append([base, base])

    # a hit veils the whole frame, so it reads even with the scope crowded. Persist
    # keeps every frame, and veiling those would silt the screen up to a flat field in a
    # few triggers, so the ring carries the hit on its own
    if flash > .02:
        if eyesy.auto_clear:
            veil.fill(color)
            veil.set_alpha(int(FLASH_ALPHA * flash))
            screen.blit(veil, (0, 0))
        flash *= FLASH_DECAY
    else:
        flash = 0.0

    # the scope, wrapped around a circle instead of laid along the bottom. it fattens on
    # a hit and settles back over the next few frames
    points = []
    for i in range(POINTS):
        angle = (i / POINTS) * 2 * math.pi
        r = base + (eyesy.audio_in[i] / 32768) * reach
        points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    pygame.draw.lines(screen, color, True, points, 2 + int(flash * 6))

    speed = eyesy.knob3 * 14 + 1
    for ring in rings:
        ring[0] += speed

    # thick at birth, thinning as it travels, so the newest ring is the loudest
    for r, born in rings:
        width = max(1, 14 - int((r - born) / (yr * .05)))
        pygame.draw.circle(screen, color, (cx, cy), int(r), width)

    rings = [ring for ring in rings if ring[0] < xr]
