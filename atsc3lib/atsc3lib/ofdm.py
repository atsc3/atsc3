"""Single façade over the A/322 OFDM tables, shared by RX and TX.

Both the receive chain and the synthetic transmitter need the same facts about
an OFDM symbol: its carrier count and origin, which carriers are pilots and
which are data, the pilot reference values, and how a symbol body maps to and
from the frequency domain.  When those facts live in two places they drift.
This module is the one place: the receive helpers (:mod:`atsc3lib.payload`,
:mod:`atsc3lib.preamble`) and the transmitter (:mod:`atsc3lib.transmit`) both
resolve an :class:`OfdmGeometry` here and call the same accessors.

It holds **no new tables**.  Every number comes from the existing modules
(``spec``, ``pilot_tables``, ``preamble``, ``pilot_reference``) and every
routine delegates to the existing function, so the façade cannot disagree with
the validated receive path.  The transmit-side inverses (carrier placement,
IFFT, guard interval) are added here because the receiver never needed them.

Reference: ATSC A/322:2024-04 Sections 7.1 (frame), 7.2 (Preamble), 7.3
(frequency interleaver), 8.1 (pilots), Tables 7.1-7.6, D.1, H.1.1.
"""

from dataclasses import dataclass

import numpy as np

from . import payload as _payload
from . import pilot_reference as _pilot_ref
from . import pilot_tables
from . import preamble as _preamble
from . import spec


@dataclass(frozen=True)
class OfdmGeometry:
    """Resolved geometry of a payload OFDM symbol (A/322 Tables 7.1-7.4).

    Fields cite the table they come from:
        fft: FFT size (8192 / 16384 / 32768).
        gi: guard interval length in samples (A/322 Table 8.9).
        noc: number of carriers NoC (Table 7.1).
        cred: carrier-reduction coefficient (Table 7.1).
        pattern: scattered-pilot pattern name, e.g. ``SP4_2`` (Table 8.2).
        dx, dy: scattered-pilot spacings (A/322 Table 8.2).
    """
    fft: int
    gi: int
    noc: int
    cred: int
    pattern: str
    dx: int
    dy: int

    @classmethod
    def resolve(cls, fft: int, gi: int, cred: int = 0,
                pattern: str = spec.RF33_PATTERN) -> "OfdmGeometry":
        """Build a geometry from an FFT size, guard interval and pattern."""
        if pattern not in spec.ALLOWED_SP[fft]:
            raise ValueError(f"{pattern} is not an allowed pattern for FFT {fft}")
        dx, dy = spec.SP_DXDY[pattern]
        return cls(fft=fft, gi=gi, noc=spec.noc(fft, cred), cred=cred,
                   pattern=pattern, dx=dx, dy=dy)

    @property
    def carrier_shift(self) -> int:
        """FFT bin of relative carrier 0 (A/322 7.1 carrier origin).

        A/322 centres the full ``NoCmax`` carrier set on DC; a cred-reduced
        signal uses the middle ``noc`` carriers.  This is the shift the
        receiver's :func:`atsc3lib.preamble.carrier_shift` uses, reused here so
        TX and RX place carriers identically.
        """
        return _preamble.carrier_shift(self.fft, self.noc)

    def common_cp(self) -> np.ndarray:
        """Common continual pilots as relative carrier indices (A/322 8.1.4)."""
        return _payload.common_cp_relative(self.noc, self.fft)

    def additional_cp(self) -> tuple:
        """Additional continual pilots, relative (A/322 Table D.1.4/D.1.5)."""
        return pilot_tables.additional_cp(self.fft, self.pattern, self.cred)

    def pilot_indices(self, l: int, sbs: bool = False) -> np.ndarray:
        """Relative indices carrying a pilot in data symbol ``l`` (A/322 8.1)."""
        return _payload._pilots(self.noc, self.dx, self.dy, l, sbs,
                                self.common_cp(), self.additional_cp())

    def data_indices(self, l: int, sbs: bool = False) -> np.ndarray:
        """Relative indices carrying data (non-pilot) in symbol ``l``."""
        mask = np.ones(self.noc, dtype=bool)
        mask[self.pilot_indices(l, sbs)] = False
        return np.flatnonzero(mask)

    def pilot_values(self, indices: np.ndarray) -> np.ndarray:
        """Known complex pilot values at ``indices`` (A/322 8.1.2)."""
        idx = np.asarray(indices, dtype=int)
        r = _pilot_ref.reference_sequence(int(idx[-1]) + 1 if len(idx) else 0)
        return (1.0 - 2.0 * r[idx]).astype(np.complex128)

    def avail_data(self) -> int:
        """Available data cells per data symbol (A/322 Tables 7.3/7.4)."""
        return pilot_tables.avail_data(self.fft, self.cred, self.pattern)

    def sbs_total(self) -> int:
        """Total data cells in a subframe boundary symbol (Tables 7.5/7.6)."""
        return pilot_tables.sbs_total(self.fft, self.cred, self.pattern)

    def sbs_active(self, spb: int) -> int:
        """Active data cells in an SBS (Annex F) for a pilot-boost value."""
        return pilot_tables.sbs_active(self.fft, self.cred, spb, self.pattern)

    def spectrum_of(self, carriers: np.ndarray) -> np.ndarray:
        """Place ``noc`` relative carriers into a full ``fft``-bin spectrum.

        The inverse of :meth:`carriers_of`; the transmit half the receiver
        never needed.  Relative carrier ``j`` lands at shifted bin
        ``carrier_shift + j`` (A/322 7.1 carrier origin).
        """
        s = np.zeros(self.fft, dtype=np.complex128)
        s[self.carrier_shift:self.carrier_shift + self.noc] = carriers
        return s

    def carriers_of(self, spectrum: np.ndarray) -> np.ndarray:
        """Relative carriers of a shifted spectrum (A/322 7.1)."""
        return np.asarray(spectrum)[
            self.carrier_shift:self.carrier_shift + self.noc]

    def body_of(self, carriers: np.ndarray) -> np.ndarray:
        """Time-domain symbol body (FFT length) from relative carriers."""
        return np.fft.ifft(np.fft.ifftshift(self.spectrum_of(carriers)))

    def carriers_from_body(self, body: np.ndarray) -> np.ndarray:
        """Relative carriers of one received symbol body (the RX direction)."""
        return _payload._relative_carriers(body, self.fft, self.noc)

    def with_guard(self, body: np.ndarray) -> np.ndarray:
        """Prepend the cyclic-prefix guard interval (A/322 7.1.8)."""
        return np.concatenate([body[-self.gi:], body])


@dataclass(frozen=True)
class PreambleGeometry:
    """Resolved geometry of the first Preamble symbol (A/322 7.2.5/Table 7.2).

    Fields cite the table they come from:
        fft: FFT size from the bootstrap's ``preamble_structure`` (Table H.1.1).
        gi: guard interval, shared with the rest of the frame (Table 8.9).
        dx: Preamble pilot spacing, DY = 1 (A/322 8.1.6.1).
        amplitude: preamble pilot amplitude A_Preamble (Table 8.6).
        noc: carrier count, fixed at cred_coeff 4 for the first symbol.
    """
    fft: int
    gi: int
    dx: int
    amplitude: float
    noc: int

    @classmethod
    def first(cls, preamble_structure: int) -> "PreambleGeometry":
        """Geometry of the first Preamble symbol for a structure value."""
        p = spec.PREAMBLE_STRUCTURE[preamble_structure]
        return cls(fft=p.fft, gi=p.gi, dx=p.dx,
                   amplitude=spec.PREAMBLE_PILOT_AMPLITUDE[(p.fft, p.gi)],
                   noc=spec.noc(p.fft, spec.PREAMBLE_FIRST_CRED))

    def pilot_indices(self) -> np.ndarray:
        """Relative indices k in [0, noc) with ``k mod DX == 0`` (8.1.6.1)."""
        return _preamble.preamble_pilot_indices(self.noc, self.dx)

    def pilot_values(self) -> np.ndarray:
        """Known preamble pilot values (A/322 8.1.6.3)."""
        return _preamble.preamble_pilot_values(self.noc, self.dx,
                                               self.amplitude)

    def data_mask(self) -> np.ndarray:
        """Boolean mask of data cells (not preamble pilots or common CP)."""
        return _preamble.preamble_data_mask(self.noc, self.dx)

    def data_cells(self) -> int:
        """Number of data cells in the first Preamble symbol (Table 7.2)."""
        return int(self.data_mask().sum())
