"""Detect execution of the model script by b4dcad's preview or STL export."""

import sys

_RENDERING_MARKER = object()


def is_rendering():
    """Return whether the caller belongs to the model script being rendered."""
    return sys._getframe(1).f_globals.get("__b4dcad_rendering__") is _RENDERING_MARKER
