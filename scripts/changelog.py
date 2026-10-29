"""Print a version's section of CHANGELOG.md."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the project folder, above scripts/

version = sys.argv[1]
lines = open(os.path.join(ROOT, 'CHANGELOG.md')).read().split('\n')
start = next((i for i, line in enumerate(lines) if re.match(r'## %s( |$)' % re.escape(version), line)), None)
if start is None:
    sys.exit('CHANGELOG.md has no section for %s' % version)
if not re.fullmatch(r'## %s - \d{4}-\d{2}-\d{2}' % re.escape(version), lines[start]):
    sys.exit('CHANGELOG.md heading for %s has no release date: %s' % (version, lines[start]))
end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('## ')), len(lines))
print('\n'.join(lines[start + 1:end]).strip())
