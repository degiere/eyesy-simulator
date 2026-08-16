import pygame

# Knob1 - bar width
# Knob2 - vertical scale
# Knob3 - flash size on trigger
# Knob4 - foreground color
# Knob5 - background color


def setup(screen, eyesy):
    pass


def draw(screen, eyesy):
    eyesy.color_picker_bg(eyesy.knob5)
    color = eyesy.color_picker(eyesy.knob4)

    width = max(1, int(eyesy.knob1 * eyesy.xres / 100))
    middle = eyesy.yres // 2

    for i in range(100):
        x = int(i * eyesy.xres / 100)
        height = int((eyesy.audio_in[i] / 32768) * eyesy.yres * eyesy.knob2)
        pygame.draw.line(screen, color, [x, middle], [x, middle + height], width)

    if eyesy.trig:
        radius = int(eyesy.knob3 * eyesy.yres * .4) + 4
        pygame.draw.circle(screen, color, [eyesy.xres // 2, middle], radius, 3)
