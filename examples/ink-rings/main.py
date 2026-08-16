import math
import pygame

# Knob1 - base radius
# Knob2 - how far audio pushes the edge
# Knob3 - ring expansion speed
# Knob4 - foreground color
# Knob5 - background color

POINTS = 100        # one per audio sample
MAX_RINGS = 24

xr = 0
yr = 0
rings = []


def setup(screen, eyesy):
    global xr, yr, rings
    xr = eyesy.xres
    yr = eyesy.yres
    rings = []


def draw(screen, eyesy):
    global rings
    eyesy.color_picker_bg(eyesy.knob5)
    color = eyesy.color_picker(eyesy.knob4)

    cx = xr // 2
    cy = yr // 2
    base = int(eyesy.knob1 * yr * .35) + 20
    reach = eyesy.knob2 * yr * .3

    # the scope, wrapped around a circle instead of laid along the bottom
    points = []
    for i in range(POINTS):
        angle = (i / POINTS) * 2 * math.pi
        r = base + (eyesy.audio_in[i] / 32768) * reach
        points.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    pygame.draw.lines(screen, color, True, points, 2)

    # every trigger drops a ring, which widens and thins as it leaves
    if eyesy.trig and len(rings) < MAX_RINGS:
        rings.append(base)

    speed = eyesy.knob3 * 14 + 1
    for i in range(len(rings)):
        rings[i] += speed

    for r in rings:
        width = max(1, 4 - int(r / (yr * .25)))
        pygame.draw.circle(screen, color, (cx, cy), int(r), width)

    rings = [r for r in rings if r < xr]
