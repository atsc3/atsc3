"""Sphinx configuration for the ATSC 3.0 receiver documentation.

The docs live at the repository root (``docs/``) and describe the whole
workspace.  ``atsc3lib/`` is added to ``sys.path`` so ``automodule`` can
import the package and pull docstrings directly from the source; this needs
the package importable (i.e. its compiled ``atsc3lib._bindings`` extensions
built), so build the docs from the workspace environment (``make docs``).
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "atsc3lib"))

project = "OpenATSC3"
copyright = "OpenATSC3"
author = "OpenATSC3"

try:
    from atsc3lib import __version__ as release
except Exception:
    release = "0.1.0"
version = release

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinx.ext.autosectionlabel",
]

autodoc_member_order = "bysource"
autodoc_typehints = "description"
autosectionlabel_prefix_document = True

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]
html_title = "OpenATSC3"
html_show_sourcelink = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
    "numpy": ("https://numpy.org/doc/stable", None),
}
