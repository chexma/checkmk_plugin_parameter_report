"""Loader of the parameter report web page.

Checkmk 2.5 has no stable API for GUI pages; this file sits in the local GUI
plugin directory (reported as deprecated in "Analyze configuration"). Any
error is logged and swallowed: a failing plugin import would otherwise stop
the whole GUI from starting. The page itself lives in
cmk_addons.plugins.parameter_report.gui.pages.
"""

import logging

try:
    from cmk_addons.plugins.parameter_report.gui import pages as _pages

    _pages.register()
except Exception:
    logging.getLogger("cmk.web").exception("parameter_report: web page not loaded")
