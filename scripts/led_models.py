"""Write the LEDs' 3D models to kicad-src/airfrog.3dshapes/, as VRML with a colour at each corner of the mesh.

XL-1010RGBC.wrl is the RGB LED: a 1.0 x 1.0 x 0.8 mm block with a colour wheel on top, white at the centre, and each
side the colours of its top edge. LED-0402-blue.wrl is the blue status LED: a white 0402 body with tinned ends and a
blue lens.
"""
import colorsys
import math
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the project folder, above scripts/
OUT = os.path.join(ROOT, 'kicad-src', 'airfrog.3dshapes')
MM = 1 / 2.54                # VRML units are 0.1 inch
N = 32                       # mesh cells along each edge of the RGB LED
W, H = 1.0, 0.8              # RGB LED width and height, mm


def wheel(u, v):
    """Colour at (u, v), each -1 to 1: hue by angle round the centre, saturation full at the square's edge."""
    hue = (math.atan2(v, u) / (2 * math.pi)) % 1.0
    sat = min(1.0, max(abs(u), abs(v))) ** 0.6
    return colorsys.hsv_to_rgb(hue, sat, 1.0)


def mesh(pts, cols, faces):
    return ['Shape { appearance Appearance { material Material { diffuseColor 1 1 1 shininess 0.5 } }',
            ' geometry IndexedFaceSet { solid FALSE colorPerVertex TRUE',
            '  coord Coordinate { point [ %s ] }' % ', '.join('%.5f %.5f %.5f' % p for p in pts),
            '  color Color { color [ %s ] }' % ', '.join('%.3f %.3f %.3f' % c for c in cols),
            '  coordIndex [ %s ] } }' % ' '.join(' '.join(str(i) for i in f) + ' -1' for f in faces)]


def grid(n, m, point, colour, flip=False):
    """An n x m mesh of quads, with corners at point(i, j) coloured colour(i, j). KiCad draws a quad only from the
    side its corners run anticlockwise, and flip reverses them."""
    pts, cols, faces = [], [], []
    for j in range(m + 1):
        for i in range(n + 1):
            pts.append(point(i, j))
            cols.append(colour(i, j))
    for j in range(m):
        for i in range(n):
            a = j * (n + 1) + i
            q = (a, a + 1, a + n + 2, a + n + 1)
            faces.append(q[::-1] if flip else q)
    return pts, cols, faces


def rgb():
    h = W / 2
    out = ['#VRML V2.0 utf8']
    out += mesh(*grid(N, N, lambda i, j: ((2 * i / N - 1) * h * MM, (2 * j / N - 1) * h * MM, H * MM),
                      lambda i, j: wheel(2 * i / N - 1, 2 * j / N - 1)))
    # each side from a corner (ux, uy) along (sx, sy)
    for ux, uy, sx, sy in ((-1, -1, 1, 0), (1, -1, 0, 1), (1, 1, -1, 0), (-1, 1, 0, -1)):
        def edge(i, ux=ux, uy=uy, sx=sx, sy=sy):
            return ux + 2 * sx * i / N, uy + 2 * sy * i / N
        out += mesh(*grid(N, 4, lambda i, j: (edge(i)[0] * h * MM, edge(i)[1] * h * MM, H * (1 - j / 4) * MM),
                          lambda i, j: wheel(*edge(i)), flip=True))
    return out


def box(x, y, z, sx, sy, sz, colour, shine=0.3):
    return ('Transform { translation %.4f %.4f %.4f children Shape { appearance Appearance { material Material { '
            'diffuseColor %.3f %.3f %.3f shininess %.2f } } geometry Box { size %.4f %.4f %.4f } } }'
            % (x * MM, y * MM, z * MM, *colour, shine, sx * MM, sy * MM, sz * MM))


def blue_0402():
    """1.0 x 0.5 mm and 0.4 mm high."""
    return ['#VRML V2.0 utf8',
            box(-0.43, 0, 0.15, 0.14, 0.5, 0.3, (0.80, 0.80, 0.82), 0.6),
            box(0.43, 0, 0.15, 0.14, 0.5, 0.3, (0.80, 0.80, 0.82), 0.6),
            box(0, 0, 0.12, 0.72, 0.5, 0.24, (0.95, 0.95, 0.93)),
            box(0, 0, 0.32, 0.72, 0.46, 0.16, (0.10, 0.35, 1.00), 0.8)]


if __name__ == '__main__':
    for name, lines in (('XL-1010RGBC.wrl', rgb()), ('LED-0402-blue.wrl', blue_0402())):
        open(os.path.join(OUT, name), 'w').write('\n'.join(lines) + '\n')
