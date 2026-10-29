"""Crop each PNG to its non-transparent pixels, with a small margin."""
import sys

from PIL import Image

MARGIN = 20

for path in sys.argv[1:]:
    im = Image.open(path)
    x0, y0, x1, y1 = im.getchannel('A').getbbox()
    im.crop((max(0, x0 - MARGIN), max(0, y0 - MARGIN), min(im.width, x1 + MARGIN),
             min(im.height, y1 + MARGIN))).save(path)
