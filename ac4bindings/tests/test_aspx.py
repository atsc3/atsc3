"""Tests for the A-SPX control data (ETSI TS 103 190-1 clause 5.7.6.3).

The A-SPX frequency skeleton (Pseudocodes 67-72) is pure arithmetic on the
aspx_config header plus the 3-bit xover offset, so it is gated two ways:

* structural properties the spec states in its own right (templates strictly
  increasing and nested, num_sbg_noise <= 5, patches tile the range and source
  from below the crossover, master starts and ends on an even subband);
* a differential run over committed configurations against the independent
  reference receiver.
"""

import gzip
import json
import os
import struct
from pathlib import Path

import numpy as np
import pytest

from ac4bindings import aspx
from ac4bindings import aspx_tables

DATA = Path(__file__).parent / "data"
REF = gzip.decompress(
    (DATA / "ac4_aspx_sbg_ref.json.gz").read_bytes()).decode()
PAYLOAD_REF = json.loads(gzip.decompress(
    (DATA / "ac4_aspx_payload_ref.json.gz").read_bytes()).decode())
ATSC3_DATA = Path(os.environ.get("AC4_ATSC3_DATA", DATA))


def _frames(name):
    # The on-air AC-4 element fixtures live in the sibling atsc3lib repo; a
    # standalone checkout (CI) does not carry them, so skip rather than fail.
    if not (ATSC3_DATA / name).exists():
        pytest.skip(f"{name} not present (sibling atsc3lib checkout)")
    d = (ATSC3_DATA / name).read_bytes()
    out, o = [], 0
    while o + 4 <= len(d):
        n = struct.unpack("<I", d[o:o + 4])[0]
        o += 4
        out.append(d[o:o + n])
        o += n
    return out


class TestTemplates:
    def test_shape(self):
        hi = aspx_tables.SBG_TEMPLATE_HIGHRES
        lo = aspx_tables.SBG_TEMPLATE_LOWRES
        assert (hi[0], hi[-1]) == (18, 62)
        assert (lo[0], lo[-1]) == (10, 46)
        assert all(hi[i] < hi[i + 1] for i in range(len(hi) - 1))
        assert all(lo[i] < lo[i + 1] for i in range(len(lo) - 1))

    def test_qmf_hz(self):
        assert aspx.QMF_HZ == 375.0


class TestSkeleton:
    def test_structural(self):
        cfg = aspx.AspxConfig(start_freq=5, stop_freq=1,
                              master_freq_scale=1, noise_sbg=3)
        t = aspx.sbg_tables(cfg, 0)
        assert t.n_noise <= 5
        assert t.master[0] % 2 == 0 and t.master[-1] % 2 == 0
        assert t.patches[0] == t.sbx
        assert t.patches[-1] == t.sbx + t.num_sb_aspx
        for st, n in zip(t.patch_start_sb, t.patch_num_sb):
            assert st >= 0 and st + n <= t.sbx
        assert t.n_patches <= 5

    def test_timeslots(self):
        assert aspx.num_aspx_timeslots(1536) == 12

    def test_differential(self):
        refs = json.loads(REF)
        for r in refs:
            t = aspx.sbg_tables(aspx.AspxConfig(**r["cfg"]), r["xover"])
            assert list(t.master) == r["master"], (r["cfg"], r["xover"])
            assert list(t.hi) == r["hi"]
            assert list(t.lo) == r["lo"]
            assert list(t.noise) == r["noise"]
            assert list(t.patches) == r["patches"]
            assert list(t.patch_num_sb) == r["patch_num_sb"]
            assert list(t.patch_start_sb) == r["patch_start_sb"]
            assert list(t.lim) == r["lim"]
            assert t.sbx == r["sbx"]
            assert t.num_sb_aspx == r["num_sb_aspx"]
            assert (t.n_hi, t.n_lo, t.n_noise, t.n_patches) == (
                r["n_hi"], r["n_lo"], r["n_noise"], r["n_patches"])

    def test_maps(self):
        cfg = aspx.AspxConfig(start_freq=5, stop_freq=1,
                              master_freq_scale=1, noise_sbg=3)
        t = aspx.sbg_tables(cfg, 0)
        h2l, l2h = aspx.sbg_maps(t)
        assert len(h2l) == t.n_hi
        assert len(l2h) == t.n_lo + 1
        assert all(0 <= v < t.n_lo for v in h2l)


class TestHf:
    """The HF generator and envelope adjuster (Pseudocodes 85-103).

    The reference receiver's HF path is a plain patch copy plus a simplified
    adjuster, so the generator's LPC/pre-flattening and the adjuster's limiter
    mapping are gated by the spec's own closed loop (Pseudocode 95) and by
    contract tests, not by a differential run.
    """

    @staticmethod
    def _tables():
        cfg = aspx.AspxConfig(start_freq=5, stop_freq=1,
                              master_freq_scale=1, noise_sbg=3)
        return cfg, aspx.sbg_tables(cfg, 0)

    def test_patch_copy(self):
        from ac4bindings import aspx_hf as H
        _, t = self._tables()
        rng = np.random.default_rng(1)
        nts = 24
        low = H.LowBand(current=(rng.standard_normal((t.sba, nts))
                                 + 1j * rng.standard_normal((t.sba, nts))),
                        previous=(rng.standard_normal((t.sba, nts))
                                  + 1j * rng.standard_normal((t.sba, nts))))
        Qh = H.hf_generate(low, t, nts, aspx.TS_OFFSET_HFGEN[1536],
                           np.zeros(t.sba, complex), np.zeros(t.sba, complex),
                           [0.0] * t.n_noise, np.ones(t.sba), preflat=False)
        for p in range(t.n_patches):
            dst = t.patches[p] - t.sbx
            src = t.patch_start_sb[p]
            n = t.patch_num_sb[p]
            assert np.array_equal(Qh[dst:dst + n],
                                  low.current[src:src + n])

    def test_closed_loop(self):
        """Pseudocode 95 with EPSILON negligible and no limiter binding:
        est * gain^2 == scf_sig."""
        from ac4bindings import aspx_hf as H
        _, t = self._tables()
        rng = np.random.default_rng(2)
        n_sb = t.num_sb_aspx
        n_env = 3
        # est >> EPSILON (1.0) so the denominator is the measured energy, and
        # scf/est keeps the unlimited gain under the limiter ceiling.
        est = rng.uniform(1e8, 1e9, size=(n_sb, n_env))
        scf_sig = est * rng.uniform(0.5, 2.0, size=(n_sb, n_env))
        scf_noise = np.zeros((n_sb, n_env))
        gain, _, _ = H.adjust(scf_sig, scf_noise, est, t,
                              np.zeros((n_sb, n_env), int), -1, -1)
        err = np.abs(est * gain ** 2 - scf_sig) / scf_sig
        assert np.median(err) < 1e-6

    def test_limiter_bounds(self):
        """Pseudocodes 96/100: the gain cap and boost cap bind and hold."""
        from ac4bindings import aspx_hf as H
        _, t = self._tables()
        rng = np.random.default_rng(3)
        n_sb = t.num_sb_aspx
        n_env = 2
        scf_sig = rng.uniform(1.0, 1e6, size=(n_sb, n_env))
        scf_noise = rng.uniform(0.0, 0.5, size=(n_sb, n_env))
        est = rng.uniform(1e-4, 1.0, size=(n_sb, n_env))
        gain, noise, _ = H.adjust(scf_sig, scf_noise, est, t,
                                  np.zeros((n_sb, n_env), int), -1, -1)
        assert np.isfinite(gain).all()
        assert (gain >= 0).all() and (noise >= 0).all()
        # boost is capped per group, so an individual gain cannot exceed the
        # unlimited gain by more than MAX_BOOST_FACT
        unlim = np.sqrt(scf_sig / (H.EPSILON + est) / (1.0 + scf_noise))
        ratio = gain / np.maximum(unlim, 1e-30)
        assert ratio.max() <= H.MAX_BOOST_FACT + 1e-6

    def test_sine_middle(self):
        from ac4bindings import aspx_hf as H
        _, t = self._tables()
        ah = [0] * t.n_hi
        ah[1] = 1
        sine, _ = H.sine_idx_from_harmonics(
            ah, t, (1,) * 4, tsg_ptr=0, state=H.SineState())
        placed = np.nonzero(sine.sum(axis=1))[0]
        sba = t.hi[1] - t.sbx
        sbz = t.hi[2] - t.sbx
        assert list(placed) == [int(0.5 * (sbz + sba))]

    def test_process_interval_finite(self):
        """The whole interval chain runs and stays finite on real geometry."""
        from ac4bindings import aspx_hf as H
        cfg, t = self._tables()
        rng = np.random.default_rng(7)
        # a synthetic two-frame core QMF matrix
        n_total = 2 * 24
        Q = (rng.standard_normal((64, n_total)) * 0.01
             + 1j * rng.standard_normal((64, n_total)) * 0.01)
        Q_high = np.zeros((t.num_sb_aspx, n_total), complex)
        low = H.LowBand(current=Q)
        states = H.HfStates()
        borders = (0, 12)
        env = H.ChannelEnv(
            signal=np.full((t.num_sb_aspx, 1), 1e3),
            noise=np.zeros((t.num_sb_aspx, 1)),
            borders=borders, tsg_ptr=0, freq_res=(1,))
        noise_values = np.exp(1j * rng.uniform(0, 2 * np.pi, 512))
        out = H.process_interval(Q_high, t, _FakeGroup(t), low, states, cfg,
                                 env, noise_values, slot=0, nts=24,
                                 ts_offset_hfgen=6)
        assert out.shape == (t.num_sb_aspx, n_total)
        assert np.isfinite(out).all()
        assert np.abs(out).sum() > 0


class _FakeGroup:
    """A minimal ``AspxGroup`` stand-in for the interval contract test."""

    def __init__(self, t):
        self.framing = type("F", (), {"borders": (0, 12), "tsg_ptr": 0,
                                      "freq_res": (1,), "num_env": 1})()
        self.tna = [0] * t.n_noise
        self.ah = [0] * t.n_hi


def _asf_trees():
    from ac4bindings import build_huff_tree, huffman
    trees = {cb: build_huff_tree(getattr(huffman, f"ASF_HCB_{cb}_LEN"),
                                 getattr(huffman, f"ASF_HCB_{cb}_CW"))
             for cb in range(1, 12)}
    sf = build_huff_tree(huffman.ASF_HCB_SCALEFAC_LEN,
                         huffman.ASF_HCB_SCALEFAC_CW)
    snf = build_huff_tree(huffman.ASF_HCB_SNF_LEN, huffman.ASF_HCB_SNF_CW)
    return trees, sf, snf


def _element(frames, index):
    """-> (element dict, substream bytes, b_iframe) for the v2 frame."""
    from ac4bindings import parse_toc, element
    v2 = [f for f in frames if (f[0] >> 6) & 3 == 2]
    raw = v2[index]
    toc = parse_toc(raw)
    o = toc["toc_bytes"] + toc.get("payload_base", 0) \
        + toc["substream_sizes"][0]
    sub = raw[o:o + toc["substream_sizes"][1]]
    trees, sf, snf = _asf_trees()
    b_iframe = bool(toc["b_iframe_global"])
    el = element(sub, trees, sf, snf, b_iframe=b_iframe)
    return el, sub, b_iframe


class TestQMF:
    """The 64-band complex QMF bank (Pseudocode 65/66)."""

    def test_window_shape(self):
        w = np.asarray(aspx_tables.QWIN)
        assert len(w) == 640
        assert np.allclose(np.abs(w[1:]), np.abs(w[1:])[::-1], atol=1e-9)

    def test_kernel_matches_reference(self):
        from ac4bindings import qmf_analyse, qmf_synthesise
        from ac4bindings import aspx_qmf
        rng = np.random.default_rng(3)
        x = rng.standard_normal(64 * 24)
        ref, filt_ref = aspx_qmf.analyse(x)
        filt = np.zeros(640)
        got = qmf_analyse(x, filt)
        assert np.abs(got - ref).max() < 1e-12
        assert np.abs(filt - filt_ref).max() < 1e-12
        yref, syn_ref = aspx_qmf.synthesise(ref)
        filt2 = np.zeros(1280)
        y = qmf_synthesise(ref, filt2)
        assert np.abs(y - yref).max() < 1e-12
        assert np.abs(filt2 - syn_ref).max() < 1e-12

    def test_perfect_reconstruction(self):
        from ac4bindings import aspx_qmf
        rng = np.random.default_rng(5)
        x = rng.standard_normal(64 * 300)
        Q, _ = aspx_qmf.analyse(x)
        y, _ = aspx_qmf.synthesise(Q)
        best, lag = -1.0, 0
        for d in range(0, 1024):
            if d + 4096 > len(y):
                break
            c = float(np.corrcoef(x[:4096], y[d:d + 4096])[0, 1])
            if c > best:
                best, lag = c, d
        a = x[2048:len(x) - 2048]
        b = y[lag + 2048:lag + 2048 + len(a)]
        snr = 10 * np.log10((a ** 2).mean() / max(((a - b) ** 2).mean(), 1e-30))
        assert lag == 577
        assert snr > 40.0


class TestRender:
    """The end-to-end A-SPX render (core QMF + HF) on real frames.

    The reference receiver's HF path is a simplification, so this is gated on
    the three properties the reference's own renderer uses: the 12..21 kHz
    band gains energy, nothing appears above 21 kHz, and the core band is
    undisturbed (a high zero-lag correlation against the core-only render).
    """

    @staticmethod
    def _render(use_hf):
        from ac4bindings import (parse_toc, element, build_huff_tree,
                                 huffman, aspx, aspx_render)
        frames = _frames("ac4_frames_mmtp_pid13.bin")
        v2 = [f for f in frames if (f[0] >> 6) & 3 == 2]
        trees = {
            cb: build_huff_tree(getattr(huffman, f"ASF_HCB_{cb}_LEN"),
                                getattr(huffman, f"ASF_HCB_{cb}_CW"))
            for cb in range(1, 12)}
        sf = build_huff_tree(huffman.ASF_HCB_SCALEFAC_LEN,
                             huffman.ASF_HCB_SCALEFAC_CW)
        snf = build_huff_tree(huffman.ASF_HCB_SNF_LEN,
                              huffman.ASF_HCB_SNF_CW)
        atrees = aspx.build_codebook_trees()
        cfg = t = None
        fstate = {}
        data = []
        for raw in v2:
            toc = parse_toc(raw)
            o = (toc["toc_bytes"] + toc.get("payload_base", 0)
                 + toc["substream_sizes"][0])
            sub = raw[o:o + toc["substream_sizes"][1]]
            ifr = bool(toc["b_iframe_global"])
            el = element(sub, trees, sf, snf, b_iframe=ifr)
            if el["aspx"]:
                cfg = aspx.AspxConfig.from_mapping(el["aspx"])
                t = aspx.sbg_tables(cfg, 0)
            groups = None
            if cfg is not None and el["codec_mode"] == 1:
                try:
                    groups, _ = aspx.parse_payload(
                        sub, el["bitpos"], t, cfg, atrees, 12, ifr, fstate)
                except Exception:
                    groups = None
            data.append((el, groups))
        st = aspx_render.RenderState(cfg=cfg, tables=t)
        out, Q, Qh = aspx_render.render(data, st, use_hf=use_hf)
        return out, Q, Qh, t

    def test_high_band_gates(self):
        out_hf, Q, Qh, t = self._render(True)
        out_core, _, _, _ = self._render(False)
        sbx, top = t.sbx, t.sbx + t.num_sb_aspx
        # Every A-SPX channel (all but the LFE) gains the high band, stops at
        # 21 kHz and keeps its core band.
        for name in ("L", "R", "Ls", "Rs", "C"):
            Ec = (np.abs(Q[name]) ** 2).sum(axis=1)
            Eh = (np.abs(Qh[name]) ** 2).sum(axis=1)
            frac_c = Ec[sbx:top].sum() / Ec.sum()
            frac_h = Eh[sbx:top].sum() / Eh.sum()
            assert frac_h > 2 * frac_c, (name, frac_c, frac_h)
            above_c = Ec[top:].sum() / Ec.sum()
            above_h = Eh[top:].sum() / Eh.sum()
            assert abs(above_h - above_c) < 1e-5, name
            n = min(len(out_hf[name]), len(out_core[name]))
            r = np.corrcoef(out_core[name][:n], out_hf[name][:n])[0, 1]
            assert r > 0.98, (name, r)

    def test_finite(self):
        out, _, _, _ = self._render(True)
        for name in ("L", "R", "Ls", "Rs", "C"):
            assert np.isfinite(out[name]).all(), name


class TestCore:
    """The core packed spectrum + ungroup (clauses 5.1.3 / Pseudocode 25)."""

    def test_packed_and_ungroup(self):
        import gzip as _gzip
        from ac4bindings import build_huff_tree, huffman, element, core
        refs = json.loads(_gzip.decompress(
            (DATA / "ac4_core_ref.json.gz").read_bytes()).decode())
        frames = _frames("ac4_frames_mmtp_pid13.bin")
        v2 = [f for f in frames if (f[0] >> 6) & 3 == 2]
        trees = {
            cb: build_huff_tree(getattr(huffman, f"ASF_HCB_{cb}_LEN"),
                                getattr(huffman, f"ASF_HCB_{cb}_CW"))
            for cb in range(1, 12)}
        sf = build_huff_tree(huffman.ASF_HCB_SCALEFAC_LEN,
                             huffman.ASF_HCB_SCALEFAC_CW)
        snf = build_huff_tree(huffman.ASF_HCB_SNF_LEN,
                              huffman.ASF_HCB_SNF_CW)
        for ref in refs:
            raw = v2[ref["frame"]]
            toc = __import__("ac4bindings").parse_toc(raw)
            o = toc["toc_bytes"] + toc.get("payload_base", 0) \
                + toc["substream_sizes"][0]
            sub = raw[o:o + toc["substream_sizes"][1]]
            el = element(sub, trees, sf, snf,
                         b_iframe=bool(toc["b_iframe_global"]))
            ch = el["channels"]["L"]
            fr = core.framing_view(ch["framing"])
            packed = core.packed_spectrum(ch, fr)
            assert np.allclose(packed,
                               np.asarray(ref["packed"], dtype=np.float64),
                               atol=1e-6), ref["frame"]
            wins, lengths = core.ungroup_float(packed, fr)
            assert lengths == ref["lengths"]
            for w, length in enumerate(ref["lengths"]):
                assert np.allclose(
                    wins[w][:length],
                    np.asarray(ref["ungrouped"][w], dtype=np.float64),
                    atol=1e-6), (ref["frame"], w)


class TestPayload:
    """Payload parse + envelope reconstruction on the real RF33 element."""

    def test_codebook_order(self):
        assert len(aspx.ASPX_CB_NAMES) == 18
        assert len(aspx.build_codebook_trees()) == 18

    def test_parse_and_reconstruct(self):
        frames = _frames("ac4_frames_mmtp_pid13.bin")
        aspx_trees = aspx.build_codebook_trees()
        cfg, t = None, None
        state = {}
        prev = {}
        checked = 0
        for ref in PAYLOAD_REF:
            el, sub, b_iframe = _element(frames, ref["frame"])
            if b_iframe:
                cfg = aspx.AspxConfig.from_mapping(el["aspx"])
                t = aspx.sbg_tables(cfg, ref["xover"])
            assert cfg is not None
            groups, pos = aspx.parse_payload(
                sub, el["bitpos"], t, cfg, aspx_trees,
                aspx.num_aspx_timeslots(), b_iframe, state)
            assert pos == ref["bitpos"], ref["frame"]
            maps = aspx.sbg_maps(t)
            for gi, (g, rg) in enumerate(zip(groups, ref["groups"])):
                fr = g.framing
                assert fr.int_class == rg["int_class"]
                assert fr.num_env == rg["num_env"]
                assert list(fr.borders) == rg["borders"]
                assert g.balance == rg["balance"]
                assert [list(e) for e in g.sig] == rg["sig"]
                q, prev[gi] = aspx.reconstruct_signal(
                    g, t, maps, prev.get(gi, (None, 0)))
                assert [[int(x) for x in e] for e in q] == rg["qscf"]
            checked += 1
        assert checked == len(PAYLOAD_REF)
