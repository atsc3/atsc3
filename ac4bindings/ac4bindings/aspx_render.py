"""End-to-end A-SPX render: core MDCT + QMF + HF generator/adjuster.

Follows the reference receiver's full-matrix flow (clause 5.7), extended from
its L/R-only render to every channel of the 5.X element:

  1. each core channel decodes to PCM (:mod:`ac4bindings.core`);
  2. the whole run per channel is QMF-analysed once, giving the full 64-row
     core matrix ``Q`` (:mod:`ac4bindings.aspx_qmf`, so the analysis filter
     state is exact);
  3. for each frame's interval the HF generator patches low rows into the
     A-SPX rows and the envelope adjuster scales them, adds noise and inserts
     sinusoids (:mod:`ac4bindings.aspx_hf`), at global timeslot
     ``i*nts + atsg*num_ts_in_ats + ts_offset_hfadj``;
  4. the adjusted rows are placed back and the matrix is QMF-synthesised to
     full-band PCM.

The A-SPX payload has five channel records in three groups: group 0 is the
L/R pair, group 1 the Ls/Rs pair, group 2 the centre.  The LFE carries no
A-SPX.  All cross-interval state lives in :class:`RenderState`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import aspx, aspx_hf, aspx_qmf, core, synthesise

#: The 5.X element's core channels, keyed as the C kernel reports them.
CHANNEL_NAMES = ("lfe", "L", "R", "Ls", "Rs", "C")


@dataclass(frozen=True)
class _Pair:
    """An A-SPX stereo pair: channel names, their stereo mode, and the two
    payload records (channel 0 = sum/level, channel 1 = balance)."""
    a: str
    b: str
    stereo_sap: int
    rec_a: int
    rec_b: int


def _pairs(el):
    """The A-SPX stereo pairs, with their decoded stereo modes.

    A 5.X element carries L/R and Ls/Rs; a ``channel_pair_element`` carries
    only L/R (TS 103 190-1 Table 22)."""
    lr = _Pair("L", "R", el.get("stereo_sap", 2), 0, 1)
    if el.get("is_pair"):
        return (lr,)
    return (lr, _Pair("Ls", "Rs", el.get("stereo_sap_sr", 2), 2, 3))


#: The mono centre's A-SPX record index.
MONO_REC = 4
FRAME_NTS = aspx_qmf.NUM_QMF_TIMESLOTS[core.LONG_LEN]
TS_HFGEN = aspx.TS_OFFSET_HFGEN[core.LONG_LEN]


@dataclass
class ChannelState:
    """Per-channel cross-interval state."""
    low: object = None
    hf: object = None
    core_overlap: object = None
    core_nprev: int = 0


@dataclass
class RenderState:
    """One element's entire render state."""
    channels: dict = field(default_factory=dict)
    cfg: object = None
    tables: object = None
    sine_values: np.ndarray = None
    signal_state: dict = field(default_factory=dict)
    noise_state: dict = field(default_factory=dict)

    def channel(self, name) -> ChannelState:
        if name not in self.channels:
            self.channels[name] = ChannelState()
        return self.channels[name]


def _pair_packed(el, a, b, stereo_sap):
    """The pair's packed spectra with the M/S decorrelation undone (Table 113).

    ``sap_mode`` 2 is full M/S; ``sap_mode`` 3 (alpha prediction) is passed
    through unfaked.
    """
    ca, cb = el["channels"][a], el["channels"][b]
    fr = core.framing_view(ca["framing"])
    pa = core.packed_spectrum(ca, fr)
    pb = core.packed_spectrum(cb, fr)
    if stereo_sap == core.SAP_MID_SIDE_ALL:
        return (pa + pb, pa - pb), fr
    return (pa, pb), fr


def core_pcm(el, st: "RenderState" = None):
    """The element's core PCM, one array per channel (no A-SPX).

    The MDCT overlap is carried in ``st`` across frames (TS 103 190-1 clause
    5.5.2, steps 5-6): it is the block-to-block state, and resetting it each
    frame leaves an audible seam and a large error.  With no ``st`` the state
    is fresh, as a single stand-alone frame.
    """
    chans = el["channels"]
    packed = {}
    for pair in _pairs(el):
        if pair.a not in chans or pair.b not in chans:
            continue
        (pa, pb), fr = _pair_packed(el, pair.a, pair.b, pair.stereo_sap)
        packed[pair.a] = (pa, fr)
        packed[pair.b] = (pb, fr)
    for name in ("lfe", "C"):
        if name in chans:
            packed[name] = (core.packed_spectrum(chans[name]),
                            core.framing_view(chans[name]["framing"]))
    pcm = {}
    for name, (spec, fr) in packed.items():
        wins, lengths = core.ungroup_float(spec, fr)
        cs = st.channel(name) if st is not None else ChannelState()
        if cs.core_overlap is None:
            cs.core_overlap = np.zeros(core.LONG_LEN, dtype=np.float64)
            cs.core_nprev = int(lengths[0]) if len(lengths) else core.LONG_LEN
        pcm[name], cs.core_nprev = synthesise(
            wins, np.asarray(lengths, np.int32), core.LONG_LEN,
            overlap=cs.core_overlap, n_prev=int(cs.core_nprev))
    return pcm


def _analyse_run(pcm, name):
    x = np.concatenate(pcm[name]) if pcm[name] else np.zeros(0)
    x = x[:(len(x) // aspx_qmf.NUM_QMF_SUBBANDS) * aspx_qmf.NUM_QMF_SUBBANDS]
    return aspx_qmf.analyse(x)[0]


def render(frames, st: RenderState = None, use_hf: bool = True):
    """Render a run of frames with A-SPX.

    ``frames`` is a list of ``(element_dict, groups)`` per frame, where
    ``groups`` is the five A-SPX channel records from ``aspx.parse_payload``
    (or None when A-SPX is unavailable).  Returns ``(pcm, Q, Q_high)``, each
    keyed by channel name.
    """
    st = st or RenderState()
    if st.sine_values is None:
        st.sine_values = aspx_qmf.noise_values()
    pcm = {name: [] for name in CHANNEL_NAMES}
    for el, groups in frames:
        cp = core_pcm(el, st)
        for name in CHANNEL_NAMES:
            if name in cp:
                pcm[name].append(cp[name])
    pcm = {k: v for k, v in pcm.items() if v}
    Q = {name: _analyse_run(pcm, name) for name in pcm}
    Qh = {name: Q[name].copy() for name in Q}
    if use_hf and st.tables is not None:
        _apply_hf(frames, Q, Qh, st)
    out = {name: aspx_qmf.synthesise(Qh[name])[0] for name in Qh}
    return out, Q, Qh


def _apply_hf(frames, Q, Qh, st: RenderState):
    """Pseudocodes 89-103 for every A-SPX channel across the run."""
    t = st.tables
    for i, (el, groups) in enumerate(frames):
        need = 2 if el.get("is_pair") else 5
        if groups is None or len(groups) < need:
            continue
        slot = i * FRAME_NTS
        for pair in _pairs(el):
            if pair.a not in Q:
                continue
            _hf_pair(Q, Qh, pair, groups[pair.rec_a], groups[pair.rec_b],
                     st, slot)
        if "C" in Q:
            _hf_mono(Q, Qh, "C", groups[MONO_REC], st, slot)


def _hf_pair(Q, Qh, pair: _Pair, ga, gb, st, slot):
    """Both channels of a pair, sharing the joint dequantisation."""
    t = st.tables
    qa, st.signal_state[pair.a] = aspx.reconstruct_signal(
        ga, t, aspx.sbg_maps(t), st.signal_state.get(pair.a, (None, 0)))
    qb, st.signal_state[pair.b] = aspx.reconstruct_signal(
        gb, t, aspx.sbg_maps(t), st.signal_state.get(pair.b, (None, 0)))
    na, st.noise_state[pair.a] = aspx.reconstruct_noise(
        ga, t, st.noise_state.get(pair.a))
    nb, st.noise_state[pair.b] = aspx.reconstruct_noise(
        gb, t, st.noise_state.get(pair.b))
    if ga.balance == 1:
        sig_a, sig_b = aspx.map_signal_joint(qa, qb, t, ga.framing.freq_res,
                                             t.num_sb_aspx, ga.qmode)
        noise_a, noise_b = aspx.map_noise_joint(na, nb, t, t.num_sb_aspx,
                                                sig_a.shape[1])
    else:
        sig_a = aspx.map_signal(qa, t, ga.framing.freq_res, t.num_sb_aspx,
                                ga.qmode)
        sig_b = aspx.map_signal(qb, t, gb.framing.freq_res, t.num_sb_aspx,
                                gb.qmode)
        noise_a = aspx.map_noise(na, t, t.num_sb_aspx, sig_a.shape[1])
        noise_b = aspx.map_noise(nb, t, t.num_sb_aspx, sig_b.shape[1])
    for name, g, sig, noise in ((pair.a, ga, sig_a, noise_a),
                                (pair.b, gb, sig_b, noise_b)):
        _hf_apply(Q, Qh, name, g, sig, noise, st, slot)


def _hf_mono(Q, Qh, name, g, st, slot):
    t = st.tables
    q, st.signal_state[name] = aspx.reconstruct_signal(
        g, t, aspx.sbg_maps(t), st.signal_state.get(name, (None, 0)))
    nq, st.noise_state[name] = aspx.reconstruct_noise(
        g, t, st.noise_state.get(name))
    sig = aspx.map_signal(q, t, g.framing.freq_res, t.num_sb_aspx, g.qmode)
    noise = aspx.map_noise(nq, t, t.num_sb_aspx, sig.shape[1])
    _hf_apply(Q, Qh, name, g, sig, noise, st, slot)


def _hf_apply(Q, Qh, name, g, sig, noise, st, slot):
    """HF generator + adjuster for one reconstructed channel."""
    t = st.tables
    ch = st.channel(name)
    low_cur = Q[name][:, slot:slot + FRAME_NTS]
    if ch.low is None:
        ch.low = aspx_hf.LowBand(current=low_cur)
    else:
        ch.low.previous = ch.low.current
        ch.low.current = low_cur
    if ch.hf is None:
        ch.hf = aspx_hf.HfStates()
    env = aspx_hf.ChannelEnv(signal=sig, noise=noise,
                             borders=g.framing.borders,
                             tsg_ptr=g.framing.tsg_ptr,
                             freq_res=g.framing.freq_res)
    band = Qh[name][t.sbx:t.sbx + t.num_sb_aspx]
    aspx_hf.process_interval(band, t, g, ch.low, ch.hf, st.cfg, env,
                             st.sine_values, slot, FRAME_NTS, TS_HFGEN)
    Qh[name][t.sbx:t.sbx + t.num_sb_aspx] = band
