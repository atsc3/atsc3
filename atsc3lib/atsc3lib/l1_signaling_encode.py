"""ATSC 3.0 L1 signalling serialisers (the transmit inverse of the parsers).

:mod:`atsc3lib.l1_signaling` parses the 200-bit L1-Basic (A/322 Table 9.2) and
the variable L1-Detail (Table 9.8) into named fields; nothing wrote them back.
This module is that inverse: :func:`l1_basic_to_bits` and
:func:`l1_detail_to_bits` reproduce the exact info-bit block (including the
32-bit CRC) from a parsed :class:`~atsc3lib.l1_signaling.L1Basic` /
:class:`~atsc3lib.l1_signaling.L1Detail`.

The field order and widths are taken from the parser itself (same clauses, same
order) so the two cannot drift; branch-dependent fields that have no dedicated
dataclass field (the ``frame_length_mode = 0`` pair, MIMO, channel bonding,
sub-slices, the ``time_info_flag`` time sub-fields) are kept in ``raw`` by the
parser and read back here.  The transmit gate is a full
parse -> serialise -> FEC-encode -> FEC-decode -> parse round trip on the real
RF33 fixtures.

Reference: ATSC A/322:2024-04, Tables 9.2 and 9.8; CRC per 6.1.2.2.
"""

from typing import Dict, List

import numpy as np

from .crc import crc32
from .l1_signaling import L1Basic, L1Detail, PLPConfig

#: Fields the L1-Detail parser reads that have a dedicated dataclass field, so
#: they are taken from the attribute rather than from ``raw``.
_RAW_FALLBACK = 0


class BitWriter:
    """MSB-first fixed-width bit accumulator (A/322 bit ordering)."""

    def __init__(self) -> None:
        self._bits: List[int] = []

    def write(self, value: int, width: int) -> "BitWriter":
        """Append ``width`` bits of ``value``, most-significant bit first."""
        value = int(value)
        if width < 0 or value < 0 or value >= (1 << width):
            raise ValueError(f"value {value} does not fit in {width} bits")
        self._bits.extend((value >> (width - 1 - i)) & 1 for i in range(width))
        return self

    def write_bits(self, bits) -> "BitWriter":
        """Append an iterable of 0/1 values verbatim."""
        self._bits.extend(int(b) & 1 for b in np.asarray(bits).ravel())
        return self

    def pad_to(self, target: int, fill: int = 0) -> "BitWriter":
        """Extend to ``target`` bits with ``fill`` (default zero)."""
        if len(self._bits) > target:
            raise ValueError(f"{len(self._bits)} bits already exceeds {target}")
        self._bits.extend([int(fill) & 1] * (target - len(self._bits)))
        return self

    def __len__(self) -> int:
        return len(self._bits)

    def bits(self) -> np.ndarray:
        """The accumulated bits as a ``uint8`` array."""
        return np.asarray(self._bits, dtype=np.uint8)

    def crc32_bits(self) -> "BitWriter":
        """Append the A/322 6.1.2.2 32-bit CRC of the bits so far."""
        c = crc32(self._bits)
        return self.write(c, 32)


def _raw(obj, name: str, default: int = _RAW_FALLBACK) -> int:
    """Read a branch-specific field the parser kept in ``obj.raw``."""
    value = obj.raw.get(name)
    return default if value is None else value


def l1_basic_to_bits(lb: L1Basic) -> np.ndarray:
    """Serialise an :class:`L1Basic` to its 200 information bits (Table 9.2)."""
    w = BitWriter()
    w.write(lb.version, 3)
    w.write(lb.mimo_scattered_pilot_encoding, 1)
    w.write(lb.lls_flag, 1)
    w.write(lb.time_info_flag, 2)
    w.write(lb.return_channel_flag, 1)
    w.write(lb.papr_reduction, 2)
    w.write(lb.frame_length_mode, 1)
    if lb.frame_length_mode == 0:
        w.write(_raw(lb, 'L1B_frame_length'), 10)
        w.write(_raw(lb, 'L1B_excess_samples_per_symbol'), 13)
    else:
        w.write(_raw(lb, 'L1B_time_offset', lb.time_offset), 16)
        w.write(_raw(lb, 'L1B_additional_samples'), 7)
    w.write(lb.num_subframes, 8)
    w.write(lb.preamble_num_symbols, 3)
    w.write(lb.preamble_reduced_carriers, 3)
    w.write(lb.l1_detail_content_tag, 2)
    w.write(lb.l1_detail_size_bytes, 13)
    w.write(lb.l1_detail_fec_type, 3)
    w.write(lb.l1_detail_additional_parity_mode, 2)
    w.write(lb.l1_detail_total_cells, 19)
    w.write(lb.first_sub_mimo, 1)
    w.write(lb.first_sub_miso, 2)
    w.write(lb.first_sub_fft_size, 2)
    w.write(lb.first_sub_reduced_carriers, 3)
    w.write(lb.first_sub_guard_interval, 4)
    w.write(lb.first_sub_num_ofdm_symbols, 11)
    w.write(lb.first_sub_scattered_pilot_pattern, 5)
    w.write(lb.first_sub_scattered_pilot_boost, 3)
    w.write(lb.first_sub_sbs_first, 1)
    w.write(lb.first_sub_sbs_last, 1)
    reserved = _raw(lb, 'L1B_reserved', (1 << 48) - 1)
    w.write(reserved, 48)
    w.crc32_bits()
    return w.bits()


def _plp_to_bits(w: BitWriter, plp: PLPConfig, n_rf: int,
                 first_sub_mimo: int, l1d_mimo: int,
                 subframe_index: int) -> None:
    """Serialise one L1D_plp entry (Table 9.8); mirror of ``_parse_plp``."""
    w.write(plp.plp_id, 6)
    w.write(plp.lls_flag, 1)
    w.write(plp.layer, 2)
    w.write(plp.start, 24)
    w.write(plp.size, 24)
    w.write(plp.scrambler_type, 2)
    w.write(plp.fec_type, 4)
    if plp.fec_type in (0, 1, 2, 3, 4, 5):
        w.write(plp.modulation or 0, 4)
        w.write(plp.code_rate or 0, 4)
    w.write(plp.ti_mode, 2)
    if plp.ti_mode == 0:
        w.write(plp.ti_fec_block_start or 0, 15)
    elif plp.ti_mode == 1:
        w.write(plp.ti_fec_block_start or 0, 22)
    if n_rf > 0:
        n_bonded = _raw(plp, 'L1D_plp_num_channel_bonded')
        w.write(n_bonded, 3)
        if n_bonded > 0:
            w.write(_raw(plp, 'L1D_plp_channel_bonding_format'), 2)
            for rf_id in plp.raw.get('L1D_plp_bonded_rf_id', []):
                w.write(rf_id, 3)
    if (subframe_index == 0 and first_sub_mimo == 1) or \
            (subframe_index > 0 and l1d_mimo == 1):
        w.write(_raw(plp, 'L1D_plp_mimo_stream_combining'), 1)
        w.write(_raw(plp, 'L1D_plp_mimo_IQ_interleaving'), 1)
        w.write(_raw(plp, 'L1D_plp_mimo_PH'), 1)
    if plp.layer == 0:
        plp_type = _raw(plp, 'L1D_plp_type')
        w.write(plp_type, 1)
        if plp_type == 1:
            w.write(_raw(plp, 'L1D_plp_num_subslices'), 14)
            w.write(_raw(plp, 'L1D_plp_subslice_interval'), 24)
        if plp.ti_mode in (1, 2) and (plp.modulation == 0):
            w.write(plp.ti_extended_interleaving or 0, 1)
        if plp.ti_mode == 1:
            w.write(plp.cti_depth or 0, 3)
            w.write(plp.cti_start_row or 0, 11)
        elif plp.ti_mode == 2:
            hti_inter = plp.hti_inter_subframe or 0
            w.write(hti_inter, 1)
            hti_ti = plp.hti_num_ti_blocks or 0
            w.write(hti_ti, 4)
            w.write(_raw(plp, 'L1D_plp_HTI_num_fec_blocks_max'), 12)
            if hti_inter == 0:
                w.write(plp.hti_num_fec_blocks or 0, 12)
            else:
                for fec in plp.raw.get('L1D_plp_HTI_num_fec_blocks', []):
                    w.write(fec, 12)
            w.write(plp.hti_cell_interleaver or 0, 1)
    else:
        w.write(plp.ldm_injection_level or 0, 5)


def l1_detail_to_bits(ld: L1Detail, l1b: L1Basic) -> np.ndarray:
    """Serialise an :class:`L1Detail` to its info bits (Table 9.8).

    The block length is fixed by ``l1b.l1_detail_size_bytes * 8``; the variable
    reserved segment is filled from ``ld.reserved_all_ones``.
    """
    w = BitWriter()
    w.write(ld.version, 4)
    w.write(ld.num_rf, 3)
    bonded = ld.raw.get('L1D_bonded_bsid', [])
    for i in range(ld.num_rf):
        w.write(bonded[i] if i < len(bonded) else 0, 16)
        w.write(0, 3)
    if l1b.time_info_flag != 0:
        w.write(_raw(ld, 'L1D_time_sec', ld.time_sec or 0), 32)
        w.write(_raw(ld, 'L1D_time_msec'), 10)
        if l1b.time_info_flag != 1:
            w.write(_raw(ld, 'L1D_time_usec'), 10)
            if l1b.time_info_flag != 2:
                w.write(_raw(ld, 'L1D_time_nsec'), 10)
    for i, sf in enumerate(ld.subframes):
        if i > 0:
            w.write(sf['mimo'], 1)
            w.write(sf['miso'], 2)
            w.write(sf['fft_size'], 2)
            w.write(sf['reduced_carriers'], 3)
            w.write(sf['guard_interval'], 4)
            w.write(sf['num_ofdm_symbols'], 11)
            w.write(sf['scattered_pilot_pattern'], 5)
            w.write(sf['scattered_pilot_boost'], 3)
            w.write(sf['sbs_first'], 1)
            w.write(sf['sbs_last'], 1)
        if l1b.num_subframes > 0:
            w.write(sf['subframe_multiplex'], 1)
        w.write(sf['frequency_interleaver'], 1)
        has_sbs = ((i == 0 and (l1b.first_sub_sbs_first
                                or l1b.first_sub_sbs_last))
                   or (i > 0 and (sf.get('sbs_first') or sf.get('sbs_last'))))
        if has_sbs:
            w.write(sf['sbs_null_cells'], 13)
        plps = sf['plps']
        w.write(len(plps) - 1, 6)
        for plp in plps:
            _plp_to_bits(w, plp, ld.num_rf, l1b.first_sub_mimo,
                         sf.get('mimo', 0), i)
    w.write(ld.bsid, 16)
    fill = 1 if ld.reserved_all_ones else 0
    w.pad_to(l1b.l1_detail_size_bytes * 8 - 32, fill=fill)
    w.crc32_bits()
    return w.bits()
