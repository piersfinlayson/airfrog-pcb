"""Generate the Airfrog schematic for one ESP32 module variant, c3 or s3. Layout units are U = 2.54 mm."""
import os
import subprocess
import sys

from kisch import Schematic, U

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the project folder, above scripts/
SRC = os.path.join(ROOT, 'kicad-src')                                 # the committed KiCad project files
LIB = 'airfrog'                                                       # project symbol library

# value -> (footprint, LCSC, manufacturer and MPN)
PASSIVES = {
    '0.1uF': ('Capacitor_SMD:C_0402_1005Metric', 'C1525', 'Samsung Electro-Mechanics CL05B104KO5NNNC'),
    '1uF': ('Capacitor_SMD:C_0402_1005Metric', 'C52923', 'Samsung Electro-Mechanics CL05A105KA5NQNC'),
    '4.7uF': ('Capacitor_SMD:C_0402_1005Metric', 'C23733', 'Samsung Electro-Mechanics CL05A475MP5NRNC'),
    '10uF': ('Capacitor_SMD:C_0603_1608Metric', 'C19702', 'Samsung Electro-Mechanics CL10A106KP8NNNC'),
    '2K2': ('Resistor_SMD:R_0402_1005Metric', 'C25879', 'UNI-ROYAL(Uniroyal Elec) 0402WGF2201TCE'),
    '4K7': ('Resistor_SMD:R_0402_1005Metric', 'C25900', 'UNI-ROYAL(Uniroyal Elec) 0402WGF4701TCE'),
    '10K': ('Resistor_SMD:R_0402_1005Metric', 'C25744', 'UNI-ROYAL(Uniroyal Elec) 0402WGF1002TCE'),
}

# value -> second-source LCSC part, written to the LCSC alt field
ALTS = {}

# Each variant's module and the module pin of each signal. RUN_DRV and BOOTSEL_DRV are on pins with no pull-up
# and no high glitch at power-up, so the MOSFETs stay off until the firmware drives them. The One ROM signals are
# on the module edge that faces the header on the PCB.
VARIANTS = {
    'c3': {
        'lib_id': LIB + ':ESP32-C3-MINI-1', 'value': 'ESP32-C3-MINI-1',
        'props': {'LCSC': 'C41349510', 'LCSC alt': 'C2838502', 'Comment': 'Espressif ESP32-C3-MINI-1-H4X'},
        'pins': {'EN': 8, 'BOOT': 23, 'TXD0': 31, 'RXD0': 30, 'RUN_DRV': 21, 'BOOTSEL_DRV': 18,
                 'SWCLK_ESP': 20, 'SWDIO_ESP': 19, 'SEL_A': 12, 'SEL_B': 13, 'RECOVERY': 16, 'IO2': 5, 'IO8': 22},
        'nc': [6, 26, 27],
        'straps': ['IO2', 'IO8'],   # floating at reset, pulled up for SPI and download boot
    },
    's3': {
        'lib_id': 'RF_Module:ESP32-S3-MINI-1', 'value': 'ESP32-S3-MINI-1',
        'props': {'LCSC': 'C3013941', 'Comment': 'Espressif ESP32-S3-MINI-1-N4R2'},
        'pins': {'EN': 45, 'BOOT': 4, 'TXD0': 39, 'RXD0': 40, 'RUN_DRV': 25, 'BOOTSEL_DRV': 20,
                 'SWCLK_ESP': 19, 'SWDIO_ESP': 21, 'SEL_A': 17, 'SEL_B': 18, 'RECOVERY': 16},
        'nc': list(range(5, 16)) + [22, 23, 24] + list(range(26, 39)) + [41, 44],
        'straps': [],
    },
}

Q_FET = {'LCSC': 'C5190214', 'Comment': 'Guangdong Hottech 2N7002DW', 'Footprint': 'Package_TO_SOT_SMD:SOT-363_SC-70-6'}
Q_BJT = {'LCSC': 'C41375125', 'Comment': 'hongjiacheng MMDT3904'}
DIODE = {'LCSC': 'C7502694', 'Comment': 'hongjiacheng BAT54WS', 'Footprint': 'Diode_SMD:D_SOD-323'}
BUTTON = {'LCSC': 'C720477', 'Comment': 'XUNPU TS-1088-AR02016', 'Footprint': 'Button_Switch_SMD:SW_SPST_TS-1088-xR020'}


def commit_date():
    """Date of HEAD's commit, or '' if there isn't one."""
    r = subprocess.run(['git', '-C', ROOT, 'log', '-1', '--format=%cs'], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True)
    return r.stdout.strip() if r.returncode == 0 else ''


VARIANT = sys.argv[1] if len(sys.argv) > 1 else 'c3'
V = VARIANTS[VARIANT]
PROJECT = 'airfrog-' + VARIANT

s = Schematic(PROJECT, paper='A4', title='Airfrog (%s)' % V['value'], date=commit_date(),
              rev=open(os.path.join(ROOT, 'VERSION')).read().strip(),
              company='piers.rocks', comments=['(c) 2026 piers.rocks'])


def P(x, y):
    return (round(x * U, 4), round(y * U, 4))


def passive(ref, value, x, y, rot=0, fields=None, dnp=False):
    """Two-pin R or C centred at (x, y) in U. Unrotated, pin 1 is at the top. Rotated 90, pin 1 is on the left."""
    fp, lcsc, comment = PASSIVES[value]
    lib = 'Device:R_Small' if ref.startswith('R') else 'Device:C_Small'
    props = {'Footprint': fp, 'Datasheet': '~', 'LCSC': lcsc, 'Comment': comment}
    if value in ALTS:
        props['LCSC alt'] = ALTS[value]
    return s.add(lib, ref, value, P(x, y), rot=rot, fields=fields, dnp=dnp, props=props)


def vpassive(ref, value, x, ytop, top=None, bottom='GND', fields=None):
    """Vertical R or C with pin 1 at (x, ytop). top/bottom: power symbol names or None."""
    p = passive(ref, value, x, ytop + 1, fields=fields)
    if top:
        s.pin_power(p, 1, top)
    if bottom:
        s.pin_power(p, 2, bottom)
    return p


def pull_up(ref, value, x, y, net, length=3):
    """Resistor from +3V3 down to (x, y), then a wire right to a label for net."""
    vpassive(ref, value, x, y - 2, top='+3V3', bottom=None)
    W((x, y), (x + length, y))
    s.label(net, P(x + length, y), 0)


def block(x0, y0, x1, y1, title):
    s.rect(P(x0, y0), P(x1, y1))
    s.text(title, P(x0 + 1, y0 + 1.2), size=1.524, bold=True)


def W(*pts):
    s.wire(*[P(*p) for p in pts])


def fields_at(ref_xy, value_xy, just='left'):
    """Reference and Value fields at points in U."""
    return {'Reference': (P(*ref_xy)[0], P(*ref_xy)[1], just, 'center', False),
            'Value': (P(*value_xy)[0], P(*value_xy)[1], just, 'center', False)}


# ------------------------------------------------------------ ESP32 module

def module(ox, oy):
    block(ox, oy, ox + 38, oy + 34, 'ESP32 module')
    cx, cy = ox + 19, oy + 18
    if V['lib_id'].startswith(LIB + ':'):
        s.need(V['lib_id'], path=os.path.join(SRC, LIB + '.kicad_sym'))
    u = s.add(V['lib_id'], 'U1', V['value'], P(cx, cy), props=dict(V['props']),
              fields=fields_at((cx + 2, cy + 13), (cx + 2, cy + 14)))
    vdd = u.pin(3)
    W((cx, vdd[1] / U), (cx, oy + 5), (cx + 13, oy + 5))
    s.power('+3V3', P(cx + 5, oy + 5))
    vpassive('C4', '0.1uF', cx + 13, oy + 5)
    if VARIANT == 'c3':
        vpassive('C5', '10uF', cx + 9, oy + 5)
    else:
        for i, line in enumerate(("No 10uF as there isn't sufficient space.", 'The ESP32 module contains 10uF.')):
            s.text(line, P(ox + 1, oy + 3 + i))
    s.pin_power(u, 1, 'GND')
    for net, pin in V['pins'].items():
        s.pin_label(u, pin, net)
    for pin in V['nc']:
        s.pin_nc(u, pin)


# ------------------------------------------------------------ boot, reset and strapping

def boot(ox, oy):
    block(ox, oy, ox + 31, oy + 26, 'Reset and boot mode')
    # EN: 10k and 1 uF delay the release of reset until 3V3 is up
    pull_up('R1', '10K', ox + 5, oy + 7, 'EN', length=8)
    vpassive('C1', '1uF', ox + 9, oy + 7)
    pull_up('R2', '10K', ox + 5, oy + 13, 'BOOT', length=8)
    for i, net in enumerate(V['straps']):
        pull_up('R%d' % (11 + i), '10K', ox + 5, oy + 18 + 5 * i, net, length=8)


# ------------------------------------------------------------ 3.3 V regulator

def ldo(ox, oy):
    block(ox, oy, ox + 38, oy + 12, '3.3 V regulator')
    u = s.add('Regulator_Linear:AP2112K-3.3', 'U2', 'AP2112K-3.3', P(ox + 20, oy + 6),
              props={'LCSC': 'C51118', 'Comment': 'Diodes Incorporated AP2112K-3.3TRG1'})
    W((ox + 17, oy + 5), (ox + 7, oy + 5))
    s.power('+5V', P(ox + 7, oy + 5))
    s.power('PWR_FLAG', P(ox + 10, oy + 5))
    W((ox + 17, oy + 6), (ox + 15, oy + 6), (ox + 15, oy + 5))
    vpassive('C2', '4.7uF', ox + 13, oy + 5)
    W((ox + 23, oy + 5), (ox + 31, oy + 5))
    s.power('+3V3', P(ox + 31, oy + 5))
    vpassive('C3', '4.7uF', ox + 27, oy + 5)
    vpassive('R13', '2K2', ox + 31, oy + 5)   # takes the SWD clamp current when the module draws little
    s.pin_nc(u, 4)
    s.pin_power(u, 2, 'GND')
    s.power('PWR_FLAG', P(ox + 4, oy + 8))
    s.power('GND', P(ox + 4, oy + 9))
    W((ox + 4, oy + 8), (ox + 4, oy + 9))


# ------------------------------------------------------------ One ROM header

def onerom(ox, oy):
    block(ox, oy, ox + 34, oy + 15, 'One ROM header')
    j = s.add('Connector_Generic:Conn_02x05_Odd_Even', 'J1', 'One ROM', P(ox + 15, oy + 10),
              fields=fields_at((ox + 14, oy + 6.5), (ox + 16, oy + 6.5)),
              props={'Footprint': 'Connector_PinSocket_2.54mm:PinSocket_2x05_P2.54mm_Vertical',
                     'LCSC': 'C2897406', 'Comment': 'HCTL PM254-2-05-Z-8.5'})
    # J1 is on the underside, so its odd pins are on row 2, the row further from One ROM's edge
    s.pin_power(j, 2, '+5V', length=U)
    for pin in (1, 7, 9):
        s.pin_power(j, pin, 'GND', length=U)
    for pin, net in ((3, 'SWDIO'), (4, 'RUN'), (5, 'SWCLK'), (6, 'BOOTSEL'), (8, 'SEL_B'), (10, 'SEL_A')):
        s.pin_label(j, pin, net)


# ------------------------------------------------------------ RUN and BOOTSEL drive

def drive(ox, oy):
    block(ox, oy, ox + 34, oy + 22, 'RUN and BOOTSEL drive')
    for unit, (x, y, drv, line, rpd) in enumerate(((ox + 17, oy + 7, 'RUN_DRV', 'RUN', 'R3'),
                                                   (ox + 17, oy + 16, 'BOOTSEL_DRV', 'BOOTSEL', 'R4')), 1):
        q = s.add('Transistor_FET:Q_Dual_NMOS_S1G1D2S2G2D1', 'Q1', '2N7002DW', P(x, y), unit=unit,
                  props=dict(Q_FET), fields=fields_at((x + 2, y - 0.5), (x + 2, y + 0.5)))
        g = q.pin({1: 2, 2: 5}[unit])
        d = q.pin({1: 6, 2: 3}[unit])
        W((g[0] / U, y), (x - 9, y))
        s.label(drv, P(x - 9, y), 180)
        vpassive(rpd, '10K', x - 4, y)
        W((d[0] / U, d[1] / U), (d[0] / U, y - 3), (x + 6, y - 3))
        s.label(line, P(x + 6, y - 3), 0)
        s.pin_power(q, {1: 1, 2: 4}[unit], 'GND')


# ------------------------------------------------------------ SWD protection

def swd(ox, oy):
    block(ox, oy, ox + 34, oy + 17, 'SWD, 5 V protection')
    for i, (line, ref_r, ref_d) in enumerate((('SWCLK', 'R5', 'D1'), ('SWDIO', 'R6', 'D2'))):
        y = oy + 8 + 6 * i
        x = ox + 12
        W((x - 4, y), (x - 1, y))
        s.label(line, P(x - 4, y), 180)
        passive(ref_r, '2K2', x, y, rot=90, fields='below1')
        W((x + 1, y), (x + 9, y))
        s.label(line + '_ESP', P(x + 9, y), 0)
        d = s.add('Device:D_Schottky_Small', ref_d, 'BAT54WS', P(x + 4, y - 1), rot=270, props=dict(DIODE),
                  fields=fields_at((x + 5, y - 1.5), (x + 5, y - 0.5)))
        s.pin_power(d, 1, '+3V3')


# ------------------------------------------------------------ ORICP pull-ups

def i2c(ox, oy):
    block(ox, oy, ox + 38, oy + 12, 'ORICP I2C pull-ups')
    pull_up('R7', '4K7', ox + 6, oy + 9, 'SEL_A', length=4)
    pull_up('R8', '4K7', ox + 19, oy + 9, 'SEL_B', length=4)


# ------------------------------------------------------------ serial auto-program

def autoprog(ox, oy):
    block(ox, oy, ox + 31, oy + 20, 'Serial auto-program')
    for unit, (x, y, base, c, e, rb) in enumerate(((ox + 15, oy + 7, 'DTR', 'EN', 'RTS', 'R9'),
                                                   (ox + 15, oy + 15, 'RTS', 'BOOT', 'DTR', 'R10')), 1):
        q = s.add('Transistor_BJT:MMDT3904', 'Q2', 'MMDT3904', P(x, y), unit=unit, props=dict(Q_BJT),
                  fields=fields_at((x + 2, y - 0.5), (x + 2, y + 0.5)))
        b, col, em = (q.pin(p) for p in {1: (2, 6, 1), 2: (5, 3, 4)}[unit])
        W((b[0] / U, y), (x - 3, y))
        passive(rb, '10K', x - 4, y, rot=90, fields='below1')
        W((x - 5, y), (x - 8, y))
        s.label(base, P(x - 8, y), 180)
        W((col[0] / U, col[1] / U), (col[0] / U, y - 3), (x + 6, y - 3))
        s.label(c, P(x + 6, y - 3), 0)
        W((em[0] / U, em[1] / U), (em[0] / U, y + 3), (x + 6, y + 3))
        s.label(e, P(x + 6, y + 3), 0)


# ------------------------------------------------------------ user and programming header

def header(ox, oy):
    block(ox, oy, ox + 24, oy + 16, 'User and programming header')
    j = s.add('Connector:Conn_01x09_Pin', 'J2', 'User/serial', P(ox + 8, oy + 9), dnp=True,
              fields=fields_at((ox + 6.5, oy + 4.5), (ox + 6.5, oy + 13.5), just='right'),
              props={'Footprint': 'Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical'})
    s.pin_power(j, 1, 'GND', length=U)
    for pin, net in ((2, 'SWDIO'), (3, 'SWCLK'), (4, 'DTR'), (5, 'RTS'), (6, 'BOOT'), (7, 'RXD0'), (8, 'TXD0'),
                     (9, 'EN')):
        s.pin_label(j, pin, net)
    s.text('SEL_D', P(ox + 16, oy + 6))
    s.text('SEL_C', P(ox + 16, oy + 7))


# ------------------------------------------------------------ recovery button

def recovery(ox, oy):
    block(ox, oy, ox + 38, oy + 9, 'Recovery button: hold at power-up for no extensions')
    sw = s.add('Switch:SW_Push', 'SW1', 'Recovery', P(ox + 20, oy + 6), props=dict(BUTTON),
               fields={'Reference': (P(ox + 19.6, oy + 4)[0], P(ox + 19.6, oy + 4)[1], 'right', 'center', False),
                       'Value': (P(ox + 20.4, oy + 4)[0], P(ox + 20.4, oy + 4)[1], 'left', 'center', False)})
    W((ox + 18, oy + 6), (ox + 14, oy + 6))
    s.label('RECOVERY', P(ox + 14, oy + 6), 180)
    W((ox + 22, oy + 6), (ox + 24, oy + 6))
    s.power('GND', P(ox + 24, oy + 6))


module(5, 5)
ldo(5, 40)
recovery(5, 53)
i2c(5, 63)
onerom(45, 5)
drive(45, 21)
swd(45, 44)
header(45, 61.5)
boot(81, 5)
autoprog(81, 32)

if __name__ == '__main__':
    out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, 'build', VARIANT)
    os.makedirs(out, exist_ok=True)
    text = s.render()
    open(os.path.join(out, PROJECT + '.kicad_sch'), 'w').write(text)
    pro = os.path.join(out, PROJECT + '.kicad_pro')
    if not os.path.exists(pro):
        open(pro, 'w').write('{\n  "meta": {\n    "filename": "%s.kicad_pro",\n    "version": 3\n  }\n}\n' % PROJECT)
    issues = s.check()
    for i in issues:
        print('LAYOUT', i)
    sys.exit(1 if issues else 0)
