"""
The EYESY On Screen Display, ported from the OS's engines/python/osd.py.

Copyright Critter & Guitari, BSD 3-Clause. See LICENSE-EYESY_OS.txt.

Geometry is the device's, unscaled: a 598x130 panel in the top left with the palette
strips at its right edge. Drawn onto the display surface after the mode has been blitted
up, so screengrabs never contain it.
"""
import pygame


def draw_knob_slider(screen, eyesy, offx, offy, index, selected):
    color = eyesy.LGRAY
    knob = getattr(eyesy, f'knob{index + 1}')
    pygame.draw.line(screen, color, [offx, offy], [offx + 10, offy], 1)
    pygame.draw.line(screen, color, [offx, offy], [offx, offy + 24], 1)
    pygame.draw.line(screen, color, [offx + 10, offy], [offx + 10, offy + 24], 1)
    pygame.draw.line(screen, color, [offx, offy + 24], [offx + 10, offy + 24], 1)
    pygame.draw.rect(
        screen, color, (offx, offy + 24 - int(24 * knob), 10, int(24 * knob)))
    if selected:
        # not on the hardware — there is no "selected knob" when you have hands
        pygame.draw.rect(screen, (80, 160, 255), (offx - 3, offy - 3, 17, 31), 1)


def draw_vu(screen, eyesy, offx, offy):
    for peak in (eyesy.audio_peak, eyesy.audio_peak_r):
        for i in range(0, 15):
            x = offx + 8 * i
            pygame.draw.rect(screen, eyesy.LGRAY, (x, offy, 7, 8), 1)
        color = eyesy.GREEN
        for i in range(0, int(peak / 2048)):
            if i > 8:
                color = (255, 255, 0)
            if i == 14:
                color = eyesy.RED
            if i < 15:
                pygame.draw.rect(screen, color, (offx + 8 * i + 1, offy + 1, 5, 6))
        offy += 9


def draw_midi(screen, eyesy, offx, offy):
    for i in range(0, 33):
        pygame.draw.line(
            screen, eyesy.LGRAY, [(i * 6) + offx, offy], [(i * 6) + offx, 24 + offy], 1)
    for i in range(0, 5):
        pygame.draw.line(
            screen, eyesy.LGRAY, [offx, (i * 6) + offy],
            [offx + 192, (i * 6) + offy], 1)
    for i in range(0, 128):
        if eyesy.midi_notes[i] > 0:
            pygame.draw.rect(
                screen, eyesy.LGRAY, (offx + 6 * (i % 32), offy + 6 * (i // 32), 6, 6))


def draw_gain_bar(screen, eyesy, offx, offy):
    pygame.draw.rect(screen, eyesy.LGRAY, (offx, offy, 119, 6), 1)
    pygame.draw.rect(screen, eyesy.LGRAY, (offx, offy, int(eyesy.audio_gain * 118), 5))


def draw_color_palette(screen, eyesy):
    for i in range(130):
        pygame.draw.line(
            screen, eyesy.color_picker_bg_preview(i / 130),
            (450, i + 10), (619, i + 10))
    for i in range(85):
        pygame.draw.line(
            screen, eyesy.color_picker(i / 85), (475, i + 35), (599, i + 35))


def render(screen, eyesy, font, active_knob):
    pygame.draw.rect(screen, (0, 0, 0), (10, 10, 598, 130))
    draw_color_palette(screen, eyesy)

    def line(text, x, centery, color=None):
        surf = font.render(text, True, color or eyesy.LGRAY, eyesy.BLACK)
        rect = surf.get_rect()
        rect.x, rect.centery = x, centery
        screen.blit(surf, rect)

    line(f'Mode: (1 of 1) {eyesy.mode}', 20, 30)
    line('SD', 404, 30, eyesy.GREEN)
    line('Scene: None', 20, 55)
    line(f'Screen Size: {eyesy.xres} x {eyesy.yres}', 20, 80)
    line(f'v{eyesy.VERSION}', 380, 80)

    for i, x in enumerate((20, 33, 46, 59, 73)):
        draw_knob_slider(screen, eyesy, x, 105, i, selected=(i + 1 == active_knob))

    draw_midi(screen, eyesy, 89, 105)
    draw_gain_bar(screen, eyesy, 286, 105)
    draw_vu(screen, eyesy, 286, 113)

    pygame.draw.rect(screen, eyesy.LGRAY, (410, 105, 25, 25), 1)
    if eyesy.trig:
        pygame.draw.rect(screen, (255, 255, 0), (410, 105, 25, 25))
