"""ATSC 3.0 receiver: the validated physical-layer chain in one place.

This is the canonical receive path, exercised end to end on real air.  It runs:

    raw IQ capture
      -> resample to 6.144 MHz, detect the bootstrap        (frontend, bootstrap)
      -> locate the first Preamble symbol, FFT, equalize,
         frequency-deinterleave                              (preamble)
      -> L1-Basic FEC decode + parse                       (l1_basic, l1_signaling)
      -> L1-Detail FEC decode + parse -> per-PLP config     (l1_detail, l1_signaling)
      -> PLP FEC -> Baseband Packets                        (payload)
      -> ALP -> IPv4/UDP -> Low-Level Signaling             (baseband, alp, ip)

:func:`decode_capture` stops at the per-PLP configuration; :func:`decode_plp_payload`
and :func:`decode_plp_streams` extend the chain through the payload and the
link/network layers.
"""

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np

from . import spec
from .bootstrap import detect_bootstrap, fine_cfo
from .crc import crc32_ok
from .frontend import read_hackrf_iq, resample_iq
from .l1_basic import L1BasicCodec
from .l1_detail import L1DetailCodec, preamble_block_deinterleave
from .l1_signaling import L1Basic, L1Detail, parse_l1_basic, parse_l1_detail
from . import l1_signaling
from .preamble import preamble_l1_cells, preamble_symbol_cells


#: Byte-variance ratio above which an interleaved IQ file is judged int16
#: (CS16) rather than int8 (CS8).  A CS16 high byte carries the signal (its
#: variance is ~1.5x the low byte), while a CS8 file has equal byte variance.
_CS16_BYTE_VARIANCE_RATIO = 1.25


@dataclass
class ReceiverResult:
    """Outcome of decoding the signalling of one frame."""
    preamble_structure: int
    l1_basic: Optional[L1Basic]
    l1_basic_ok: bool
    l1_detail: Optional[L1Detail]
    l1_detail_ok: bool
    bootstrap_start: int
    frame_start: int
    error: Optional[str] = None

    @property
    def plps(self):
        """Flat list of (subframe_index, PLPConfig) for the frame."""
        if self.l1_detail is None:
            return []
        out = []
        for sf in self.l1_detail.subframes:
            for plp in sf['plps']:
                out.append((sf['index'], plp))
        return out


#: A/322 7.2.2.2: the minimum frame length bounds how soon a bootstrap can
#: recur, so a bounded acquisition search of a couple of minimum frame periods
#: finds a bootstrap for any emitter whose frame length is near the minimum —
#: the RF33 lighthouse (52.7 ms frames) is exactly this case.  A 5 s-frame
#: emitter is not guaranteed to have a bootstrap inside that window: on the
#: wire the receiver simply advances and searches the next window, while an
#: offline capture can opt into ``full_search`` to scan the whole buffer.
_MIN_FRAME_LENGTH_S = spec.MIN_FRAME_LENGTH_S
_ACQUIRE_SEARCH_PERIODS = 2
_ACQUIRE_SEARCH_S = _ACQUIRE_SEARCH_PERIODS * _MIN_FRAME_LENGTH_S


def _detect_bootstrap_bounded(boot: np.ndarray, full_search: bool = False):
    """Detect a bootstrap, bounding the correlation search (A/322 7.2.2.2).

    By default only ``_ACQUIRE_SEARCH_S`` seconds are searched, which is the
    wire behaviour: a miss means "no bootstrap in this window yet", not "scan
    the whole buffer".  ``full_search=True`` retries over the entire input when
    the bounded window misses, for offline captures of long-frame emitters.
    """
    search_limit = int(_ACQUIRE_SEARCH_S * spec.BOOTSTRAP_RATE_HZ)
    try:
        return detect_bootstrap(boot, search_limit=search_limit)
    except ValueError:
        if not full_search or len(boot) <= search_limit:
            raise
        return detect_bootstrap(boot)


def _bootstrap_to_preamble(iq_main: np.ndarray, fs_main: float,
                           full_search: bool = False):
    """Detect the bootstrap and return (preamble body, main frame, structure, start).

    ``main`` is the main-rate IQ beginning at the bootstrap, de-rotated by the
    fractional carrier-frequency offset estimated from the bootstrap; the first
    Preamble OFDM symbol body follows after the bootstrap span and the guard
    interval.
    """
    boot = resample_iq(iq_main, fs_main, spec.BOOTSTRAP_RATE_HZ)
    det = _detect_bootstrap_bounded(boot, full_search=full_search)
    cfo = fine_cfo(boot, det.start)

    main = resample_iq(iq_main, fs_main, spec.MAIN_RATE_HZ)
    start = int(round(det.start * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
    main = main[start:]
    if cfo:
        n = np.arange(len(main))
        main = (main * np.exp(-1j * 2 * np.pi * cfo * n
                              / spec.MAIN_RATE_HZ)).astype(np.complex64)
    boot_span = int(round(spec.BOOTSTRAP_TOTAL_SAMPLES
                          * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
    params = spec.PREAMBLE_STRUCTURE[det.structure]
    symbol = main[boot_span + params.gi:boot_span + params.gi
                  + params.fft].astype(np.complex128)
    return symbol, main, det.structure, start


def decode_signaling(iq_main: np.ndarray, fs_main: float,
                     max_iterations: int = 100,
                     full_search: bool = False) -> ReceiverResult:
    """Decode L1-Basic and L1-Detail from a raw IQ capture.

    Args:
        iq_main: Complex IQ samples at the capture sample rate.
        fs_main: Capture sample rate in Hz (e.g. 10e6 for the HackRF).
        max_iterations: LDPC iteration cap for both signalling blocks.
        full_search: Retry the bootstrap correlation over the whole capture when
            the bounded wire search misses (offline long-frame captures).

    Returns:
        ReceiverResult with the parsed L1-Basic, L1-Detail (and therefore the
        per-PLP configuration), plus the bootstrap/frame sample offsets.
    """
    try:
        symbol, main, structure, start = _bootstrap_to_preamble(
            iq_main, fs_main, full_search=full_search)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the caller
        return ReceiverResult(preamble_structure=-1, l1_basic=None,
                              l1_basic_ok=False, l1_detail=None,
                              l1_detail_ok=False, bootstrap_start=-1,
                              frame_start=-1,
                              error=f"bootstrap/preamble: {exc}")

    params = spec.PREAMBLE_STRUCTURE[structure]
    cells, _ = preamble_l1_cells(symbol, structure)

    # --- L1-Basic: first 484 cells (Mode 3), then parse ---
    lb_codec = L1BasicCodec(params.l1b_mode, max_iterations=max_iterations)
    lb_bits, lb_ok = lb_codec.decode_cells(cells[:lb_codec.n_cells])
    if not (lb_ok and crc32_ok(lb_bits)):
        return ReceiverResult(preamble_structure=structure, l1_basic=None,
                              l1_basic_ok=False, l1_detail=None,
                              l1_detail_ok=False, bootstrap_start=start,
                              frame_start=start,
                              error="L1-Basic did not verify")
    l1b = parse_l1_basic(lb_bits)

    # --- L1-Detail: L1B_L1_Detail_total_cells across the Preamble symbols ---
    ksig = l1b.l1_detail_size_bytes * 8
    ld_codec = L1DetailCodec(l1b.l1_detail_fec_type + 1, ksig,
                             max_iterations=max_iterations)
    boot_span = int(round(spec.BOOTSTRAP_TOTAL_SAMPLES
                          * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
    detail_cells = _preamble_l1_detail_cells(
        cells, main, boot_span, structure, params, l1b,
        lb_codec.n_cells, ld_codec.n_cells)
    if detail_cells is None:
        return ReceiverResult(preamble_structure=structure, l1_basic=l1b,
                              l1_basic_ok=True, l1_detail=None,
                              l1_detail_ok=False, bootstrap_start=start,
                              frame_start=start,
                              error="L1-Detail cells incomplete")
    if l1b.preamble_num_symbols > 0:
        detail_cells = preamble_block_deinterleave(
            detail_cells, l1b.preamble_num_symbols + 1)
    ld_bits, bch_ok, ld_crc_ok = ld_codec.decode_cells(detail_cells)
    if not (bch_ok and ld_crc_ok):
        return ReceiverResult(preamble_structure=structure, l1_basic=l1b,
                              l1_basic_ok=True, l1_detail=None,
                              l1_detail_ok=False, bootstrap_start=start,
                              frame_start=start,
                              error="L1-Detail did not verify")
    l1d = parse_l1_detail(ld_bits, l1b)

    return ReceiverResult(preamble_structure=structure, l1_basic=l1b, l1_basic_ok=True,
                          l1_detail=l1d, l1_detail_ok=True,
                          bootstrap_start=start, frame_start=start)


def _preamble_l1_detail_cells(first_cells, main, boot_span, structure, params,
                              l1b, n_l1b_cells, n_detail_cells):
    """Gather L1-Detail cells from the Preamble symbol(s).

    L1-Basic occupies the start of the first Preamble symbol; L1-Detail fills
    the rest of that symbol and then the later Preamble symbols (A/322
    7.2.5.1/7.2.5.2).  The later symbols use ``L1B_preamble_reduced_carriers``
    and the frequency-interleaver symbol counter continues frame-frame (the
    first Preamble symbol is symbol 0).  Returns the first ``n_detail_cells``
    cells in mapping order, or None when the capture does not contain them.
    """
    parts = [first_cells[n_l1b_cells:]]
    n_have = len(parts[0])
    fft, gi = params.fft, params.gi
    cred = l1b.preamble_reduced_carriers
    for sym_index in range(1, l1b.preamble_num_symbols + 1):
        if n_have >= n_detail_cells:
            break
        start = boot_span + (fft + gi) * sym_index + gi
        body = main[start:start + fft]
        if len(body) < fft:
            return None
        sym_cells = preamble_symbol_cells(
            body.astype(np.complex128), structure, fi_index=sym_index,
            cred_coeff=cred)
        parts.append(sym_cells)
        n_have += len(sym_cells)
    return np.concatenate(parts)[:n_detail_cells]


def _guess_sample_format(path: str) -> str:
    """Distinguish an int8 (CS8) from an int16 (CS16) interleaved IQ file.

    In a CS16 file the bytes come in little-endian I/Q pairs, so the high byte
    of each sample (odd byte positions) carries the signal while the low byte
    (even byte positions) is near-uniform over its full range.  In a CS8 file
    every byte is an I or Q sample with equal variance.  The odd/even
    byte-variance ratio therefore separates them (CS16 ~1.5, CS8 ~1.0).
    """
    raw = np.fromfile(path, dtype=np.uint8, count=4_000_000)
    if raw.size < 4:
        return 'cs8'
    low = raw[0::2].astype(np.float64).std()
    high = raw[1::2].astype(np.float64).std()
    return 'cs16' if high > _CS16_BYTE_VARIANCE_RATIO * low else 'cs8'


def _read_iq(path: str, fmt: str) -> np.ndarray:
    """Read an interleaved IQ file in the given sample format to complex64."""
    if fmt in ('cs8', 'int8', 'hackrf'):
        return read_hackrf_iq(path)
    if fmt in ('cf32', 'float32'):
        raw = np.fromfile(path, dtype=np.float32)
        return (raw[::2] + 1j * raw[1::2]).astype(np.complex64)
    if fmt in ('cs16', 'int16'):
        raw = np.fromfile(path, dtype=np.int16)
        return ((raw[::2] + 1j * raw[1::2]) / 32768.0).astype(np.complex64)
    raise ValueError(f"Unknown capture format {fmt!r}")


def decode_capture(path: str, fs_main: float, fmt: str = 'auto',
                   max_iterations: int = 100,
                   full_search: bool = False) -> ReceiverResult:
    """Decode the signalling from a saved IQ capture file.

    ``fmt`` selects the sample format: 'cs8' (int8 interleaved IQ), 'cs16'
    (int16) or 'cf32' (float32).  'auto' detects int8 vs int16 by byte
    variance and defaults to int8.  ``full_search`` widens the bootstrap
    correlation search to the whole file for long-frame captures.
    """
    if fmt == 'auto':
        fmt = _guess_sample_format(path)
    iq = _read_iq(path, fmt)
    return decode_signaling(iq, fs_main, max_iterations=max_iterations,
                            full_search=full_search)


def _subframe0_geometry(result):
    """Resolve subframe-0 demod geometry from a decoded ReceiverResult."""
    lb = result.l1_basic
    sf0 = result.l1_detail.subframes[0]
    fft = l1_signaling.FFT_SIZE_2BIT.get(lb.first_sub_fft_size, 8192)
    gi = spec.guard_interval(fft, lb.first_sub_guard_interval)
    noc = spec.noc(fft, lb.first_sub_reduced_carriers)
    pattern = spec.SP_PATTERN_SIGNALING.get(
        lb.first_sub_scattered_pilot_pattern, 'SP4_2')
    dx, dy = spec.SP_DXDY.get(pattern, (4, 2))
    n_sym = sf0.get('num_ofdm_symbols')
    if n_sym is None:
        n_sym = lb.first_sub_num_ofdm_symbols
    n_data_symbols = n_sym + 1
    sbs = []
    if sf0.get('sbs_first') or lb.first_sub_sbs_first:
        sbs.append(0)
    if sf0.get('sbs_last') or lb.first_sub_sbs_last:
        sbs.append(n_data_symbols - 1)
    sbs_null = sf0.get('sbs_null_cells')
    n_preamble_symbols = lb.preamble_num_symbols + 1
    return dict(fft=fft, gi=gi, noc=noc, dx=dx, dy=dy,
                n_data_symbols=n_data_symbols, sbs=tuple(sbs),
                pattern=pattern, sbs_null=sbs_null,
                cred=lb.first_sub_reduced_carriers,
                n_preamble_symbols=n_preamble_symbols,
                fi_offset=n_preamble_symbols,
                fi_enabled=bool(sf0.get('frequency_interleaver', 1)))


def subframe_geometry(result, index: int):
    """Resolve the demod geometry of subframe ``index`` from L1 signalling.

    Subframe 0 draws its geometry from L1-Basic (the first-subframe fields);
    subframes after it carry their own L1-Detail fields (A/322 Table 9.8).
    ``fi_offset`` is the frequency-interleaver symbol-counter origin for the
    first data symbol: 1 for subframe 0 (the Preamble is symbol 0) and 0 for
    every later subframe, whose counter resets at the boundary (A/322 7.3).
    """
    if index == 0:
        return _subframe0_geometry(result)
    sf = result.l1_detail.subframes[index]
    fft = l1_signaling.FFT_SIZE[sf['fft_size']]
    gi = spec.guard_interval(fft, sf['guard_interval'])
    cred = sf['reduced_carriers']
    noc = spec.noc(fft, cred)
    pattern = spec.SP_PATTERN_SIGNALING[sf['scattered_pilot_pattern']]
    dx, dy = spec.SP_DXDY[pattern]
    n_data_symbols = sf['num_ofdm_symbols'] + 1
    sbs = []
    if sf.get('sbs_first'):
        sbs.append(0)
    if sf.get('sbs_last'):
        sbs.append(n_data_symbols - 1)
    return dict(fft=fft, gi=gi, noc=noc, dx=dx, dy=dy,
                n_data_symbols=n_data_symbols, sbs=tuple(sbs),
                pattern=pattern, sbs_null=sf.get('sbs_null_cells'),
                cred=cred, fi_offset=0,
                fi_enabled=bool(sf.get('frequency_interleaver', 1)))


def _subframe_start_offset(result) -> int:
    """Frame sample offset of subframe 1's first symbol (guard-interval start).

    The boot span is added by the caller (it is where ``main`` begins).  This
    accumulates whole symbols from the first Preamble symbol through the end of
    subframe 0, each at its own (FFT + GI) pitch.
    """
    lb = result.l1_basic
    structure = result.preamble_structure
    pre = spec.PREAMBLE_STRUCTURE[structure]
    np_sym = lb.preamble_num_symbols + 1
    off = (pre.fft + pre.gi) * np_sym
    sf0 = _subframe0_geometry(result)
    off += (sf0['fft'] + sf0['gi']) * sf0['n_data_symbols']
    return off


def decode_plp_payload(iq_main: np.ndarray, fs_main: float, plp_id: int = None,
                       max_iterations: int = 100, result=None,
                       subframe: int = 0, fine_timing: bool = False,
                       cpe: bool = False, full_search: bool = False):
    """Decode one data PLP's payload from a named subframe (default 0).

    ``plp_id`` selects the PLP; when None the smallest layer-0 PLP of the
    subframe is chosen.  ``result`` may supply an already-decoded
    :class:`ReceiverResult` to avoid re-running the signalling chain.  Returns
    ``(result, payload)`` with ``payload`` a
    :class:`~atsc3lib.payload.PlpPayload` or None.

    Subframe 0 includes the Preamble's spare cells; every later subframe is
    demodulated at its own FFT/GI/pilot geometry with the A/322 7.3 frequency
    interleaver counter reset at the subframe boundary.  ``fine_timing``
    refines the FFT window off the scattered pilots; ``cpe`` runs the
    decision-directed per-symbol common-phase correction.
    """
    if result is None:
        result = decode_signaling(iq_main, fs_main,
                                  max_iterations=max_iterations,
                                  full_search=full_search)
    target = select_plp(result, plp_id, subframe)
    if target is None:
        return result, None

    _, main, structure, _ = _bootstrap_to_preamble(
        iq_main, fs_main, full_search=full_search)
    payload = decode_plp_frame(result, structure, main, target, subframe,
                               max_iterations=max_iterations,
                               fine_timing=fine_timing, cpe=cpe)
    return result, payload


def select_plp(result, plp_id: int = None, subframe: int = 0):
    """The layer-0 :class:`PLPConfig` to decode, or None.

    ``plp_id=None`` picks the smallest layer-0 PLP of the subframe (the
    RF33-class QPSK PLP alongside a large payload PLP).
    """
    if not result.l1_detail_ok or subframe >= len(result.l1_detail.subframes):
        return None
    sf = result.l1_detail.subframes[subframe]
    candidates = [p for p in sf['plps'] if p.layer == 0]
    if not candidates:
        return None
    if plp_id is None:
        return min(candidates, key=lambda p: p.size)
    return next((p for p in candidates if p.plp_id == plp_id), None)


#: Minimum bootstrap self-similarity for a frame-aligned ``main``.  A bootstrap
#: begins with Part C, a copy of Part A's last ``C_SIZE`` samples at lag
#: ``BOOTSTRAP_FFT_SIZE`` (A/321 5.4); on an aligned frame that match is ~0.9+,
#: while a raw unacquired resample or noise reads ~0.1.  Used to reject a
#: misaligned ``main`` before it silently decodes to 0/N.
_BOOTSTRAP_LOCK_MIN = 0.5
_BOOTSTRAP_LOCK_LAG_TOLERANCE = 48


def bootstrap_lock_metric(main: np.ndarray) -> float:
    """Peak |normalised correlation| of Part C with its Part A repeat.

    Searches a small lag window around the nominal ``BOOTSTRAP_FFT_SIZE`` at the
    main sample rate to absorb the resampler's fractional delay.  Returns a
    value in [0, 1]; ~0.9+ for a frame-aligned ``main``.
    """
    ratio = spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ
    c_len = int(round(spec.BOOTSTRAP_C_SIZE * ratio))
    lag0 = int(round(spec.BOOTSTRAP_FFT_SIZE * ratio))
    if main.size < lag0 + _BOOTSTRAP_LOCK_LAG_TOLERANCE + c_len:
        return 0.0
    a = main[:c_len]
    na = float(np.linalg.norm(a))
    if na == 0.0:
        return 0.0
    best = 0.0
    for lag in range(lag0 - _BOOTSTRAP_LOCK_LAG_TOLERANCE,
                     lag0 + _BOOTSTRAP_LOCK_LAG_TOLERANCE + 1):
        b = main[lag:lag + c_len]
        nb = float(np.linalg.norm(b))
        if nb == 0.0:
            continue
        m = abs(np.vdot(b, a)) / (na * nb)
        if m > best:
            best = float(m)
    return best


def _require_bootstrap_aligned(main: np.ndarray) -> None:
    """Raise if ``main`` is not a frame-aligned, acquired main-rate stream."""
    if bootstrap_lock_metric(main) < _BOOTSTRAP_LOCK_MIN:
        raise ValueError(
            "decode_plp_frame requires the acquired main stream from "
            "_bootstrap_to_preamble (frame-aligned and CFO-corrected); a raw "
            "resample_iq(iq, MAIN_RATE_HZ) silently decodes to 0/N")


def decode_plp_frame(result, structure: int, main: np.ndarray, target,
                     subframe: int = 0, max_iterations: int = 100,
                     fine_timing: bool = False, cpe: bool = False):
    """Decode ``target`` PLP's payload from an acquired main-rate frame.

    ``main`` MUST be the main-rate IQ returned by
    :func:`_bootstrap_to_preamble` (or the equivalent lock path): it must begin
    at a bootstrap and already carry that bootstrap's fractional-CFO
    de-rotation.  A raw ``resample_iq(iq, MAIN_RATE_HZ)`` is *not* a valid input
    — it is neither frame-aligned nor CFO-corrected — and yields garbage (0/N),
    silently, because the demodulator still finds OFDM symbol energy.  Use
    :func:`decode_plp_payload` (which acquires ``main`` itself) unless you are a
    live loop that has already locked.

    ``result`` and ``structure`` carry the already-decoded L1 configuration, so
    a live loop can lock once and decode successive frames without re-acquiring.
    """
    from .payload import decode_subframe0_plp, decode_subframe_plp

    _require_bootstrap_aligned(main)

    g = subframe_geometry(result, subframe)
    boot_span = int(round(spec.BOOTSTRAP_TOTAL_SAMPLES
                          * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
    dummy_start = _dummy_tail_start(result, subframe)
    if subframe == 0:
        n_l1b = L1BasicCodec(
            spec.PREAMBLE_STRUCTURE[structure].l1b_mode).n_cells
        l1_cells = n_l1b + result.l1_basic.l1_detail_total_cells
        return decode_subframe0_plp(
            main, boot_span, g['fft'], g['gi'], g['noc'], g['dx'], g['dy'],
            g['n_data_symbols'], g['sbs'], preamble_structure=structure,
            l1_cells=l1_cells, plp=target, max_iterations=max_iterations,
            pattern=g['pattern'], sbs_null=g['sbs_null'],
            preamble_num_symbols=g['n_preamble_symbols'],
            preamble_reduced_carriers=result.l1_basic.preamble_reduced_carriers,
            l1b_cells=n_l1b, fi_enabled=g['fi_enabled'],
            fine_timing_enabled=fine_timing, cpe=cpe, dummy_start=dummy_start)
    t0 = boot_span + _subframe_start_offset(result)
    return decode_subframe_plp(
        main, t0, g['fft'], g['gi'], g['noc'], g['dx'], g['dy'],
        g['n_data_symbols'], g['sbs'], plp=target,
        max_iterations=max_iterations, pattern=g['pattern'],
        sbs_null=g['sbs_null'], cred_coeff=g['cred'],
        fi_offset=g['fi_offset'], fi_enabled=g['fi_enabled'],
        fine_timing_enabled=fine_timing, cpe=cpe, dummy_start=dummy_start)


def decode_first_plp_payload(iq_main: np.ndarray, fs_main: float,
                             max_iterations: int = 100, result=None):
    """Decode signalling and the smallest subframe-0 PLP payload.

    This is the RF33-class case (a small QPSK PLP alongside a large payload
    PLP).  Returns ``(result, payload)`` where ``payload`` is a
    :class:`~atsc3lib.payload.PlpPayload` or None.
    """
    return decode_plp_payload(iq_main, fs_main, plp_id=None,
                              max_iterations=max_iterations, result=result)


def decode_plp_streams(iq_main: np.ndarray, fs_main: float, plp_id: int = None,
                       max_iterations: int = 100, result=None):
    """Decode one PLP all the way to network-layer streams.

    Runs :func:`decode_plp_payload` and then Baseband Packets -> ALP -> IPv4/6
    -> UDP -> Low-Level Signaling (A/322 5.2, A/330 5, A/331 6.1).  Returns
    ``(result, streams)`` with ``streams`` a
    :class:`~atsc3lib.payload.DecodedStreams` or None.
    """
    from .payload import decode_streams

    result, payload = decode_plp_payload(
        iq_main, fs_main, plp_id=plp_id, max_iterations=max_iterations,
        result=result)
    if payload is None:
        return result, None
    return result, decode_streams(payload)


def acquire_capture(iq: np.ndarray, fs: float, max_iterations: int = 100,
                    full_search: bool = False):
    """Acquire once: ``(result, structure, main)`` or ``(result, None, None)``.

    ``main`` is the bootstrap-aligned, CFO-corrected main-rate stream a caller
    can step frame by frame without re-acquiring (see
    :func:`decode_plp_frame`).
    """
    result = decode_signaling(iq, fs, max_iterations=max_iterations,
                              full_search=full_search)
    if not result.l1_detail_ok:
        return result, None, None
    _symbol, main, structure, _start = _bootstrap_to_preamble(
        iq, fs, full_search=full_search)
    return result, structure, main


def decode_plp_frames_streams(iq: np.ndarray, fs: float,
                              plp_id: int = None, subframe: int = 0,
                              max_iterations: int = 100, full_search: bool = False,
                              max_frames: Optional[int] = None,
                              acquired=None):
    """Acquire once and decode ``plp`` across every frame of a capture.

    A single frame rarely carries a whole media object, so media reassembly
    must drain *all* frames the capture holds.  The lock (L1 config and the
    bootstrap-aligned, CFO-corrected main stream) is taken once and stepped by
    the full frame period (:func:`frame_samples`), never re-acquired, so every
    frame's cells stay aligned.  ``max_frames`` bounds the work; None consumes
    the whole capture.  ``acquired`` reuses an :func:`acquire_capture` result
    so several PLPs share one lock.

    Returns ``(result, structure, payloads)`` with the per-frame
    :class:`~atsc3lib.payload.PlpPayload` objects that converged.
    """
    result, structure, main = acquired if acquired else acquire_capture(
        iq, fs, max_iterations=max_iterations, full_search=full_search)
    if not result.l1_detail_ok:
        return result, structure, []
    target = select_plp(result, plp_id, subframe)
    if target is None:
        return result, structure, []
    period = frame_samples(result)
    need = subframe_end_samples(result, subframe)
    payloads = []
    offset = 0
    while offset + need <= main.size:
        if max_frames is not None and len(payloads) >= max_frames:
            break
        frame = main[offset:offset + period]
        payload = decode_plp_frame(
            result, structure, frame, target, subframe=subframe,
            max_iterations=max_iterations)
        if payload is not None:
            payloads.append(payload)
        offset += period
    return result, structure, payloads


def decode_all_plp_frames(iq: np.ndarray, fs: float,
                          targets=None, max_iterations: int = 100,
                          full_search: bool = False, max_frames=None):
    """Acquire once and drain every target PLP across the whole capture.

    ``targets`` is a list of ``(subframe, plp_id)``; with None every layer-0
    PLP the L1 signalling carries is drained, so media split across PLPs is
    recovered in one pass.  Returns ``(result, payloads)`` with all payloads.
    """
    result, structure, main = acquire_capture(
        iq, fs, max_iterations=max_iterations, full_search=full_search)
    if not result.l1_detail_ok:
        return result, []
    if targets is None:
        targets = sorted({(sf, plp.plp_id) for sf, plp in result.plps
                          if plp.layer == 0})
    acquired = (result, structure, main)
    payloads = []
    for subframe, plp_id in targets:
        _r, _s, got = decode_plp_frames_streams(
            iq, fs, plp_id=plp_id, subframe=subframe,
            max_iterations=max_iterations, full_search=full_search,
            max_frames=max_frames, acquired=acquired)
        payloads.extend(got)
    return result, payloads


def _frame_prefix_samples(result) -> int:
    """Main-rate samples from the bootstrap through the end of subframe 0."""
    pre = spec.PREAMBLE_STRUCTURE[result.preamble_structure]
    g0 = _subframe0_geometry(result)
    boot_span = int(round(spec.BOOTSTRAP_TOTAL_SAMPLES
                          * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
    return (boot_span
            + (pre.fft + pre.gi) * g0['n_preamble_symbols']
            + (g0['fft'] + g0['gi']) * g0['n_data_symbols'])


def subframe_end_samples(result, subframe: int) -> int:
    """Main-rate samples from bootstrap through the end of ``subframe``.

    This is the minimum buffer needed to decode that subframe; it equals the
    old subframe-0-only ``frame_samples`` for ``subframe == 0``.
    """
    total = _frame_prefix_samples(result)
    for index in range(1, subframe + 1):
        g = subframe_geometry(result, index)
        total += (g['fft'] + g['gi']) * g['n_data_symbols']
    return total


def frame_samples(result) -> int:
    """Main-rate samples of one whole frame: bootstrap + all subframes.

    The Preamble and every subframe contribute their own (FFT + GI) symbol
    span; summing only subframe 0 yields the start of subframe 1, not the
    frame period.  On RF33 (subframe 0 = 8192/1536, subframe 1 = 16384/1536)
    the full period is 1,708,032 samples = 247.1 ms, measured bootstrap to
    bootstrap on air.
    """
    return subframe_end_samples(result, len(result.l1_detail.subframes) - 1)


def _dummy_tail_start(result, subframe: int = 0) -> Optional[int]:
    """Pool index where a subframe's A/322 7.2.6.5 dummy tail begins.

    Every layer-0 PLP of the subframe occupies a slice of the pool; the dummy
    tail follows the last of them.  Returns None when the subframe has no
    layer-0 PLP.
    """
    sf = result.l1_detail.subframes[subframe]
    ends = [p.start + p.size for p in sf['plps'] if p.layer == 0]
    return max(ends) if ends else None


def _subframe0_cell_pool(result, main: np.ndarray, boot_span: int,
                         plp=None, fine_timing: bool = False,
                         cpe: bool = False):
    """Build subframe 0's cell pool from a frame-aligned main stream.

    ``main`` starts at the bootstrap, so ``t0`` is ``boot_span`` (the Preamble
    begins after the bootstrap span).  ``plp`` supplies the constellation for
    the CPE and the dummy-tail boundary; ``fine_timing`` refines the FFT window
    off the scattered pilots.
    """
    from .payload import build_cell_pool, cpe_spec
    g = _subframe0_geometry(result)
    pre = spec.PREAMBLE_STRUCTURE[result.preamble_structure]
    n_l1b = L1BasicCodec(pre.l1b_mode).n_cells
    l1_cells = n_l1b + result.l1_basic.l1_detail_total_cells
    return build_cell_pool(
        main, boot_span, g['fft'], g['gi'], g['noc'], g['dx'], g['dy'],
        g['n_data_symbols'], sbs_symbols=g['sbs'],
        preamble_structure=result.preamble_structure, l1_cells=l1_cells,
        pattern=g['pattern'], sbs_null=g['sbs_null'],
        preamble_num_symbols=g['n_preamble_symbols'],
        preamble_reduced_carriers=result.l1_basic.preamble_reduced_carriers,
        l1b_cells=n_l1b, fi_enabled=g['fi_enabled'],
        fine_timing_enabled=fine_timing,
        cpe=cpe_spec(plp, _dummy_tail_start(result, 0))
        if (cpe and plp is not None) else None), g


def decode_cti_plp_streams(iq_main: np.ndarray, fs_main: float,
                           plp_id: int = None, n_frames: int = 6,
                           max_iterations: int = 50, result=None,
                           max_blocks: int = None, fine_timing: bool = True,
                           cpe: bool = True):
    """Decode a CTI-mode PLP across consecutive frames (A/322 7.1.4).

    The convolutional time interleaver never resets, so a FEC block spans
    frames; this gathers the PLP's cells from ``n_frames`` consecutive
    bootstrap-aligned frames, CTI de-interleaves, and decodes from the offset
    the transmitter signals.  Returns ``(result, CtiDecode, streams)``.

    ``fine_timing`` refines each frame's FFT window off the scattered pilots and
    ``cpe`` applies the decision-directed per-symbol common-phase correction;
    both are ON by default for this path, which is where they matter.

    This is the RF30/RF25 Core-layer path (LDM, CTI, Ninner 64800).
    """
    from .payload import decode_cti_plp, decode_streams

    if result is None:
        result = decode_signaling(iq_main, fs_main,
                                  max_iterations=max_iterations)
    if not result.l1_detail_ok:
        return result, None, None

    sf = result.l1_detail.subframes[0]
    candidates = [p for p in sf['plps'] if p.layer == 0]
    if not candidates:
        return result, None, None
    if plp_id is None:
        target = min(candidates, key=lambda p: p.size)
    else:
        target = next((p for p in candidates if p.plp_id == plp_id), None)
    if target is None:
        return result, None, None

    _, main, structure, _ = _bootstrap_to_preamble(iq_main, fs_main)
    boot_span = int(round(spec.BOOTSTRAP_TOTAL_SAMPLES
                          * spec.MAIN_RATE_HZ / spec.BOOTSTRAP_RATE_HZ))
    period = frame_samples(result)
    segs = []
    for n in range(n_frames):
        off = n * period
        if off + boot_span + period > len(main):
            break
        pool, _g = _subframe0_cell_pool(result, main[off:], boot_span,
                                        plp=target, fine_timing=fine_timing,
                                        cpe=cpe)
        segs.append(pool.cells[target.start:target.start + target.size])
    if not segs:
        return result, None, None
    decoded = decode_cti_plp(np.concatenate(segs), target,
                             max_iterations=max_iterations,
                             max_blocks=max_blocks)
    return result, decoded, decode_streams(decoded.payload)
