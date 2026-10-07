"""ATSC 3.0 synthetic transmitter (the inverse of the receive chain).

This builds a complete, decodable frame from L1 signalling and a data-PLP
payload, reusing the validated receive primitives so the two cannot drift:

    L1Basic / L1Detail  -> l1_signaling_encode + L1*Codec.encode
    bits -> cells       -> L1*Codec.bits_to_cells
    payload             -> BCH -> LDPC -> BICM -> NUC -> data cells
    cells -> OFDM        -> ofdm.OfdmGeometry (pilots, FI, IFFT, guard)
    bootstrap            -> bootstrap.generate_bootstrap

The first implemented geometry is 8K / GI1536 / SP4_2 / QPSK 2/15 / short frame,
the RF33 PLP-16 shape; the frame is bounded by the signalled frame length, never
a scan.  Modulation happens at :data:`atsc3lib.spec.MAIN_RATE_HZ` (6.912 MHz).

Gated by ``tests/test_transmit.py`` (RX loopback) and
``tests/test_transmit_security.py`` (the own-CA certificate hook carried over
the real PHY).

Reference: ATSC A/322:2024-04 Sections 5.2, 6, 7, 8; A/330 5.1; A/331 6.1.
"""

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

from . import l1_signaling
from . import nuc
from . import ofdm
from . import spec
from . import twisted_block
from .bch import BCHCode
from .bootstrap import generate_bootstrap
from .frequency_interleaver import interleave as fi_interleave
from .group_interleaver import GroupInterleaver
from .l1_basic import L1BasicCodec
from .l1_detail import L1DetailCodec
from .l1_signaling_encode import l1_basic_to_bits, l1_detail_to_bits
from .ldpc_exact import ATSC3LDPCExact, RATE_MIN
from .nuc import MODULATION_BITS
from .payload import BCH_T, PLP_NINNER
from .signaling_fec import scramble_bits

#: Payload pilot amplitude for data symbols (A/322 8.1.2; unit for the
#: default boost, matching the receiver's ``data_symbol_cells`` default).
PAYLOAD_PILOT_AMPLITUDE = 1.0


@dataclass(frozen=True)
class TransmitFrame:
    """A generated frame and the geometry it was built at."""
    iq: np.ndarray                 # main-rate samples (spec.MAIN_RATE_HZ)
    structure: int                 # preamble_structure
    fft: int
    gi: int
    noc: int
    n_data_symbols: int
    sbs_symbols: Tuple[int, ...]
    fi_offset: int
    l1_basic_cells: int
    l1_detail_cells: int
    plp_cells: int


def build_alp_stream(alp_packets: List[bytes]) -> Tuple[bytes, List[int]]:
    """Concatenate whole IPv4 ALP packets into a payload stream (A/330).

    Returns ``(stream, boundaries)`` where ``boundaries`` are the byte offsets
    at which an ALP packet starts, exactly as the receiver's ``AlpWalker``
    expects from Baseband Packet pointers.
    """
    from . import alp
    parts: List[bytes] = []
    boundaries: List[int] = []
    offset = 0
    for pkt in alp_packets:
        packet = alp.build_single_ipv4_packet(pkt)
        boundaries.append(offset)
        parts.append(packet)
        offset += len(packet)
    return b"".join(parts), boundaries


def build_lls_baseband_packets(ls_payloads: List[bytes], plp) -> Tuple[bytes, int]:
    """Wrap LLS table payloads into Baseband Packets for a PLP (A/331 6.1).

    Each byte string is an LLS table payload (its 4-byte header already
    included); this wraps it in the LLS IPv4/UDP datagram (``ip.build_lls_udp``)
    and an ALP packet, then splits the resulting stream into fixed-length
    Baseband Packets for the PLP's FEC.  Returns ``(bbp_stream, n_fec)``.
    """
    from . import ip
    stream, boundaries = build_alp_stream(
        [ip.build_lls_udp(ls) for ls in ls_payloads])
    packets = baseband_packets_for(stream, boundaries, plp)
    return b"".join(packets), len(packets)


def baseband_packets_for(stream: bytes, boundaries, plp) -> List[bytes]:
    """Split an ALP stream into the PLP's Baseband Packets (A/322 5.2.2)."""
    from . import baseband as _baseband
    return _baseband.pack_baseband_stream(stream, boundaries, _kpayload(plp))


def _mod_name(plp_modulation: int) -> str:
    name = l1_signaling.L1D_MODULATION.get(plp_modulation)
    if name is None or name not in MODULATION_BITS:
        raise NotImplementedError(
            f"modulation index {plp_modulation} not supported by the TX")
    return name


def modulate_plp(payload: bytes, plp, n_fec: int,
                 max_iterations: int = 50) -> np.ndarray:
    """Encode an ALP payload stream into a PLP's on-air cells (A/322 6).

    Inverse of ``payload.decode_data_plp``: the byte stream is split into
    ``n_fec`` fixed Baseband Packets, each scrambled, BCH-encoded, LDPC-encoded,
    BICM bit-interleaved and NUC-mapped.  No HTI/cell interleaver is applied
    (the first rung signals ``TI_mode = 2`` with ``cell_interleaver = 0`` and a
    single TI block, whose twisted block interleaver reduces to the identity).

    Returns a ``(n_fec, cells_per_fec)`` complex array in cell order.
    """
    mod = _mod_name(plp.modulation)
    ninner = PLP_NINNER.get(plp.fec_type)
    if ninner is None:
        raise NotImplementedError(f"fec_type {plp.fec_type} not supported")
    rate = RATE_MIN + plp.code_rate
    bch = BCHCode(ninner, BCH_T)
    ldpc = ATSC3LDPCExact(rate, n=ninner, max_iterations=max_iterations)
    gi = GroupInterleaver(rate, mod, n=ninner)
    kpayload_bits = ldpc.K - bch.mouter
    kpayload_bytes = kpayload_bits // 8
    cells_per_fec = ninner // MODULATION_BITS[mod]

    stream = bytes(payload)
    need = n_fec * kpayload_bytes
    if len(stream) > need:
        raise ValueError(
            f"payload {len(stream)} bytes exceeds {n_fec} FEC blocks "
            f"({need} bytes); raise n_fec")
    stream = stream + b"\x00" * (need - len(stream))

    out = np.empty((n_fec, cells_per_fec), dtype=np.complex128)
    for j in range(n_fec):
        kb = np.unpackbits(np.frombuffer(
            stream[j * kpayload_bytes:(j + 1) * kpayload_bytes],
            dtype=np.uint8))
        scrambled = scramble_bits(kb)
        nouter = np.asarray(bch.encode(scrambled), dtype=np.uint8)
        info = np.zeros(ldpc.K, dtype=np.uint8)
        info[:len(nouter)] = nouter
        codeword = ldpc.encode(info)
        tx_bits = gi.interleave(codeword)
        out[j] = nuc.points(MODULATION_BITS[mod], rate)[
            _bits_to_label(tx_bits, MODULATION_BITS[mod])]
    return out


def _bits_to_label(bits: np.ndarray, mod_bits: int) -> np.ndarray:
    """Pack MSB-first bit groups into constellation labels (A/322 6.3.3)."""
    b = np.asarray(bits, dtype=np.uint8)[:len(bits) // mod_bits * mod_bits]
    groups = b.reshape(-1, mod_bits)
    weights = (1 << np.arange(mod_bits - 1, -1, -1)).astype(np.int64)
    return groups.astype(np.int64).dot(weights)


def _preamble_symbol(structure: int, l1_cells: np.ndarray) -> np.ndarray:
    """Assemble the first Preamble symbol (A/322 7.2.5) with guard interval."""
    pg = ofdm.PreambleGeometry.first(structure)
    carriers = np.zeros(pg.noc, dtype=np.complex128)
    mask = pg.data_mask()
    if len(l1_cells) > int(mask.sum()):
        raise ValueError(
            f"L1 needs {len(l1_cells)} cells but the Preamble has "
            f"{int(mask.sum())}")
    carriers[mask] = fi_interleave(np.asarray(l1_cells, dtype=np.complex128),
                                   0, pg.fft)
    non_data = np.flatnonzero(~mask)
    from .pilot_reference import reference_sequence
    r = reference_sequence(pg.noc).astype(np.int64)
    carriers[non_data] = (pg.amplitude * (1.0 - 2.0 * r[non_data]))
    spectrum = np.zeros(pg.fft, dtype=np.complex128)
    shift = _preamble_carrier_shift(pg.fft, pg.noc)
    spectrum[shift:shift + pg.noc] = carriers
    body = np.fft.ifft(np.fft.ifftshift(spectrum))
    return np.concatenate([body[-pg.gi:], body])


def _preamble_carrier_shift(fft: int, noc: int) -> int:
    from .preamble import carrier_shift
    return carrier_shift(fft, noc)


def _subframe0_geometry(lb: l1_signaling.L1Basic):
    """Subframe-0 geometry from L1-Basic (mirror of ``_subframe0_geometry``)."""
    fft = l1_signaling.FFT_SIZE_2BIT.get(lb.first_sub_fft_size, 8192)
    gi = spec.guard_interval(fft, lb.first_sub_guard_interval)
    cred = lb.first_sub_reduced_carriers
    pattern = spec.SP_PATTERN_SIGNALING.get(
        lb.first_sub_scattered_pilot_pattern, spec.RF33_PATTERN)
    n_data_symbols = (lb.first_sub_num_ofdm_symbols) + 1
    sbs = []
    if lb.first_sub_sbs_first:
        sbs.append(0)
    if lb.first_sub_sbs_last:
        sbs.append(n_data_symbols - 1)
    return ofdm.OfdmGeometry.resolve(fft, gi, cred, pattern), n_data_symbols, tuple(sbs)


def build_frame(l1b: l1_signaling.L1Basic, l1d: l1_signaling.L1Detail,
                plp_payload: bytes, plp=None, n_fec: Optional[int] = None,
                structure: int = 27, frame_interval: int = 0) -> TransmitFrame:
    """Build one ATSC 3.0 frame decodable by :mod:`atsc3lib.receiver`.

    Args:
        l1b: L1-Basic fields (must describe the same geometry passed here).
        l1d: L1-Detail fields.
        plp_payload: the ALP payload byte stream to carry in subframe 0.
        plp: the :class:`PLPConfig` to encode into; defaults to the first
            layer-0 PLP of ``l1d`` subframe 0.
        n_fec: FEC blocks to emit; defaults to the payload's minimum.
        structure: ``preamble_structure`` (Table H.1.1); default 27 (8K/GI1536).
    """
    if l1b.preamble_num_symbols != 0:
        raise NotImplementedError(
            "multi-symbol Preamble transmit is a follow-on (NP must be 1)")
    if plp is None:
        plp = next(p for p in l1d.subframes[0]['plps'] if p.layer == 0)

    # --- signalled payload: L1-Basic then L1-Detail ---
    lb_codec = L1BasicCodec(spec.PREAMBLE_STRUCTURE[structure].l1b_mode)
    l1b_tx = lb_codec.encode(l1_basic_to_bits(l1b))
    l1b_cells = lb_codec.bits_to_cells(l1b_tx)
    ld_codec = L1DetailCodec(l1b.l1_detail_fec_type + 1,
                             l1b.l1_detail_size_bytes * 8)
    l1d_tx = ld_codec.encode(l1_detail_to_bits(l1d, l1b))
    l1d_cells = ld_codec.bits_to_cells(l1d_tx)

    # --- Preamble symbol data (L1-Basic + L1-Detail) ---
    preamble_cells = np.concatenate([l1b_cells, l1d_cells])
    # --- data-PLP cells ---
    n_fec = n_fec if n_fec is not None else max(
        1, -(-len(plp_payload) // _kpayload(plp)))
    mem = modulate_plp(plp_payload, plp, n_fec)
    cells_per_fec = mem.shape[1]
    # A/322 7.1.5.4: the receive path applies the twisted block interleaver to
    # the PLP slice (``twisted_block.fec_block``), so the transmit slice is its
    # inverse over the block-major memory.  ncols = n_fec for one TI block.
    transmitted = twisted_block.interleave(
        mem.reshape(-1), cells_per_fec, n_fec)
    size = len(transmitted)
    if plp.size != size:
        raise ValueError(
            f"PLPConfig.size {plp.size} != {n_fec} FEC blocks ({size} cells); "
            "set size = n_fec * cells_per_fec for the synthetic frame")
    plp_cells = transmitted

    g, n_data_symbols, sbs = _subframe0_geometry(l1b)
    fi_offset = l1b.preamble_num_symbols + 1
    n_null = spec.SBS_NULL_8K_CRED0 if sbs else 0
    lo_n, hi_n = n_null // 2, n_null - n_null // 2

    # --- subframe cell pool capacity, matching build_cell_pool exactly ---
    pg = ofdm.PreambleGeometry.first(structure)
    pre_data = pg.data_cells()
    spare_cap = pre_data - len(preamble_cells)
    if spare_cap < 0:
        raise ValueError("L1-Basic + L1-Detail exceed the Preamble data cells")
    caps = []
    for l in range(n_data_symbols):
        is_sbs = l in sbs
        cap = len(g.data_indices(l, is_sbs)) - (n_null if is_sbs else 0)
        if cap < 0:
            raise ValueError("SBS null cells exceed the boundary-symbol data")
        caps.append(cap)
    total_cap = spare_cap + sum(caps)
    if plp.start + size > total_cap:
        raise ValueError(
            f"PLP [start {plp.start}, size {size}] exceeds the subframe pool "
            f"({total_cap} cells)")

    pool = np.zeros(total_cap, dtype=np.complex128)
    pool[plp.start:plp.start + size] = plp_cells

    # --- Preamble: L1-Basic + L1-Detail + the pool's leading spare cells ---
    pre_alloc = np.concatenate(
        [preamble_cells, pool[:spare_cap]])
    preamble = _preamble_symbol(structure, pre_alloc)

    # --- data symbols, consuming the pool in order ---
    off = spare_cap
    data_symbols = []
    for l in range(n_data_symbols):
        is_sbs = l in sbs
        take = caps[l]
        seq = pool[off:off + take]
        off += take
        if is_sbs:
            seq = np.concatenate([np.zeros(lo_n, dtype=np.complex128), seq,
                                  np.zeros(hi_n, dtype=np.complex128)])
        carriers = np.zeros(g.noc, dtype=np.complex128)
        carriers[g.data_indices(l, is_sbs)] = fi_interleave(
            seq, fi_offset + l, g.fft)
        pk = g.pilot_indices(l, is_sbs)
        carriers[pk] = g.pilot_values(pk)
        body = g.body_of(carriers)
        data_symbols.append(np.concatenate([body[-g.gi:], body]))

    # --- bootstrap + Preamble + data symbols at the main rate ---
    boot_native = generate_bootstrap(structure, frame_interval=frame_interval)
    from .frontend import resample_iq
    boot = resample_iq(boot_native, spec.BOOTSTRAP_RATE_HZ,
                       spec.MAIN_RATE_HZ).astype(np.complex128)
    payload = np.concatenate([preamble] + data_symbols)

    # ``generate_bootstrap`` returns a waveform at its own natural scale while
    # the IFFT payload sits far below it.  On air the two are comparable (RF33
    # bootstrap/data RMS ~0.017/0.020, AGC-relative), and the loopback's AWGN
    # SNR is only meaningful at a realistic ratio, so match the payload RMS to
    # the bootstrap's.  The receiver is absolute-scale-invariant otherwise.
    rb = float(np.sqrt(np.mean(np.abs(boot) ** 2)))
    rp = float(np.sqrt(np.mean(np.abs(payload) ** 2)))
    if rp > 0.0:
        payload = payload * (rb / rp)
    iq = np.concatenate([boot, payload])

    return TransmitFrame(
        iq=iq, structure=structure, fft=g.fft, gi=g.gi, noc=g.noc,
        n_data_symbols=n_data_symbols, sbs_symbols=sbs, fi_offset=fi_offset,
        l1_basic_cells=len(l1b_cells), l1_detail_cells=len(l1d_cells),
        plp_cells=size)


def _kpayload(plp) -> int:
    """Baseband Packet payload bytes for a PLP's FEC (A/322 5.2.2)."""
    ninner = PLP_NINNER[plp.fec_type]
    ldpc = ATSC3LDPCExact(RATE_MIN + plp.code_rate, n=ninner)
    bch = BCHCode(ninner, BCH_T)
    return (ldpc.K - bch.mouter) // 8


#: The 8K/GI1536 subframe-0 signalling the first TX rung emits: one L1-Preamble
#: symbol, subframe-boundary symbols first and last, 34 data symbols (A/322
#: Table 9.2/9.8), scattered pilots SP4_2.
DEFAULT_STRUCTURE = 27
DEFAULT_BSID = 540
L1D_SIZE_BYTES = 64


def synthetic_l1(plp, bsid: int = DEFAULT_BSID,
                 structure: int = DEFAULT_STRUCTURE):
    """Build the L1-Basic/L1-Detail that describe ``plp`` (A/322 Tables 9.2/9.8).

    This is the signalling half of the first TX rung (8K/GI1536/SP4_2, one
    Preamble symbol, a single subframe).  ``plp`` must already carry its
    ``size`` (``n_fec * cells_per_fec``).
    """
    p = spec.PREAMBLE_STRUCTURE[structure]
    sf0 = {'index': 0, 'frequency_interleaver': 1, 'sbs_null_cells': spec.SBS_NULL_8K_CRED0,
           'num_plp': 0, 'plps': [plp]}
    lb = l1_signaling.L1Basic(
        version=0, mimo_scattered_pilot_encoding=0, lls_flag=1,
        time_info_flag=0, return_channel_flag=0, papr_reduction=0,
        frame_length_mode=1, time_offset=0, num_subframes=0,
        preamble_num_symbols=0, preamble_reduced_carriers=0,
        l1_detail_content_tag=0, l1_detail_size_bytes=L1D_SIZE_BYTES,
        l1_detail_fec_type=2, l1_detail_additional_parity_mode=0,
        l1_detail_total_cells=L1DetailCodec(3, L1D_SIZE_BYTES * 8).n_cells,
        first_sub_mimo=0, first_sub_miso=0, first_sub_fft_size=0,
        first_sub_reduced_carriers=0, first_sub_guard_interval=6,
        first_sub_num_ofdm_symbols=34, first_sub_scattered_pilot_pattern=2,
        first_sub_scattered_pilot_boost=1, first_sub_sbs_first=1,
        first_sub_sbs_last=1, crc_ok=True,
        raw={'L1B_time_offset': 0, 'L1B_additional_samples': 0,
             'L1B_reserved': (1 << 48) - 1})
    ld = l1_signaling.L1Detail(
        version=0, num_rf=0, time_sec=None, bsid=bsid, subframes=[sf0],
        reserved_len=0, reserved_all_ones=True, crc_ok=True, raw={})
    return lb, ld


def synthetic_plp(n_fec: int, plp_id: int = 16,
                  lls_flag: int = 1):
    """A 16K QPSK 2/15 PLP of ``n_fec`` Baseband Packets (A/322 Table 9.8)."""
    cells_per_fec = spec.L1B_NINNER // 2
    return l1_signaling.PLPConfig(
        plp_id=plp_id, lls_flag=lls_flag, layer=0, start=0,
        size=n_fec * cells_per_fec, scrambler_type=0, fec_type=0,
        modulation=0, code_rate=0, ti_mode=2, ti_fec_block_start=None,
        hti_inter_subframe=0, hti_num_ti_blocks=0, hti_num_fec_blocks=0,
        hti_cell_interleaver=0, cti_depth=None, cti_start_row=None,
        ti_extended_interleaving=0, ldm_injection_level=None)


def build_lls_frame(ls_payloads: List[bytes], bsid: int = DEFAULT_BSID,
                    plp_id: int = 16, structure: int = DEFAULT_STRUCTURE,
                    frame_interval: int = 0) -> Tuple[TransmitFrame, object]:
    """Build a frame carrying LLS tables as one PLP (A/331 6.1 over A/322).

    Each entry of ``ls_payloads`` is a complete LLS table payload (its 4-byte
    header included).  They are wrapped in the LLS IPv4/UDP datagram (A/331),
    split into Baseband Packets, signalled, and modulated.  Returns
    ``(frame, plp)``.
    """
    from . import ip
    stream, boundaries = build_alp_stream(
        [ip.build_lls_udp(ls) for ls in ls_payloads])
    placeholder = synthetic_plp(1, plp_id)
    packets = baseband_packets_for(stream, boundaries, placeholder)
    n_fec = len(packets)
    plp = synthetic_plp(n_fec, plp_id)
    lb, ld = synthetic_l1(plp, bsid, structure)
    frame = build_frame(lb, ld, b"".join(packets), plp=plp, n_fec=n_fec,
                        structure=structure, frame_interval=frame_interval)
    return frame, plp
