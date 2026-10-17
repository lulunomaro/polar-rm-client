"""Ordered configuration quiescence without UI, devices or domain mutation.

Revocation and input release retain their original failure prefixes: failed
revocation does not proceed into publication. Pending-clear is independent and
must still be attempted. The caller owns recovery and must not commit domain
changes if any step fails.
"""
from __future__ import annotations

import logging
from typing import Callable

_LOG = logging.getLogger(__name__)


def quiesce_configuration_inputs(
    *, revoke: Callable[[], object], release: Callable[[], object],
    clear_pending: Callable[[], object],
) -> None:
    """Preserve the primary exception while making cleanup failure observable.

    A pending-clear failure alone propagates normally. If revocation/release
    already failed, retain that original exception (and traceback), attach the
    secondary error as a note, and log it. Diagnostic-sink failures cannot mask
    the original error. This handles ordinary Exceptions, not process exits.
    """
    try:
        revoke()
        release()
    except Exception as primary:
        try:
            clear_pending()
        except Exception as cleanup:
            try:
                primary.add_note(f"Pending input clear failed: {type(cleanup).__name__}: {cleanup}")
            except Exception:
                pass
            try:
                _LOG.error("Pending input clear failed after configuration quiescence failure",
                           exc_info=(type(cleanup), cleanup, cleanup.__traceback__))
            except Exception:
                pass
        raise
    clear_pending()
