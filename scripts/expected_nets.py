"""Netlist written from the design, independent of the drawing code. Each function takes the variant, mini or pro.

Nets in named() must match by name and members. Nets in unnamed() match by members only.
Two-pin unpolarised R/C pins may be swapped.
"""

# module pin of each ESP32 signal
PINS = {
    # Mini, ESP32-C3: RUN_DRV IO7, SWCLK IO6, SWDIO IO5, BOOTSEL_DRV IO4, RECOVERY IO10, SEL_B IO1, SEL_A IO0, LED IO2, RGB IO8,
    # J1_7 IO3, J1_9 IO18
    'mini': {'3V3': 3, 'EN': 8, 'BOOT': 23, 'TXD0': 31, 'RXD0': 30, 'RUN_DRV': 21, 'BOOTSEL_DRV': 18,
           'SWCLK_ESP': 20, 'SWDIO_ESP': 19, 'SEL_A': 12, 'SEL_B': 13, 'RECOVERY': 16, 'LED': 5, 'RGB': 22,
           'J1_7': 6, 'J1_9': 26},
    # Pro, ESP32-S3: RUN_DRV IO21, BOOTSEL_DRV IO16, SWCLK IO15, SWDIO IO17, SEL_A IO13, SEL_B IO14, RECOVERY IO12, LED IO11,
    # RGB IO48, J1_7 IO47, J1_9 IO18
    'pro': {'3V3': 3, 'EN': 45, 'BOOT': 4, 'TXD0': 39, 'RXD0': 40, 'RUN_DRV': 25, 'BOOTSEL_DRV': 20,
           'SWCLK_ESP': 19, 'SWDIO_ESP': 21, 'SEL_A': 17, 'SEL_B': 18, 'RECOVERY': 16, 'LED': 15, 'RGB': 30,
           'J1_7': 27, 'J1_9': 22},
}

MODULE_GND = {
    'mini': [1, 2, 11, 14] + list(range(36, 54)),
    'pro': [1, 2, 42, 43] + list(range(46, 66)),
}


def named(v):
    n = {}
    u = {k: 'U1.%d' % p for k, p in PINS[v].items()}

    def add(name, *nodes):
        n.setdefault(name, set()).update(nodes)

    add('GND', *['U1.%d' % p for p in MODULE_GND[v]], 'U2.2', 'J1.1', 'J2.1', 'SW1.2',
        'Q1.1', 'Q1.4', 'R3.2', 'R4.2', 'C1.2', 'C2.2', 'C3.2', 'C4.2', 'R13.2', 'D4.3', 'C6.2')
    add('+5V', 'J1.2', 'U2.1', 'U2.3', 'C2.1', 'D4.2', 'C6.1')
    add('+3V3', 'U2.5', u['3V3'], 'C3.1', 'C4.1', 'R1.1', 'R2.1', 'R7.1', 'R8.1', 'D1.1', 'D2.1', 'R13.1', 'D3.2')

    # reset, boot mode and the serial adapter's auto-program transistors
    add('EN', u['EN'], 'R1.2', 'C1.1', 'Q2.6', 'J2.9')
    add('BOOT', u['BOOT'], 'R2.2', 'Q2.3', 'J2.6')
    add('DTR', 'R9.1', 'Q2.4', 'J2.4')
    add('RTS', 'R10.1', 'Q2.1', 'J2.5')
    add('TXD0', u['TXD0'], 'J2.8')
    add('RXD0', u['RXD0'], 'J2.7')

    # J1 is on the underside: odd pins on row 2, even pins on row 1, pins 2n-1 and 2n in column n
    # RUN and BOOTSEL pulled low by MOSFETs, off unless the ESP32 drives the gate high
    add('RUN', 'J1.4', 'Q1.6')
    add('BOOTSEL', 'J1.6', 'Q1.3')
    add('RUN_DRV', u['RUN_DRV'], 'Q1.2', 'R3.1')
    add('BOOTSEL_DRV', u['BOOTSEL_DRV'], 'Q1.5', 'R4.1')

    # SWD, also SEL_C and SEL_D on the user header, through 2.2k with a clamp to 3V3 on the ESP32 side
    add('SWCLK', 'J1.5', 'J2.3', 'R5.1')
    add('SWDIO', 'J1.3', 'J2.2', 'R6.1')
    add('SWCLK_ESP', u['SWCLK_ESP'], 'R5.2', 'D1.2')
    add('SWDIO_ESP', u['SWDIO_ESP'], 'R6.2', 'D2.2')

    # ORICP I2C
    add('SEL_A', 'J1.10', u['SEL_A'], 'R7.2')
    add('SEL_B', 'J1.8', u['SEL_B'], 'R8.2')

    add('RECOVERY', u['RECOVERY'], 'SW1.1')

    # J1-7 and J1-9 through 1K to the ESP32
    add('J1_7', u['J1_7'], 'R17.1')
    add('J1_9', u['J1_9'], 'R18.1')

    # blue status LED from 3V3, on when LED is low, and the RGB LED
    add('LED', u['LED'], 'R14.2')
    add('RGB', u['RGB'], 'R15.1')

    if v == 'mini':
        add('+3V3', 'R11.1', 'R12.1', 'C5.1')
        add('GND', 'C5.2')
        add('LED', 'R11.2')
        add('RGB', 'R12.2')
    return n


def unnamed(v):
    return [{'R9.2', 'Q2.2'}, {'R10.2', 'Q2.5'},   # transistor bases
            {'J1.7', 'R17.2'}, {'J1.9', 'R18.2'}, {'D3.1', 'R14.1'}, {'R15.2', 'D4.4'},
            {'D4.1', 'TP1.1'}]


def no_connect(v):
    """Pins deliberately left unconnected."""
    if v == 'mini':
        pins = [4, 7, 9, 10, 15, 17, 24, 25, 28, 29, 32, 33, 34, 35, 27]
    else:
        pins = list(range(5, 15)) + [23, 24, 26, 28, 29] + list(range(31, 39)) + [41, 44]
    return ['U1.%d' % p for p in pins] + ['U2.4']
