"""A-SPX control data (ETSI TS 103 190-1 clause 5.7.6.3).

This module builds and decodes everything the A-SPX high band is driven by:

* the frequency skeleton -- master subband groups (Pseudocode 67), their
  high/low-resolution signal tables (68/69), the noise table (70), the HF
  patch table (71) and the limiter table (72), all in :func:`sbg_tables`;
* the payload parse -- ``aspx_framing`` (Table 53), the delta directions
  (Table 54), the HF-generation flags (Tables 55/56) and the entropy-coded
  envelopes (Tables 57/58), in :func:`parse_payload`;
* envelope reconstruction and dequantisation (Pseudocodes 80-84).

The HF generator and envelope adjuster (Pseudocodes 85-103) are in
:mod:`ac4bindings.aspx_hf`; the QMF bank they run in is the compiled
``ac4bindings.qmf_analyse`` / ``qmf_synthesise``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from . import aspx_tables

#: QMF subbands span 24 kHz at 48 kHz (clause 5.7.3).
NUM_QMF_SUBBANDS = 64
QMF_HZ = 24000.0 / NUM_QMF_SUBBANDS

#: Table 191: QMF timeslots per A-SPX timeslot, and HF-generator time-slot
#: offset, both by frame length.
NUM_TS_IN_ATS = {2048: 2, 1920: 2, 1536: 2, 1024: 1, 960: 1, 768: 1,
                 512: 1, 384: 1}
TS_OFFSET_HFGEN = {2048: 6, 1920: 6, 1536: 6, 1024: 3, 960: 3, 768: 3,
                   512: 3, 384: 3}

#: Interval classes (Table 53/125).
FIXFIX, FIXVAR, VARFIX, VARVAR = 0, 1, 2, 3
INT_CLASS_NAMES = {FIXFIX: "FIXFIX", FIXVAR: "FIXVAR", VARFIX: "VARFIX",
                   VARVAR: "VARVAR"}

#: Table 53: width of the relative-border fields shrinks to 1 bit when
#: num_aspx_timeslots <= 8 (Note 1); the FIXFIX env count is read with
#: ``num_env_bits_fixfix + 1`` bits; ``aspx_tsg_ptr`` is stored minus one.
REL_BORD_BITS_WIDE = 2
REL_BORD_BITS_NARROW = 1
REL_BORD_STRIDE = 2
REL_BORD_BASE = 2
FIXFIX_ENVBITS_OFFSET = 1
TSG_PTR_OFFSET = 1
NATS_NARROW_MAX = 8

#: Pseudocode 77: the duration test for derived frequency resolution.
FREQ_RES_TS_DIV = 6.0
FREQ_RES_TS_OFF = 3.25

#: Pseudocode 67/5.7.6.3.1.1: the master template is indexed in steps of two
#: subbands from each end.
MASTER_STEP = 2

#: Pseudocode 70: noise-group span rounding and the shall count.
NOISE_SGB_ROUND = 0.5

#: Pseudocode 71 constants.
PATCH_SAMPLE_FREQ = 48
PATCH_GOAL_SB_48 = 43
PATCH_GOAL_SB_OTHER = 46
PATCH_SOURCE_LOW_HIRES = 4
PATCH_SOURCE_LOW_LORES = 2
PATCH_MIN_SPAN = 3
PATCH_LOOP_GUARD = 64

#: Pseudocode 72: two limiter groups per octave.
LIMITER_MIN_OCT = 0.245

#: Pseudocode 82/84: the dequantisation divisor per quantisation mode.
QSTEP_15DB = 2
QSTEP_30DB = 1
#: Pseudocode 83/84.
NOISE_FLOOR_OFFSET = 6
PAN_OFFSET = 12
#: Pseudocode 80/81: the balance channel uses a doubled step.
JOINT_CHANNEL = 1
JOINT_DELTA = 2

#: Pseudocode 78/84: a delta direction of 0 means FREQ, 1 means TIME.
DIR_FREQ = 0


@dataclass(frozen=True)
class AspxConfig:
    """The A-SPX header (Table 50), sent only in I-frames.

    The four fields the frequency skeleton needs (``start_freq``,
    ``stop_freq``, ``master_freq_scale``, ``noise_sbg``) have defaults so a
    skeleton can be built from a partial record; the payload decode fills
    every field from the C ``element`` kernel.
    """
    start_freq: int
    stop_freq: int
    master_freq_scale: int
    noise_sbg: int
    quant_mode_env: int = 1
    interpolation: int = 1
    preflat: int = 1
    limiter: int = 1
    num_env_bits_fixfix: int = 0
    freq_res_mode: int = 2

    @classmethod
    def from_mapping(cls, m) -> "AspxConfig":
        """Build from the dict the C ``element`` kernel returns."""
        return cls(quant_mode_env=m["quant_mode_env"],
                   start_freq=m["start_freq"], stop_freq=m["stop_freq"],
                   master_freq_scale=m["master_freq_scale"],
                   interpolation=m["interpolation"], preflat=m["preflat"],
                   limiter=m["limiter"], noise_sbg=m["noise_sbg"],
                   num_env_bits_fixfix=m["num_env_bits_fixfix"],
                   freq_res_mode=m["freq_res_mode"])

    def as_dict(self) -> dict:
        """The plain form the C kernel hands back (JSON-shaped)."""
        return {"quant_mode_env": self.quant_mode_env,
                "start_freq": self.start_freq, "stop_freq": self.stop_freq,
                "master_freq_scale": self.master_freq_scale,
                "interpolation": self.interpolation, "preflat": self.preflat,
                "limiter": self.limiter, "noise_sbg": self.noise_sbg,
                "num_env_bits_fixfix": self.num_env_bits_fixfix,
                "freq_res_mode": self.freq_res_mode}


@dataclass(frozen=True)
class AspxFraming:
    """One channel group's ``aspx_framing`` (Table 53, Pseudocode 76)."""
    int_class: int
    num_env: int
    num_noise: int
    freq_res: tuple
    borders: tuple
    tsg_ptr: int


@dataclass
class AspxGroup:
    """One A-SPX channel's decoded payload (Tables 51/52)."""
    framing: AspxFraming
    sig: list
    noise: list
    dir_sig: list
    dir_noise: list
    balance: int
    ch: int
    qmode: int
    ah: list
    tna: list


@dataclass(frozen=True)
class SbgTables:
    """Every derived A-SPX subband group table and count (clause 5.7.6.3.1).

    ``master``/``n_master``   sbg_master, Pseudocode 67.
    ``hi``/``n_hi``           sbg_sig_highres, Pseudocode 68.
    ``lo``/``n_lo``           sbg_sig_lowres, Pseudocode 69.
    ``noise``/``n_noise``     sbg_noise, Pseudocode 70.
    ``patch_num_sb``/``patch_start_sb``/``patches``/``n_patches``
                              Pseudocode 71.
    ``lim``/``n_lim``         sbg_lim, Pseudocode 72.
    ``sbx``/``num_sb_aspx``   A-SPX range start and width.
    ``sba``/``sbz``           master table first/last subband.
    """
    master: tuple
    n_master: int
    hi: tuple
    n_hi: int
    lo: tuple
    n_lo: int
    noise: tuple
    n_noise: int
    patch_num_sb: tuple
    patch_start_sb: tuple
    patches: tuple
    n_patches: int
    lim: tuple
    n_lim: int
    sbx: int
    num_sb_aspx: int
    sba: int
    sbz: int


def num_aspx_timeslots(frame_length: int = 1536,
                       qmf_bands: int = NUM_QMF_SUBBANDS) -> int:
    """Pseudocode 75a.  ``(frame_length / qmf_bands) / num_ts_in_ats``."""
    return (frame_length // qmf_bands) // NUM_TS_IN_ATS[frame_length]


def _master(cfg: AspxConfig) -> list:
    """Pseudocode 67.  The template length is ``num_sbg_master + 1``."""
    tpl = (aspx_tables.SBG_TEMPLATE_HIGHRES
           if cfg.master_freq_scale == 1
           else aspx_tables.SBG_TEMPLATE_LOWRES)
    n = (len(tpl) - 1) - MASTER_STEP * (cfg.start_freq + cfg.stop_freq)
    base = MASTER_STEP * cfg.start_freq
    return [tpl[base + i] for i in range(n + 1)]


def _patches(master, n_master, sba, sbx, span, cfg):
    """Pseudocode 71.  -> (num_sb, start_sb, borders, n_patches).

    Transcribed literally, including the ``odd`` parity term and the
    ``< PATCH_MIN_SPAN`` escape back to the end of the master table.
    """
    msb, usb = sba, sbx
    goal_sb = (PATCH_GOAL_SB_48 if PATCH_SAMPLE_FREQ == 48
               else PATCH_GOAL_SB_OTHER)
    source_band_low = (PATCH_SOURCE_LOW_HIRES if cfg.master_freq_scale == 1
                       else PATCH_SOURCE_LOW_LORES)

    if goal_sb < sbx + span:
        sbg = 0
        for i in range(len(master)):
            if master[i] >= goal_sb:
                break
            sbg = i + 1
    else:
        sbg = n_master

    num_sb, start_sb = [], []
    for _ in range(PATCH_LOOP_GUARD * n_master):
        j = sbg
        sb = master[j]
        odd = (sb - REL_BORD_STRIDE + sba) % 2
        while sb > (sba - source_band_low + msb - odd):
            j -= 1
            sb = master[j]
            odd = (sb - REL_BORD_STRIDE + sba) % 2
        n = max(sb - usb, 0)
        num_sb.append(n)
        start_sb.append(sba - odd - n)
        if n > 0:
            usb = msb = sb
        else:
            msb = sbx
            num_sb.pop()
            start_sb.pop()
        if master[sbg] - sb < PATCH_MIN_SPAN:
            sbg = n_master
        if sb == sbx + span:
            break
    else:
        raise RuntimeError("A-SPX patch loop did not terminate")

    if len(num_sb) > 1 and num_sb[-1] < PATCH_MIN_SPAN:
        num_sb.pop()
        start_sb.pop()

    borders = [sbx]
    for n in num_sb:
        borders.append(borders[-1] + n)
    return num_sb, start_sb, borders, len(num_sb)


def _limiter(lo, patches):
    """Pseudocode 72, main form: merge lowres and patch borders, drop any pair
    closer than :data:`LIMITER_MIN_OCT` octaves, preferring a patch border."""
    cand = sorted(set(lo) | set(patches))
    patch = set(patches)
    out = [cand[0]]
    for b in cand[1:]:
        if math.log2(b / out[-1]) < LIMITER_MIN_OCT:
            if b in patch and out[-1] not in patch:
                out[-1] = b
            continue
        out.append(b)
    return out, len(out) - 1


def sbg_tables(cfg: AspxConfig, xover: int) -> SbgTables:
    """Pseudocodes 67-72.  ``xover`` is ``aspx_xover_subband_offset``."""
    master = _master(cfg)
    n_master = len(master) - 1

    n_hi = n_master - xover
    hi = [master[s + xover] for s in range(n_hi + 1)]
    sbx = hi[0]
    num_sb_aspx = hi[n_hi] - sbx

    n_lo = n_hi - n_hi // 2
    lo = [hi[0]]
    for s in range(1, n_lo + 1):
        lo.append(hi[MASTER_STEP * s] if n_hi % 2 == 0
                  else hi[MASTER_STEP * s - 1])

    sba, sbz = master[0], master[n_master]
    n_noise = max(1, math.floor(cfg.noise_sbg * math.log2(sbz / sba)
                                + NOISE_SGB_ROUND))
    idx = [0]
    noise = [lo[0]]
    for s in range(1, n_noise + 1):
        idx.append(idx[s - 1] + (n_lo - idx[s - 1]) // (n_noise + 1 - s))
        noise.append(lo[idx[s]])

    num_sb, start_sb, patches, n_patches = _patches(
        master, n_master, sba, sbx, num_sb_aspx, cfg)
    lim, n_lim = _limiter(lo, patches)

    return SbgTables(
        master=tuple(master), n_master=n_master, hi=tuple(hi), n_hi=n_hi,
        lo=tuple(lo), n_lo=n_lo, noise=tuple(noise), n_noise=n_noise,
        patch_num_sb=tuple(num_sb), patch_start_sb=tuple(start_sb),
        patches=tuple(patches), n_patches=n_patches, lim=tuple(lim),
        n_lim=n_lim, sbx=sbx, num_sb_aspx=num_sb_aspx, sba=sba, sbz=sbz)


def sbg_maps(t: SbgTables):
    """Pseudocode 80's index maps between high- and low-resolution tables."""
    hi, lo = t.hi, t.lo
    high2low = [0] * t.n_hi
    low2high = [0] * (t.n_lo + 1)
    sbg_low = 0
    for sbg in range(t.n_hi):
        if sbg_low + 1 < len(lo) and lo[sbg_low + 1] == hi[sbg]:
            sbg_low += 1
            low2high[sbg_low] = sbg
        high2low[sbg] = sbg_low
    return high2low, low2high


# --------------------------------------------------------------------------
# A-SPX payload parse (clauses 4.2.12.3-4.2.12.8, Tables 51-58)
# --------------------------------------------------------------------------

#: Pseudocode 79's 18 codebooks, in the order :func:`build_codebook_trees`
#: returns them; :func:`_ec_sym` indexes the tree list through this.
ASPX_CB_NAMES = (
    "ASPX_HCB_ENV_LEVEL_15_F0", "ASPX_HCB_ENV_LEVEL_15_DF",
    "ASPX_HCB_ENV_LEVEL_15_DT", "ASPX_HCB_ENV_LEVEL_30_F0",
    "ASPX_HCB_ENV_LEVEL_30_DF", "ASPX_HCB_ENV_LEVEL_30_DT",
    "ASPX_HCB_ENV_BALANCE_15_F0", "ASPX_HCB_ENV_BALANCE_15_DF",
    "ASPX_HCB_ENV_BALANCE_15_DT", "ASPX_HCB_ENV_BALANCE_30_F0",
    "ASPX_HCB_ENV_BALANCE_30_DF", "ASPX_HCB_ENV_BALANCE_30_DT",
    "ASPX_HCB_NOISE_LEVEL_F0", "ASPX_HCB_NOISE_LEVEL_DF",
    "ASPX_HCB_NOISE_LEVEL_DT", "ASPX_HCB_NOISE_BALANCE_F0",
    "ASPX_HCB_NOISE_BALANCE_DF", "ASPX_HCB_NOISE_BALANCE_DT",
)
ASPX_CB_INDEX = {name: i for i, name in enumerate(ASPX_CB_NAMES)}


class _Bits:
    """MSB-first bit reader over a bytes buffer (TS 103 190-1 6.1)."""

    def __init__(self, data, pos=0):
        self.d = data
        self.p = pos

    def u(self, n):
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def vb(self, n_bits):
        value = 0
        while True:
            value += self.u(n_bits)
            if not self.u(1):
                return value
            value <<= n_bits
            value += 1 << n_bits


def int_class(b: _Bits) -> int:
    """Table 125.  0 / 10 / 110 / 111 (prefix code)."""
    if not b.u(1):
        return FIXFIX
    if not b.u(1):
        return FIXVAR
    return VARVAR if b.u(1) else VARFIX


def tab_border(nats: int, n: int):
    """Table 193: FIXFIX uniform borders, last one exact."""
    return [round(i * nats / n) for i in range(n)] + [nats]


def var_borders(ic, n, nats, b_iframe, var_l, var_r, rel_l, rel_r, state, key):
    """Pseudocode 76, the three variable interval classes."""
    a = [0] * (n + 1)
    if ic == FIXVAR:
        a[0] = 0
        a[n] = var_r + nats
        for t in range(len(rel_r)):
            a[n - t - 1] = a[n - t] - rel_r[t]
    else:
        a[0] = var_l if b_iframe else state.get(key, nats) - nats
        a[n] = nats if ic == VARFIX else var_r + nats
        for t in range(len(rel_l)):
            a[t + 1] = a[t] + rel_l[t]
        if ic == VARVAR:
            for t in range(len(rel_r)):
                a[n - t - 1] = a[n - t] - rel_r[t]
    return a


def freq_res(borders, atsg, tsg_ptr, nats, mode):
    """Pseudocode 77."""
    if mode == 1:
        return 0
    if mode == 3:
        return 1
    if atsg < tsg_ptr and nats > NATS_NARROW_MAX:
        return 1
    return 1 if (borders[atsg + 1] - borders[atsg]) \
        > (nats / FREQ_RES_TS_DIV + FREQ_RES_TS_OFF) else 0


def aspx_framing(b, cfg: AspxConfig, nats, b_iframe, state, key) -> AspxFraming:
    """Table 53 + Pseudocode 76.  -> framing for one channel group.

    Variable interval classes carry ``previous_stop_pos`` state per channel
    group, keyed ``key``.
    """
    ic = int_class(b)
    n_rel_bits = (REL_BORD_BITS_WIDE if nats > NATS_NARROW_MAX
                  else REL_BORD_BITS_NARROW)
    rel_l, rel_r = [], []
    var_l = var_r = 0

    if ic == FIXFIX:
        envbits = cfg.num_env_bits_fixfix + FIXFIX_ENVBITS_OFFSET
        num_env = 1 << b.u(envbits)
        tsg_ptr = 0
        borders = tab_border(nats, num_env)
    else:
        if ic == FIXVAR:
            var_r = b.u(REL_BORD_BITS_WIDE)
            for _ in range(b.u(n_rel_bits)):
                rel_r.append(REL_BORD_STRIDE * b.u(n_rel_bits) + REL_BORD_BASE)
        elif ic == VARFIX:
            if b_iframe:
                var_l = b.u(REL_BORD_BITS_WIDE)
            for _ in range(b.u(n_rel_bits)):
                rel_l.append(REL_BORD_STRIDE * b.u(n_rel_bits) + REL_BORD_BASE)
        else:
            if b_iframe:
                var_l = b.u(REL_BORD_BITS_WIDE)
            for _ in range(b.u(n_rel_bits)):
                rel_l.append(REL_BORD_STRIDE * b.u(n_rel_bits) + REL_BORD_BASE)
            var_r = b.u(REL_BORD_BITS_WIDE)
            for _ in range(b.u(n_rel_bits)):
                rel_r.append(REL_BORD_STRIDE * b.u(n_rel_bits) + REL_BORD_BASE)
        num_env = len(rel_l) + len(rel_r) + 1
        ptr_bits = math.ceil(math.log2(num_env + 2))
        tsg_ptr = b.u(ptr_bits) - TSG_PTR_OFFSET
        borders = var_borders(ic, num_env, nats, b_iframe, var_l, var_r,
                              rel_l, rel_r, state, key)

    if cfg.freq_res_mode == 0:
        fr = tuple(b.u(1) for _ in range(num_env))
    else:
        fr = tuple(freq_res(borders, e, tsg_ptr, nats, cfg.freq_res_mode)
                   for e in range(num_env))
    state[key] = borders[num_env]
    return AspxFraming(int_class=ic, num_env=num_env,
                       num_noise=2 if num_env > 1 else 1, freq_res=fr,
                       borders=tuple(borders), tsg_ptr=tsg_ptr)


def delta_dir(b, fr: AspxFraming) -> tuple:
    """Table 54."""
    return (tuple(b.u(1) for _ in range(fr.num_env)),
            tuple(b.u(1) for _ in range(fr.num_noise)))


def hfgen_iwc(b, t: SbgTables, nats, two_ch, balance=0):
    """Tables 55 and 56.  -> (aspx_add_harmonic, aspx_tna_mode) per channel."""
    n_noise, n_hi = t.n_noise, t.n_hi
    ah = [[0] * n_hi, [0] * n_hi]
    tna = [[0] * n_noise, [0] * n_noise]
    tna[0] = [b.u(2) for _ in range(n_noise)]
    if two_ch:
        if balance == 0:
            tna[1] = [b.u(2) for _ in range(n_noise)]
        else:
            tna[1] = list(tna[0])
        if b.u(1):                              # aspx_ah_left
            ah[0] = [b.u(1) for _ in range(n_hi)]
        if b.u(1):                              # aspx_ah_right
            ah[1] = [b.u(1) for _ in range(n_hi)]
        if b.u(1):                              # aspx_fic_present
            if b.u(1):                          # aspx_fic_left
                for _ in range(n_hi):
                    b.u(1)
            if b.u(1):                          # aspx_fic_right
                for _ in range(n_hi):
                    b.u(1)
        if b.u(1):                              # aspx_tic_present
            copy = b.u(1)
            left = right = 0
            if copy == 0:
                left, right = b.u(1), b.u(1)
            if copy or left:
                for _ in range(nats):
                    b.u(1)
            if right:
                for _ in range(nats):
                    b.u(1)
    else:
        if b.u(1):                              # aspx_ah_present
            ah[0] = [b.u(1) for _ in range(n_hi)]
        if b.u(1):                              # aspx_fic_present
            for _ in range(n_hi):
                b.u(1)
        if b.u(1):                              # aspx_tic_present
            for _ in range(nats):
                b.u(1)
    return ah, tna


def hcb_name(data_type, qmode, smode, htype) -> str:
    """Pseudocode 79: the codebook name for a data type and mode."""
    sm = "BALANCE" if smode else "LEVEL"
    if data_type == "SIGNAL":
        return f"ASPX_HCB_ENV_{sm}_{30 if qmode else 15}_{htype}"
    return f"ASPX_HCB_NOISE_{sm}_{htype}"


def delta_centre(name: str) -> int:
    """The delta-book zero point.

    Annex A gives the A-SPX books no ``cb_off``, so the centre comes from the
    code lengths: every DF/DT book has odd length with its shortest codeword at
    index ``(n-1)/2``, which is what a zero-centred delta alphabet looks like.
    The F0 books are absolute levels and are used raw.
    """
    if name.endswith("_F0"):
        return 0
    from . import huffman
    return (len(getattr(huffman, name + "_LEN")) - 1) // 2


def ec_data(b, trees, t: SbgTables, data_type, num_env, freq_res_arr, qmode,
            smode, dirs):
    """Tables 57 and 58.  -> list of per-envelope decoded value lists."""
    out = []
    for env in range(num_env):
        if data_type == "SIGNAL":
            n_sbg = t.n_hi if freq_res_arr[env] else t.n_lo
        else:
            n_sbg = t.n_noise
        vals = []
        if dirs[env] == DIR_FREQ:                 # F0 then DF
            vals.append(_ec_sym(b, trees, data_type, qmode, smode, "F0"))
            nm = hcb_name(data_type, qmode, smode, "DF")
            for _ in range(1, n_sbg):
                vals.append(_ec_sym(b, trees, data_type, qmode, smode, "DF")
                            - delta_centre(nm))
        else:                                     # all DT
            nm = hcb_name(data_type, qmode, smode, "DT")
            for _ in range(n_sbg):
                vals.append(_ec_sym(b, trees, data_type, qmode, smode, "DT")
                            - delta_centre(nm))
        out.append(vals)
    return out


def _ec_sym(b, trees, data_type, qmode, smode, htype) -> int:
    name = hcb_name(data_type, qmode, smode, htype)
    return _tree_decode(b, trees[ASPX_CB_INDEX[name]])


def _tree_decode(b, tree) -> int:
    left, right, sym, root = tree
    node = int(root)
    while int(sym[node]) < 0:
        node = int(right[node]) if b.u(1) else int(left[node])
    return int(sym[node])


def aspx_data(b, trees, t: SbgTables, cfg: AspxConfig, nats, b_iframe, two_ch,
              state, grp):
    """Tables 51 and 52.  -> list of per-channel group records."""
    if b_iframe:
        b.u(3)                                    # aspx_xover_subband_offset
    f0 = aspx_framing(b, cfg, nats, b_iframe, state, (grp, 0))
    q0 = _forced_qmode(cfg, f0)
    if not two_ch:
        d_sig, d_noise = delta_dir(b, f0)
        ah, tna = hfgen_iwc(b, t, nats, two_ch=False)
        sig = ec_data(b, trees, t, "SIGNAL", f0.num_env, f0.freq_res,
                      q0, 0, d_sig)
        noi = ec_data(b, trees, t, "NOISE", f0.num_noise, None, 0, 0, d_noise)
        return [AspxGroup(framing=f0, sig=sig, noise=noi, dir_sig=d_sig,
                          dir_noise=d_noise, balance=0, ch=0, qmode=q0,
                          ah=ah[0], tna=tna[0])]
    balance = b.u(1)
    f1, q1 = f0, q0
    if balance == 0:
        f1 = aspx_framing(b, cfg, nats, b_iframe, state, (grp, 1))
        q1 = _forced_qmode(cfg, f1)
    d0_sig, d0_noise = delta_dir(b, f0)
    d1_sig, d1_noise = delta_dir(b, f1)
    ah, tna = hfgen_iwc(b, t, nats, two_ch=True, balance=balance)
    sm1 = 1 if balance else 0
    s0 = ec_data(b, trees, t, "SIGNAL", f0.num_env, f0.freq_res, q0, 0, d0_sig)
    s1 = ec_data(b, trees, t, "SIGNAL", f1.num_env, f1.freq_res, q1, sm1,
                 d1_sig)
    n0 = ec_data(b, trees, t, "NOISE", f0.num_noise, None, 0, 0, d0_noise)
    n1 = ec_data(b, trees, t, "NOISE", f1.num_noise, None, 0, sm1, d1_noise)
    return [AspxGroup(framing=f0, sig=s0, noise=n0, dir_sig=d0_sig,
                      dir_noise=d0_noise, balance=balance, ch=0, qmode=q0,
                      ah=ah[0], tna=tna[0]),
            AspxGroup(framing=f1, sig=s1, noise=n1, dir_sig=d1_sig,
                      dir_noise=d1_noise, balance=balance, ch=1, qmode=q1,
                      ah=ah[1], tna=tna[1])]


def _forced_qmode(cfg: AspxConfig, fr: AspxFraming) -> int:
    """Table 51/52: a FIXFIX interval with one envelope forces qmode 0."""
    if fr.int_class == FIXFIX and fr.num_env == 1:
        return 0
    return cfg.quant_mode_env


def parse_payload(sub, offset, t: SbgTables, cfg: AspxConfig, trees, nats,
                  b_iframe, state):
    """Decode the three A-SPX channel groups that follow the six channels.

    ``state`` carries per-group ``previous_stop_pos`` for the variable interval
    classes; it is mutated.  Returns ``(groups, bitpos)``.
    """
    b = _Bits(sub, offset)
    groups = []
    groups += aspx_data(b, trees, t, cfg, nats, b_iframe, True, state, 0)
    groups += aspx_data(b, trees, t, cfg, nats, b_iframe, True, state, 1)
    groups += aspx_data(b, trees, t, cfg, nats, b_iframe, False, state, 2)
    return groups, b.p


def parse_payload_pair(sub, offset, t: SbgTables, cfg: AspxConfig, trees, nats,
                       b_iframe, state):
    """Decode the single A-SPX 2-channel group of a ``channel_pair_element``
    (TS 103 190-1 Table 22).  Returns ``(groups, bitpos)`` with two records,
    L and R."""
    b = _Bits(sub, offset)
    groups = aspx_data(b, trees, t, cfg, nats, b_iframe, True, state, 0)
    return groups, b.p


def build_codebook_trees():
    """-> ``[tree, ...]`` in :data:`ASPX_CB_NAMES` order."""
    from . import huffman
    from . import build_huff_tree
    trees = []
    for name in ASPX_CB_NAMES:
        lens = getattr(huffman, name + "_LEN")
        words = getattr(huffman, name + "_CW")
        trees.append(build_huff_tree(lens, words))
    return trees


# --------------------------------------------------------------------------
# Envelope reconstruction (Pseudocodes 80-84)
# --------------------------------------------------------------------------

def reconstruct_signal(dat: AspxGroup, t: SbgTables, maps, prev):
    """Pseudocode 80.  -> qscf[atsg][sbg], and the new state."""
    high2low, low2high = maps
    fr = dat.framing
    delta = (JOINT_DELTA if dat.ch == JOINT_CHANNEL and dat.balance == 1
             else 1)
    out = []
    prev_q, prev_res = prev
    for atsg, vals in enumerate(dat.sig):
        res = fr.freq_res[atsg]
        n_sbg = t.n_hi if res else t.n_lo
        q = [0] * n_sbg
        if dat.dir_sig[atsg] == DIR_FREQ:
            acc = 0
            for sbg in range(min(n_sbg, len(vals))):
                acc += delta * vals[sbg]
                q[sbg] = acc
        else:
            for sbg in range(min(n_sbg, len(vals))):
                if prev_q is None:
                    base = 0
                elif res == prev_res:
                    base = prev_q[sbg] if sbg < len(prev_q) else 0
                elif res == 0 and prev_res == 1:
                    i = low2high[sbg] if sbg < len(low2high) else 0
                    base = prev_q[i] if i < len(prev_q) else 0
                else:
                    i = high2low[sbg] if sbg < len(high2low) else 0
                    base = prev_q[i] if i < len(prev_q) else 0
                q[sbg] = base + delta * vals[sbg]
        out.append(q)
        prev_q, prev_res = q, res
    return out, (prev_q, prev_res)


def reconstruct_noise(dat: AspxGroup, t: SbgTables, prev):
    """Pseudocode 81.  -> qscf_noise[atsg][sbg], and the new state."""
    delta = (JOINT_DELTA if dat.ch == JOINT_CHANNEL and dat.balance == 1
             else 1)
    dirs = dat.dir_noise
    out, prev_q = [], prev
    for e, vals in enumerate(dat.noise):
        n = t.n_noise
        q = [0] * n
        if dirs[e] == DIR_FREQ:
            acc = 0
            for sbg in range(min(n, len(vals))):
                acc += delta * vals[sbg]
                q[sbg] = acc
        else:
            for sbg in range(min(n, len(vals))):
                base = prev_q[sbg] if (prev_q and sbg < len(prev_q)) else 0
                q[sbg] = base + delta * vals[sbg]
        out.append(q)
        prev_q = q
    return out, prev_q


def _qstep(qmode) -> int:
    """Pseudocode 82/84: the dequantisation divisor for a quantisation mode."""
    return QSTEP_15DB if qmode == 0 else QSTEP_30DB


def map_signal(qscf_env, t: SbgTables, freq_res_arr, n_sb, qmode):
    """Pseudocode 82/91: dequantise a signal envelope onto QMF subbands.

    Returns ``(n_sb, n_env)``."""
    import numpy as np
    a = _qstep(qmode)
    n_env = len(qscf_env)
    out = np.zeros((n_sb, n_env))
    for e, q in enumerate(qscf_env):
        tbl = t.hi if freq_res_arr[e] else t.lo
        for sbg, val in enumerate(q):
            if sbg + 1 >= len(tbl):
                break
            lo = max(tbl[sbg] - t.sbx, 0)
            hi = min(tbl[sbg + 1] - t.sbx, n_sb)
            out[lo:hi, e] = NUM_QMF_SUBBANDS * (2.0 ** (val / a))
    return out


def map_noise(qscf_env, t: SbgTables, n_sb, n_env=None):
    """Pseudocode 83 + 91: noise indices -> per-subband noise levels.

    The noise envelope grid is coarser than the signal grid, so Pseudocode 91
    repeats the latest noise envelope across the signal envelopes it spans;
    ``n_env`` is that signal-envelope count (defaults to the noise count).
    """
    import numpy as np
    if n_env is None:
        n_env = len(qscf_env)
    out = np.zeros((n_sb, n_env))
    for e in range(n_env):
        q = qscf_env[min(e, len(qscf_env) - 1)]
        for sbg, val in enumerate(q):
            if sbg + 1 >= len(t.noise):
                break
            lo = max(t.noise[sbg] - t.sbx, 0)
            hi = min(t.noise[sbg + 1] - t.sbx, n_sb)
            out[lo:hi, e] = 2.0 ** (NOISE_FLOOR_OFFSET - val)
    return out


def map_signal_joint(qa, qb, t: SbgTables, freq_res_arr, n_sb, qmode):
    """Pseudocode 84 (signal half): joint sum/balance dequantisation."""
    import numpy as np
    a = _qstep(qmode)
    n_env = min(len(qa), len(qb))
    A = np.zeros((n_sb, n_env))
    B = np.zeros((n_sb, n_env))
    for e in range(n_env):
        tbl = t.hi if freq_res_arr[e] else t.lo
        for sbg in range(min(len(qa[e]), len(qb[e]))):
            if sbg + 1 >= len(tbl):
                break
            lo = max(tbl[sbg] - t.sbx, 0)
            hi = min(tbl[sbg + 1] - t.sbx, n_sb)
            if hi <= lo:
                continue
            va, vb = qa[e][sbg], qb[e][sbg]
            nom = (2.0 ** (va / a + 1)) * NUM_QMF_SUBBANDS
            A[lo:hi, e] = nom / (1.0 + 2.0 ** (PAN_OFFSET - vb / a))
            B[lo:hi, e] = nom / (1.0 + 2.0 ** (vb / a - PAN_OFFSET))
    return A, B


def map_noise_joint(qa, qb, t: SbgTables, n_sb, n_env):
    """Pseudocode 84 (noise half): joint sum/balance dequantisation."""
    import numpy as np
    A = np.zeros((n_sb, n_env))
    B = np.zeros((n_sb, n_env))
    for e in range(n_env):
        ia = min(e, len(qa) - 1)
        ib = min(e, len(qb) - 1)
        for sbg in range(min(t.n_noise, len(qa[ia]), len(qb[ib]))):
            lo = max(t.noise[sbg] - t.sbx, 0)
            hi = min(t.noise[sbg + 1] - t.sbx, n_sb)
            if hi <= lo:
                continue
            va, vb = qa[ia][sbg], qb[ib][sbg]
            nom = 2.0 ** (NOISE_FLOOR_OFFSET - va + 1)
            A[lo:hi, e] = nom / (1.0 + 2.0 ** (PAN_OFFSET - vb))
            B[lo:hi, e] = nom / (1.0 + 2.0 ** (vb - PAN_OFFSET))
    return A, B
