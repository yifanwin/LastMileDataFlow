"""Compatibility shim: the four construction templates now live in `construction.cases`.

`validation.placement` still imports `requirements` from here; the case constructors are the single
implementation, so this module only forwards. Kept as a public name because other packages and tests
import it.
"""
from .cases import pending_hypotheses, requirements

__all__ = ['requirements', 'pending_hypotheses']
