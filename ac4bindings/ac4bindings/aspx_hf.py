"""A-SPX HF generation and envelope adjustment (TS 103 190-1 5.7.6.4).

The high band is not transmitted: the HF generator copies low QMF subbands up
into the A-SPX range (Pseudocode 89), and the envelope adjuster scales them to
the transmitted envelopes, adds noise and inserts sinusoids (Pseudocodes
90-103).  This module is the reference transcription; the compiled QMF bank it
runs in is ``ac4bindings.qmf_analyse`` / ``qmf_synthesise``.

Pseudocode map:

  85   :func:`preflatten`     cubic fit of the low-band slope -> gain_vec
  86   :func:`covariance`     complex LPC covariance over the extended low band
  87   :func:`lpc`            alpha0 / alpha1 prediction coefficients
  88   :func:`chirp`          per-noise-group tonality adjustment, smoothed
  89   :func:`hf_generate`    the patch copy + LPC + pre-flattening
  90   :func:`estimate`       measured envelope of the patched band
  94   :func:`sine_noise_levels`
  95-101 :func:`adjust`       compensatory gain, limiter, boost, noise trim
  102-103 :func:`apply_noise` the noise generator tool

Pseudocodes 85-89 have no reference implementation to differ against (the
reference receiver renders the core and a plain patch copy only), so they are
gated by the parameter-free closed loop of Pseudocode 95: after adjustment the
measured band energy equals the transmitted ``scf_sig``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

#: Pseudocode 86/90: the HF-adjuster time-slot offset, and Table 191's QMF
#: timeslots per A-SPX timeslot for a 1536-sample frame.
TS_OFFSET_HFADJ = 4
NUM_TS_IN_ATS = 2

#: Pseudocode 95-100 constants.
EPSILON = 1.0
EPSILON0 = 1e-12
LIM_GAIN = 1.41254
MAX_SIG_GAIN = 1e5
MAX_BOOST_FACT = 1.584893192
#: Pseudocode 87.
EPSILON_INV = 2.0 ** -20
LPC_MAG_LIMIT = 4.0
#: Pseudocode 86: the covariance sums over the extended band with a step of
#: two, using taps delayed by 2 and 4 timeslots (Pseudocode 89).
COV_STEP = 2
LPC_TAP1 = 2
LPC_TAP2 = 4
LPC_ORDER = 3
#: Pseudocode 85.
POLY_ORDER = 3
DB_FACTOR = 10.0
GAIN_DB_DIV = 20.0
#: Pseudocode 92: the sinusoid sits at the middle of its envelope group.
SINE_MID = 0.5
#: Pseudocode 88.
CHIRP_SMOOTH_DOWN = (0.75, 0.25)
CHIRP_SMOOTH_UP = (0.90625, 0.09375)
CHIRP_MIN = 0.015625
#: Pseudocode 103.
NOISE_TABLE_LEN = 512
NOISE_IDX_OFFSET = 1

#: Table 194: tabNewChirp[aspx_tna_mode][aspx_tna_mode_prev].  Stored with the
#: CURRENT mode as the first index, exactly as Pseudocode 88 indexes it; the
#: spec's printed table is symmetric in the Light..Heavy block, so only the
#: first row differs, and that row is mode-current None.
TAB_NEW_CHIRP = (
    (0.0, 0.6, 0.9, 0.98),
    (0.6, 0.75, 0.9, 0.98),
    (0.0, 0.75, 0.9, 0.98),
    (0.0, 0.75, 0.9, 0.98),
)


@dataclass
class LowBand:
    """The HF generator's low-band input and its cross-frame state."""
    #: Q_low, ``(sba, nts)`` complex, the current interval.
    current: np.ndarray
    #: Q_low of the previous interval, or None on the first interval.
    previous: np.ndarray = None
    #: prev_chirp_array, per noise subband group.
    prev_chirp: np.ndarray = None
    #: aspx_tna_mode from the previous interval, per noise subband group.
    prev_tna: list = None
    #: The noise-table index carried across intervals.
    noise_index: int = 0


def preflatten(Q_low, sbx, ts_from, ts_to):
    """Pseudocode 85.  -> gain_vec over the first ``sbx`` subbands."""
    env = np.abs(Q_low[:sbx, ts_from:ts_to]) ** 2
    pow_env_db = DB_FACTOR * np.log10(env.mean(axis=1) + 1.0)
    mean_energy = pow_env_db.mean()
    x = np.arange(sbx)
    slope = np.polyval(np.polyfit(x, pow_env_db, POLY_ORDER), x)
    return 10.0 ** ((mean_energy - slope) / GAIN_DB_DIV)


def extend_low(low: LowBand, sba, nts, ts_offset_hfgen):
    """Pseudocode 86's Q_low_ext: previous-frame slots then this frame's."""
    n_ext = nts + ts_offset_hfgen + TS_OFFSET_HFADJ
    ext = np.zeros((sba, n_ext), complex)
    if low.previous is not None:
        ts_prev = nts - TS_OFFSET_HFADJ
        ext[:, :TS_OFFSET_HFADJ] = \
            low.previous[:sba, ts_prev:ts_prev + TS_OFFSET_HFADJ]
    ext[:, TS_OFFSET_HFADJ:TS_OFFSET_HFADJ + nts] = low.current[:sba, :nts]
    return ext


def covariance(low: LowBand, sba, nts, ts_offset_hfgen):
    """Pseudocode 86.  -> cov[sb, i, j] for i in 0..2, j in 1..2."""
    ext = extend_low(low, sba, nts, ts_offset_hfgen)
    n_ext = ext.shape[1]
    cov = np.zeros((sba, LPC_ORDER, LPC_ORDER), complex)
    for i in range(LPC_ORDER):
        for j in range(1, LPC_ORDER):
            acc = np.zeros(sba, complex)
            for ts in range(TS_OFFSET_HFADJ, n_ext, COV_STEP):
                acc += ext[:, ts - COV_STEP * i] * np.conj(ext[:, ts - COV_STEP * j])
            cov[:, i, j] = acc
    return cov


def lpc(cov):
    """Pseudocode 87.  -> (alpha0, alpha1), zeroed where |alpha| >= 4."""
    n = cov.shape[0]
    alpha0 = np.zeros(n, complex)
    alpha1 = np.zeros(n, complex)
    inv = 1.0 / (1.0 + EPSILON_INV)
    for sb in range(n):
        denom = cov[sb, 2, 2] * cov[sb, 1, 1] \
            - abs(cov[sb, 1, 2]) ** 2 * inv
        if denom == 0:
            a1 = 0.0
        else:
            a1 = (cov[sb, 0, 1] * cov[sb, 1, 2]
                  - cov[sb, 0, 2] * cov[sb, 1, 1]) / denom
        if cov[sb, 1, 1] == 0:
            a0 = 0.0
        else:
            a0 = (-cov[sb, 0, 1] + a1 * np.conj(cov[sb, 1, 2])) / cov[sb, 1, 1]
        alpha0[sb], alpha1[sb] = a0, a1
    big = (np.abs(alpha0) >= LPC_MAG_LIMIT) | (np.abs(alpha1) >= LPC_MAG_LIMIT)
    alpha0[big] = 0.0
    alpha1[big] = 0.0
    return alpha0, alpha1


def chirp(tna, prev_tna, prev_chirp):
    """Pseudocode 88 + Table 194, time-smoothed per noise subband group."""
    out = []
    for sbg in range(len(tna)):
        nc = TAB_NEW_CHIRP[tna[sbg]][prev_tna[sbg]]
        if nc < prev_chirp[sbg]:
            nc = CHIRP_SMOOTH_DOWN[0] * nc + CHIRP_SMOOTH_DOWN[1] * prev_chirp[sbg]
        else:
            nc = CHIRP_SMOOTH_UP[0] * nc + CHIRP_SMOOTH_UP[1] * prev_chirp[sbg]
        out.append(0.0 if nc < CHIRP_MIN else nc)
    return out


def noise_group_of(sb_high, t):
    """The noise subband group index containing A-SPX subband ``sb_high``."""
    g = 0
    while g + 1 < len(t.noise) and t.noise[g + 1] <= sb_high:
        g += 1
    return g


def hf_generate(low: LowBand, t, nts, ts_offset_hfgen, alpha0, alpha1,
                chirp_arr, gain_vec, preflat):
    """Pseudocode 89.  -> Q_high over the A-SPX range (n_sb, nts) complex."""
    ext = extend_low(low, t.sba, nts, ts_offset_hfgen)
    Q_high = np.zeros((t.num_sb_aspx, nts), complex)
    n = np.arange(nts) + TS_OFFSET_HFADJ
    sum_sb = 0
    for i in range(t.n_patches):
        for sb in range(t.patch_num_sb[i]):
            sb_high = t.sbx + sum_sb + sb
            p = t.patch_start_sb[i] + sb
            g = noise_group_of(sb_high, t)
            val = ext[p, n].copy()
            val += chirp_arr[g] * alpha0[p] * ext[p, n - LPC_TAP1]
            val += chirp_arr[g] ** 2 * alpha1[p] * ext[p, n - LPC_TAP2]
            if preflat:
                val = val / gain_vec[p]
            Q_high[sb_high - t.sbx, :] = val
        sum_sb += t.patch_num_sb[i]
    return Q_high


def estimate(Q_high, t, borders, nts_in_ats=NUM_TS_IN_ATS,
             ts_off=TS_OFFSET_HFADJ, slot=0):
    """Pseudocode 90, the aspx_interpolation == 1 branch.  -> (n_sb, n_env).

    ``Q_high`` is the full matrix; ``slot`` is the interval's first QMF
    timeslot, so the measured window is
    ``[slot + atsg*nts_in_ats + ts_off, ...)``.
    """
    n_sb, n_env = t.num_sb_aspx, len(borders) - 1
    est = np.zeros((n_sb, n_env))
    for e in range(n_env):
        ta = slot + int(borders[e]) * nts_in_ats + ts_off
        tz = slot + int(borders[e + 1]) * nts_in_ats + ts_off
        ta, tz = max(ta, 0), min(tz, Q_high.shape[1])
        if tz <= ta:
            continue
        est[:, e] = (np.abs(Q_high[:, ta:tz]) ** 2).mean(axis=1)
    return est


@dataclass
class SineState:
    """Pseudocode 92's cross-interval sinusoid state.

    ``prev_tsg_ptr``/``prev_num_env`` decide ``p_sine_at_end``; ``prev_last``
    is ``sine_idx_sb_prev[:, num_atsg_sig_prev - 1]``.
    """
    prev_tsg_ptr: int = None
    prev_num_env: int = 0
    prev_last: np.ndarray = None


def sine_idx_from_harmonics(ah, t, freq_res_arr, tsg_ptr, state: SineState):
    """Pseudocode 92/93.  -> (sine_idx_sb, p_sine_at_end).

    A sinusoid is placed in the MIDDLE subband of each high-resolution
    envelope group, but only once the envelope at or after ``aspx_tsg_ptr``
    (or when a sine was already present at the previous interval's end) --
    this is what keeps a sine continuous across an interval boundary.
    :func:`sine_area` then marks the whole group (Pseudocode 93).
    """
    n_sb, n_env = t.num_sb_aspx, len(freq_res_arr)
    if state.prev_tsg_ptr is not None and state.prev_tsg_ptr == state.prev_num_env:
        p_sine_at_end = 0
    else:
        p_sine_at_end = -1
    sine_idx = np.zeros((n_sb, n_env), dtype=int)
    for e in range(n_env):
        for sbg in range(t.n_hi):
            sba = t.hi[sbg] - t.sbx
            sbz = t.hi[sbg + 1] - t.sbx
            sb_mid = int(SINE_MID * (sbz + sba))
            if not (0 <= sb_mid < n_sb):
                continue
            carry = (state.prev_last is not None
                     and sb_mid < len(state.prev_last)
                     and state.prev_last[sb_mid])
            if e >= tsg_ptr or p_sine_at_end == 0 or carry:
                sine_idx[sb_mid, e] = ah[sbg]
    return sine_idx, p_sine_at_end


def sine_area(sine_idx, t, freq_res_arr):
    """Pseudocode 93.  -> 1 for every subband of an envelope group with a
    sinusoid in it, else 0."""
    n_sb, n_env = sine_idx.shape
    area = np.zeros_like(sine_idx, dtype=float)
    for e in range(n_env):
        tbl = t.hi if freq_res_arr[e] else t.lo
        for sbg in range(len(tbl) - 1):
            lo = max(tbl[sbg] - t.sbx, 0)
            hi = min(tbl[sbg + 1] - t.sbx, n_sb)
            if hi <= lo:
                continue
            if sine_idx[lo:hi, e].any():
                area[lo:hi, e] = 1.0
    return area


def sine_noise_levels(scf_sig, scf_noise, sine_idx):
    """Pseudocode 94.  -> (sine_lev, noise_lev)."""
    sig_noise_fact = scf_sig / (1.0 + scf_noise)
    return np.sqrt(sig_noise_fact * sine_idx), np.sqrt(sig_noise_fact * scf_noise)


def _limiter_gain(t, n_sb, n_env, scf_sig, est):
    """Pseudocode 96.  -> max_sig_gain_sb (n_sb, n_env).

    The per-group sum runs over ``[sbg_lim[sbg], sbg_lim[sbg+1] - 1)`` but the
    per-subband map assigns each group over ``[sbg_lim[sbg], sbg_lim[sbg+1])``,
    so the last subband of a group carries its group's ceiling; the spec's
    mapping loop is the authority here, not the reference receiver, whose
    simplified form leaves that subband unlimited.
    """
    max_sbg = np.empty((t.n_lim, n_env))
    for e in range(n_env):
        for sbg in range(t.n_lim):
            lo = max(t.lim[sbg] - t.sbx, 0)
            hi = min(t.lim[sbg + 1] - 1 - t.sbx, n_sb)
            nom = scf_sig[lo:hi, e].sum()
            den = EPSILON0 + est[lo:hi, e].sum()
            max_sbg[sbg, e] = np.sqrt(nom / den) * LIM_GAIN
    out = np.empty((n_sb, n_env))
    for e in range(n_env):
        sbg = 0
        for sb in range(n_sb):
            while sbg + 1 < t.n_lim and sb >= t.lim[sbg + 1] - t.sbx:
                sbg += 1
            out[sb, e] = min(max_sbg[sbg, e], MAX_SIG_GAIN)
    return out


def _boost(t, n_sb, n_env, scf_sig, est, sig_gain_lim, sine_lev, noise_lim,
           tsg_ptr, p_sine_at_end):
    """Pseudocodes 99-100.  -> boost_fact_sb (n_sb, n_env)."""
    boost_sbg = np.ones((t.n_lim, n_env))
    for e in range(n_env):
        for sbg in range(t.n_lim):
            lo = max(t.lim[sbg] - t.sbx, 0)
            hi = min(t.lim[sbg + 1] - 1 - t.sbx, n_sb)
            if hi <= lo:
                continue
            nom = EPSILON0 + scf_sig[lo:hi, e].sum()
            den = EPSILON0 + (est[lo:hi, e] * sig_gain_lim[lo:hi, e] ** 2).sum()
            den += (sine_lev[lo:hi, e] ** 2).sum()
            add_noise = ~((sine_lev[lo:hi, e] != 0)
                          | (e == tsg_ptr) | (e == p_sine_at_end))
            den += (noise_lim[lo:hi, e] ** 2 * add_noise).sum()
            boost_sbg[sbg, e] = np.sqrt(nom / den)
    out = np.empty((n_sb, n_env))
    for e in range(n_env):
        sbg = 0
        for sb in range(n_sb):
            while sbg + 1 < t.n_lim and sb >= t.lim[sbg + 1] - t.sbx:
                sbg += 1
            out[sb, e] = min(boost_sbg[sbg, e], MAX_BOOST_FACT)
    return out


def adjust(scf_sig, scf_noise, est, t, sine_idx, tsg_ptr, p_sine_at_end):
    """Pseudocodes 95-101.  -> (sig_gain_adj, noise_lev_adj, sine_lev_adj).

    The reference receiver's adjuster is a simplification of this (it drops
    the sine branch and the limiter's second mapping loop), so this stage is
    gated by the closed loop of Pseudocode 95 -- after applying the gain the
    measured band energy equals ``scf_sig`` -- and by unit tests, not by a
    differential run.
    """
    n_sb, n_env = est.shape
    sine_lev, noise_lev = sine_noise_levels(scf_sig, scf_noise, sine_idx)

    sig_gain = np.zeros((n_sb, n_env))
    for e in range(n_env):
        sine_at_end = (e == tsg_ptr or e == p_sine_at_end)
        for sb in range(n_sb):
            denom0 = EPSILON + est[sb, e]
            if sine_idx[sb, e] == 0:
                denom = (denom0 if sine_at_end
                         else denom0 * (1.0 + scf_noise[sb, e]))
                sig_gain[sb, e] = np.sqrt(scf_sig[sb, e] / denom)
            else:
                denom = denom0 * (1.0 + scf_noise[sb, e])
                sig_gain[sb, e] = np.sqrt(
                    scf_sig[sb, e] * scf_noise[sb, e] / denom)

    max_gain = _limiter_gain(t, n_sb, n_env, scf_sig, est)
    noise_lim = np.minimum(noise_lev,
                           noise_lev * max_gain / np.maximum(sig_gain, 1e-30))
    sig_gain_lim = np.minimum(sig_gain, max_gain)
    boost = _boost(t, n_sb, n_env, scf_sig, est, sig_gain_lim, sine_lev,
                   noise_lim, tsg_ptr, p_sine_at_end)

    return sig_gain_lim * boost, noise_lim * boost, sine_lev * boost


@dataclass
class HfStates:
    """The cross-interval state one channel's A-SPX chain carries."""
    prev_chirp: list = None
    prev_tna: list = None
    sine: SineState = field(default_factory=SineState)


@dataclass
class ChannelEnv:
    """One channel's dequantised envelopes for an interval, with the timing
    the adjuster needs (``borders``/``tsg_ptr``/``freq_res``)."""
    signal: np.ndarray
    noise: np.ndarray
    borders: tuple
    tsg_ptr: int
    freq_res: tuple


def process_interval(Q_high, t, group, low: LowBand, states: HfStates, cfg,
                     env: ChannelEnv, sine_values, slot, nts,
                     ts_offset_hfgen, nts_in_ats=NUM_TS_IN_ATS):
    """The HF generator + envelope adjuster for one interval, one channel.

    ``Q_high`` is the FULL ``(num_sb_aspx, n_total)`` patched matrix shared
    across frames; ``slot`` is this interval's first QMF timeslot.  The patch
    copy is written for the interval's columns, then Pseudocodes 90-103 apply
    the envelopes at global timeslots ``slot + atsg*nts_in_ats + ts_offset_hfadj``
    (Pseudocode 86/90).  Mutates ``Q_high``, ``low`` and ``states``; returns
    ``Q_high``.
    """
    cov = covariance(low, t.sba, nts, ts_offset_hfgen)
    alpha0, alpha1 = lpc(cov)
    prev_chirp = states.prev_chirp or [0.0] * t.n_noise
    prev_tna = states.prev_tna or [0] * t.n_noise
    chirp_arr = chirp(group.tna, prev_tna, prev_chirp)
    states.prev_chirp, states.prev_tna = chirp_arr, list(group.tna)
    ts_from = int(env.borders[0]) * nts_in_ats
    ts_to = int(env.borders[-1]) * nts_in_ats
    gain_vec = preflatten(low.current, t.sbx, ts_from, ts_to)

    patch = hf_generate(low, t, nts, ts_offset_hfgen, alpha0, alpha1,
                        chirp_arr, gain_vec, bool(cfg.preflat))
    Q_high[:, slot:slot + nts] = patch

    est = estimate(Q_high, t, env.borders, nts_in_ats, ts_off=TS_OFFSET_HFADJ,
                   slot=slot)
    sine_idx, p_sine_at_end = sine_idx_from_harmonics(
        group.ah, t, env.freq_res, env.tsg_ptr, states.sine)
    sig_gain, noise_lev, sine_lev = adjust(
        env.signal, env.noise, est, t, sine_idx, env.tsg_ptr, p_sine_at_end)

    n_env = est.shape[1]
    for e in range(n_env):
        ta = slot + int(env.borders[e]) * nts_in_ats + TS_OFFSET_HFADJ
        tz = slot + int(env.borders[e + 1]) * nts_in_ats + TS_OFFSET_HFADJ
        ta, tz = max(ta, 0), min(tz, Q_high.shape[1])
        if tz <= ta:
            continue
        Q_high[:, ta:tz] *= sig_gain[:, e:e + 1]
        Q_high[:, ta:tz] += sine_lev[:, e:e + 1] * sine_idx[:, e:e + 1]

    idx = low.noise_index
    for e in range(n_env):
        ta = slot + int(env.borders[e]) * nts_in_ats + TS_OFFSET_HFADJ
        tz = slot + int(env.borders[e + 1]) * nts_in_ats + TS_OFFSET_HFADJ
        ta, tz = max(ta, 0), min(tz, Q_high.shape[1])
        span = max(0, tz - ta)
        for ts in range(ta, tz):
            kk = (idx + t.num_sb_aspx * (ts - ta) + np.arange(t.num_sb_aspx)
                  + NOISE_IDX_OFFSET) % NOISE_TABLE_LEN
            Q_high[:, ts] += noise_lev[:, e] * sine_values[kk]
        idx = (idx + t.num_sb_aspx * span) % NOISE_TABLE_LEN
    low.noise_index = idx

    states.sine.prev_tsg_ptr = env.tsg_ptr
    states.sine.prev_num_env = n_env
    if sine_idx.shape[1]:
        states.sine.prev_last = sine_idx[:, -1].copy()
    return Q_high
