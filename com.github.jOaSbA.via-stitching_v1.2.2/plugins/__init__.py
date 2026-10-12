# Makes this directory an importable package, which is the only reason KiCad
# loads it at all once the Plugin and Content Manager has installed it.
#
# The PCM puts the archive's plugins/ contents in
# 3rdparty/plugins/<identifier with dots as underscores>/, and KiCad's loader
# (LoadPlugins in pcbnew.py) descends into a subdirectory of a plugin
# directory only when it holds an __init__.py -- otherwise it records
# "Skip subdir ..." and moves on, with no error and no toolbar button. Dropping
# the same files straight into scripting/plugins/ works without this, because
# there they are top-level modules rather than a subdirectory, which is how
# every hand-installed test of this plugin passed while a real PCM install
# would have loaded nothing.
#
# Importing the module is all that is needed: it registers the ActionPlugin at
# import time.
#
# License: GPL-3.0-or-later

from . import via_stitching_action_legacy  # noqa: F401
