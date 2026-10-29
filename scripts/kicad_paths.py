"""Locations of kicad-cli and KiCad's stock libraries."""
import os
import shutil
import sys

if sys.platform == 'darwin':
    SHARE = '/Applications/KiCad/KiCad.app/Contents/SharedSupport'
    DEFAULT_CLI = '/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli'
else:
    SHARE = '/usr/share/kicad'
    DEFAULT_CLI = '/usr/bin/kicad-cli'

KICAD_CLI = os.environ.get('KICAD_CLI') or shutil.which('kicad-cli') or DEFAULT_CLI
SYMBOL_DIR = os.environ.get('KICAD9_SYMBOL_DIR', SHARE + '/symbols')
FOOTPRINT_DIR = os.environ.get('KICAD9_FOOTPRINT_DIR', SHARE + '/footprints')
