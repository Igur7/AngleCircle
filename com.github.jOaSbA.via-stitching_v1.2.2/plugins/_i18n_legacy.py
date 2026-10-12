# Loads the plugin's UI string catalog, matching KiCad's configured language.
#
# The catalogs themselves are shared with the IPC build rather than copied:
# the two backends say the same things, so a second set of translations would
# only drift. build.py packages plugins/locale/ alongside this file, and in a
# source checkout it is found one directory over.
#
# Same flat-JSON approach as the IPC build's _i18n.py, and for the same reason:
# a handful of languages and well under a hundred strings do not justify a
# .po/.mo compile step.
#
# License: GPL-3.0-or-later

import json
import os

from _kicad_config_legacy import ui_language

_LANGUAGE_TO_CODE = {
    "English": "en",
    "Dutch": "nl",
    "Nederlands": "nl",
    "German": "de",
    "Deutsch": "de",
    "French": "fr",
    "Français": "fr",
    "简体中文": "zh",
}

_HERE = os.path.dirname(os.path.abspath(__file__))
# Installed package first, then the repo layout.
_CATALOG_DIRS = (
    os.path.join(_HERE, "locale"),
    os.path.join(os.path.dirname(_HERE), "plugins", "locale"),
)
_catalog_cache = {}


def _catalog(code):
    if code not in _catalog_cache:
        _catalog_cache[code] = {}
        for directory in _CATALOG_DIRS:
            path = os.path.join(directory, "{}.json".format(code))
            try:
                with open(path, encoding="utf-8") as fh:
                    _catalog_cache[code] = json.load(fh)
                break
            except Exception:
                continue
    return _catalog_cache[code]


_active_catalog = _catalog(_LANGUAGE_TO_CODE.get(ui_language(), "en"))


def _(text):
    """Translate `text` if the active catalog has it, else return it as-is.

    English is never a catalog file: the source strings passed here are the
    English fallback.
    """
    return _active_catalog.get(text, text)
