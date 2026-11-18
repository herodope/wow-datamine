"""Local wowsims/forever sims: gear optimization, stat weights, item comparisons.

Modules:
  harness    runs jobs through bin/bisrun (the Go batch harness)
  simdb      the sim's item database: names, enchants by slot, sources
  rules      exclusion-rule profiles (config/rules/<name>.json)
  pool       per-spec candidate pool with exact item stat vectors, cached
  gear       a 17-slot gear set; .gear.siml in and out through the sim
  weights    finite-difference stat weights, EP
  objective  dps | tps | dtps | tmi | dtps_min_hp:<HP>
  optimize   seeds, screen, hill climb, enchant pass, final sim
  analysis   sim one set, compare items, close calls
  findings   saved results with staleness checks
  jobs       detached background jobs
  api        the above as compact text, for the MCP server and the CLI

See docs/wowsim.md.
"""

import sys
from pathlib import Path

# config.py lives in scripts/, beside this package.
_scripts = str(Path(__file__).resolve().parent.parent)
if _scripts not in sys.path:
    sys.path.insert(0, _scripts)
