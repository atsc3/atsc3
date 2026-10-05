# Minimal Sphinx makefile for the OpenATSC3 documentation.
#
#   make html     build the HTML documentation into _build/html
#   make clean    remove the build directory
#
# The docs import atsc3lib for autodoc, so run this from the workspace
# environment (the top-level `make docs` handles that) rather than a bare
# interpreter.

SPHINXOPTS    ?=
SPHINXBUILD   ?= sphinx-build
SOURCEDIR     = .
BUILDDIR      = _build

.PHONY: help html clean

help:
	@$(SPHINXBUILD) -M help "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

html:
	@$(SPHINXBUILD) -M html "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)

clean:
	@$(SPHINXBUILD) -M clean "$(SOURCEDIR)" "$(BUILDDIR)" $(SPHINXOPTS) $(O)
