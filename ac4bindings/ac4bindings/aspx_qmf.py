"""64-band complex QMF analysis/synthesis reference (TS 103 190-1 5.7.3/5.7.4).

``analyse`` is Pseudocode 65 and ``synthesise`` is Pseudocode 66, transcribed
literally from the standard.  This module is the reference the compiled
``_ac4.qmf_analyse`` / ``_ac4.qmf_synthesise`` kernels are differentially
tested against; it is also the perfect-reconstruction gate (a complex QMF bank
is oversampled by two, so analysis then synthesis returns the input delayed by
the bank's group delay).

The 640-tap prototype ``QWIN`` (Annex D.3) and the noise values are committed
in :mod:`ac4bindings.aspx_tables`.
"""

from __future__ import annotations

import numpy as np

from .aspx_tables import QWIN

NUM_QMF_SUBBANDS = 64
NUM_QMF_WIN_COEF = 640
#: Pseudocode 65 sums the windowed samples in this many 2*64 blocks; the
#: synthesis folds the same number back out (Pseudocode 66).
NUM_QMF_FOLDS = NUM_QMF_WIN_COEF // (2 * NUM_QMF_SUBBANDS)
#: Table 188.
NUM_QMF_TIMESLOTS = {2048: 32, 1920: 30, 1536: 24, 1024: 16, 960: 15,
                     768: 12, 512: 8, 384: 6}


def analysis_matrix() -> np.ndarray:
    """M[sb][n] = exp(j*pi/128*(sb+0.5)*(2n-1)), 64 x 128 (Pseudocode 65)."""
    sb = np.arange(NUM_QMF_SUBBANDS)[:, None]
    n = np.arange(2 * NUM_QMF_SUBBANDS)[None, :]
    return np.exp(1j * np.pi / (2 * NUM_QMF_SUBBANDS) * (sb + 0.5)
                  * (2 * n - 1))


def synthesis_matrix() -> np.ndarray:
    """N[n][sb] = 1/64 * exp(j*pi/128*(sb+0.5)*(2n-255)), 128 x 64.

    The constant is 4*num_qmf_subbands - 1 = 255, exactly as clause 5.7.4.2
    step 2 and Pseudocode 66 both print.  It is the value that reconstructs.
    """
    n = np.arange(2 * NUM_QMF_SUBBANDS)[:, None]
    sb = np.arange(NUM_QMF_SUBBANDS)[None, :]
    c = 4 * NUM_QMF_SUBBANDS - 1
    return np.exp(1j * np.pi / (2 * NUM_QMF_SUBBANDS) * (sb + 0.5)
                  * (2 * n - c)) / NUM_QMF_SUBBANDS


def analyse(pcm, qmf_filt=None) -> np.ndarray:
    """Pseudocode 65.  -> (64, num_timeslots) complex, and the filter state.

    Returns ``(Q, qmf_filt)``; pass the previous ``qmf_filt`` to carry the
    576-sample overlap between calls.
    """
    pcm = np.asarray(pcm, dtype=np.float64)
    nts = len(pcm) // NUM_QMF_SUBBANDS
    M = analysis_matrix()
    filt = (np.zeros(NUM_QMF_WIN_COEF) if qmf_filt is None
            else np.asarray(qmf_filt, dtype=np.float64).copy())
    out = np.empty((NUM_QMF_SUBBANDS, nts), complex)
    for ts in range(nts):
        filt[NUM_QMF_SUBBANDS:] = filt[:-NUM_QMF_SUBBANDS]
        filt[:NUM_QMF_SUBBANDS] = pcm[ts * NUM_QMF_SUBBANDS:
                                      (ts + 1) * NUM_QMF_SUBBANDS][::-1]
        z = filt * QWIN
        u = z[:2 * NUM_QMF_SUBBANDS].copy()
        for k in range(1, NUM_QMF_FOLDS):
            u += z[k * 2 * NUM_QMF_SUBBANDS:(k + 1) * 2 * NUM_QMF_SUBBANDS]
        out[:, ts] = M @ u
    return out, filt


def synthesise(Q, qsyn_filt=None):
    """Pseudocode 66.  -> real PCM, and the synthesis filter state."""
    Q = np.asarray(Q, dtype=complex)
    nts = Q.shape[1]
    N = synthesis_matrix()
    filt = (np.zeros(10 * 2 * NUM_QMF_SUBBANDS) if qsyn_filt is None
            else np.asarray(qsyn_filt, dtype=np.float64).copy())
    out = np.empty(nts * NUM_QMF_SUBBANDS)
    g = np.empty(NUM_QMF_WIN_COEF)
    for ts in range(nts):
        filt[2 * NUM_QMF_SUBBANDS:] = filt[:-2 * NUM_QMF_SUBBANDS]
        filt[:2 * NUM_QMF_SUBBANDS] = np.real(N @ Q[:, ts])
        for n in range(NUM_QMF_FOLDS):
            g[2 * NUM_QMF_SUBBANDS * n:
              2 * NUM_QMF_SUBBANDS * n + NUM_QMF_SUBBANDS] = \
                filt[4 * NUM_QMF_SUBBANDS * n:
                     4 * NUM_QMF_SUBBANDS * n + NUM_QMF_SUBBANDS]
            g[2 * NUM_QMF_SUBBANDS * n + NUM_QMF_SUBBANDS:
              2 * NUM_QMF_SUBBANDS * (n + 1)] = \
                filt[4 * NUM_QMF_SUBBANDS * n + 3 * NUM_QMF_SUBBANDS:
                     4 * NUM_QMF_SUBBANDS * n + 4 * NUM_QMF_SUBBANDS]
        w = g * QWIN
        out[ts * NUM_QMF_SUBBANDS:(ts + 1) * NUM_QMF_SUBBANDS] = \
            w.reshape(2 * NUM_QMF_FOLDS, NUM_QMF_SUBBANDS).sum(axis=0)
    return out, filt


def noise_values() -> np.ndarray:
    """Table D.2's 512 unit-magnitude complex noise values."""
    from . import aspx_tables
    return (np.asarray(aspx_tables.ASPX_NOISE_RE)
            + 1j * np.asarray(aspx_tables.ASPX_NOISE_IM))
