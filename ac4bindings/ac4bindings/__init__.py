"""ac4bindings: compiled AC-4 (ETSI TS 103 190) decoder kernels.

The Python AC-4 decoder is far too slow for live use (the A-SPX QMF and the
spectral decode dominate), so the codec lives here as a C extension.  The
implementation is written from ETSI TS 103 190-2 V1.2.1 (the part that governs
ATSC 3.0, whose streams are ``bitstream_version`` 2) and is gated against the
independent reference receiver only as a referee.

Current coverage (each rung gated against the reference receiver as referee):

  * ``parse_toc``   -- ``ac4_toc()`` (Part-2 clause 6.2.1.1), byte-exact on
                       every RF33 audio frame.
  * ``substream``   -- substream location (TS 103 190-1 4.3.3.2.11).
  * ``huff_decode`` / ``build_huff_tree`` -- Annex A Huffman decoding.
  * ``spectral``    -- ``asf_spectral_data`` (4.2.8.4), the decode hot path.
  * ``framing``     -- ``sf_info`` / ``sf_info_lfe`` (4.2.7.1/4.2.7.2).
  * ``sf``          -- ``sf_data`` (4.2.7.3) over all window groups: sections,
                       spectral, scale factors (Pseudocode 21) and noise fill
                       (Pseudocode 23).
  * ``element``     -- the 5.X channel element's ASF core (4.2.6.6).
  * ``dequant``     -- quantization reconstruction and scaling (5.1.3.2).
  * ``kbd_window`` / ``imdct`` -- the KBD window (5.5.3) and the inverse MDCT
                       (5.5.2), gated by perfect reconstruction.
  * ``ungroup``     -- bitstream order to per-window spectra (Pseudocode 25).
  * ``synthesise``  -- the block-switching filterbank and overlap-add
                       (Pseudocode 63-64), gated by ``S @ S.T == I``.
  * ``aspx``        -- A-SPX control data: the subband-group skeleton
                       (Pseudocodes 67-72), the payload parse (Tables 51-58)
                       and envelope reconstruction (Pseudocodes 80-84).
  * ``aspx_qmf``    -- 64-band complex QMF analysis/synthesis (5.7.3/5.7.4),
                       with the compiled ``qmf_analyse``/``qmf_synthesise``
                       kernels; gated by perfect reconstruction.
  * ``aspx_hf``     -- the HF generator and envelope adjuster (5.7.6.4),
                       gated by the spec's closed loop.
  * ``core``        -- the core packed spectrum, M/S unmix and ungroup
                       (5.1.3, Table 113, Pseudocode 25).
  * ``aspx_render`` -- the end-to-end core + QMF + HF render over all 5.X
                       channels, gated by the high band appearing, stopping
                       at 21 kHz and leaving each core band intact.
  * ``decode``      -- the whole-track entry point (``decode_track``): a
                       captured AC-4 asset to per-channel PCM, packaging the
                       frame walk for ``atsc3lib`` and the tests.

The ASF core closes: a 5.X element's channels decode to PCM through
dequant -> ungroup -> synthesise.  A-SPX extends that spectrum above the
coded bandwidth, and :mod:`aspx_render` joins the two into a full-band 5.1
render.  Still to land: PCM assembly into ``atsc3lib.media`` and A-CPL (the
RF33 stream is discrete 5.1, so no upmix is needed to render it).
"""

from . import _ac4
from . import tables
from . import huffman
from . import aspx
from . import aspx_tables
from . import aspx_qmf
from . import aspx_hf

__all__ = ["parse_toc", "substream", "huff_decode", "build_huff_tree",
           "spectral", "framing", "sf", "element", "available", "tables"]

__version__ = "0.1.0"

#: The C kernel's SFB row width; must match AC4_SF_MAX_SFB in _ac4.c.
_SF_MAX_SFB = 64
_SF_MAX_GROUPS = 16
_SF_MAX_SECTS = 64
#: Serialised framing offsets; must match AC4_FR_* in _ac4.c.
_FR_W2G_OFF = 9
_FR_NWIN_OFF = _FR_W2G_OFF + 16
_FR_SIZE = _FR_NWIN_OFF + _SF_MAX_GROUPS


def framing(data, offset=0, b_lfe=False):
    """Parse ``sf_info()`` / ``sf_info_lfe()`` (TS 103 190-1 4.2.7.1/4.2.7.2).

    Returns ``(packed int32 array, new_bit_position)``.  The packed array is
    the framing the C :func:`sf` kernel consumes; use :func:`unpack_framing`.
    """
    return _ac4.framing(bytes(data), int(offset), 1 if b_lfe else 0)


def unpack_framing(packed):
    """Turn a packed framing array into a readable dict."""
    import numpy as np
    p = np.asarray(packed)
    nw = int(p[5])
    ng = int(p[6])
    return {
        "b_long": int(p[0]),
        "tl": (int(p[1]), int(p[2])),
        "different": int(p[3]),
        "n_half": int(p[4]),
        "num_windows": nw,
        "num_groups": ng,
        "max_sfb": (int(p[7]), int(p[8])),
        "w2g": p[_FR_W2G_OFF:_FR_W2G_OFF + nw].tolist(),
        "nwin": p[_FR_NWIN_OFF:_FR_NWIN_OFF + ng].tolist(),
    }


def available() -> bool:
    """True when the compiled extension is importable."""
    return _ac4 is not None


def parse_toc(frame):
    """Parse ``ac4_toc()`` (TS 103 190-2 6.2.1.1) from a raw AC-4 frame.

    Args:
        frame: the raw AC-4 frame bytes (one ``raw_ac4_frame``, no sync header).

    Returns:
        A dict of TOC fields, including ``substream_sizes`` (``int32`` NumPy
        array) and ``toc_bytes``.  Raises ``ValueError`` on a bitstream_version
        this kernel does not handle or a malformed TOC.
    """
    return _ac4.parse_toc(bytes(frame))


def substream(frame, index):
    """The bytes of substream ``index`` in a raw AC-4 frame.

    The offset follows TS 103 190-1 4.3.3.2.11: substream data starts after the
    byte-aligned ``ac4_toc``, at ``payload_base`` plus the sizes of the
    preceding substreams.
    """
    return _ac4.substream(bytes(frame), int(index))


def build_huff_tree(lens, words):
    """Flatten a codebook into a binary prefix tree for the C decoder.

    Returns ``(left, right, sym, root)`` as ``int32`` arrays: internal nodes
    index into ``left``/``right`` (-1 = no child), leaves carry ``sym >= 0``.
    A tree reproduces ANY prefix code exactly; the AC-4 books are not
    canonical, so a tree is the safe representation.
    """
    left = []
    right = []
    sym = []

    def new_node():
        left.append(-1)
        right.append(-1)
        sym.append(-1)
        return len(sym) - 1

    root = new_node()
    for i, (length, word) in enumerate(zip(lens, words)):
        length = int(length)
        if length <= 0:
            continue
        node = root
        for bit in range(length - 1, -1, -1):
            branch = (int(word) >> bit) & 1
            child = right[node] if branch else left[node]
            if child == -1:
                child = new_node()
                if branch:
                    right[node] = child
                else:
                    left[node] = child
            node = child
        sym[node] = i
    import numpy as np
    return (np.array(left, dtype=np.int32),
            np.array(right, dtype=np.int32),
            np.array(sym, dtype=np.int32),
            np.int32(root))


def huff_decode(data, lens, words, offset=0):
    """Decode one Huffman codeword (TS 103 190-1 4.2.8.5).

    Args:
        data: the bitstream bytes.
        lens, words: the codebook's length and codeword arrays (Annex A).
        offset: bit offset to start reading at.

    Returns:
        ``(symbol, bits_consumed)``.
    """
    return _ac4.huff_decode(bytes(data), lens, words, int(offset))


def spectral(data, sects, offsets, trees, offset=0):
    """Decode one window group's ``asf_spectral_data`` (TS 103 190-1 4.2.8.4).

    Args:
        data: bitstream bytes.
        sects: ``(cb, sfb_start, sfb_end)`` triples.
        offsets: ``sect_sfb_offset`` for the group.
        trees: ``{cb: (left, right, sym, root)}`` from :func:`build_huff_tree`.
        offset: starting bit position.

    Returns:
        ``(lines int32 array, new_bit_position)``.
    """
    import numpy as np
    left = []; right = []; sym = []; roots = [-1] * 12
    # Concatenate the per-codebook trees, recording each root offset.  Child
    # indices are local to a codebook's tree, so each is rebased by the node
    # block it lands in; a missing rebase would walk into another codebook.
    # ``roots`` is indexed by codebook number, so it has 12 slots (0 unused).
    node_base = 0
    for cb in range(1, 12):
        l, r, s, root = trees[cb]
        roots[cb] = node_base + int(root)
        for child in l.tolist():
            left.append(-1 if child < 0 else node_base + child)
        for child in r.tolist():
            right.append(-1 if child < 0 else node_base + child)
        sym.extend(s.tolist())
        node_base += len(s)
    left = np.array(left, dtype=np.int32)
    right = np.array(right, dtype=np.int32)
    sym = np.array(sym, dtype=np.int32)
    roots = np.array(roots, dtype=np.int32)
    dims = np.zeros(12, dtype=np.int32)
    mods = np.zeros(12, dtype=np.int32)
    offs_cb = np.zeros(12, dtype=np.int32)
    unsigned = np.zeros(12, dtype=np.int32)
    for cb in range(1, 12):
        dims[cb] = tables.CB_DIM[cb]
        mods[cb] = tables.CB_MOD[cb]
        offs_cb[cb] = tables.CB_OFF[cb]
        unsigned[cb] = 1 if tables.UNSIGNED_CB[cb] else 0
    sects_arr = np.asarray(sects, dtype=np.int32).reshape(-1)
    offs_arr = np.asarray(offsets, dtype=np.int32)
    return _ac4.spectral(bytes(data), sects_arr, offs_arr, left, right, sym,
                         roots, dims, mods, offs_cb, unsigned, int(offset))


def _sfb_rows():
    """Pack the Annex B band offsets into the C kernel's fixed-width rows.

    Rows are ordered by :data:`tables.NUM_SFB`'s insertion order, with a
    trailing -1 beyond each row's length.  The return value is cached: the
    tables are static.
    """
    import numpy as np
    cache = getattr(_sfb_rows, "_cache", None)
    if cache is not None:
        return cache
    lengths = list(tables.NUM_SFB)
    rows = np.full((len(lengths), _SF_MAX_SFB), -1, dtype=np.int32)
    for i, length in enumerate(lengths):
        off = tables.SFB_OFFSET[length]
        rows[i, :len(off)] = off
    lens = np.array(lengths, dtype=np.int32)
    cache = (rows, lens)
    _sfb_rows._cache = cache
    return cache


def _spectral_tables(trees):
    """Flatten ``{cb: tree}`` into the concatenated arrays the kernel wants."""
    import numpy as np
    left = []; right = []; sym = []; roots = [-1] * 12
    node_base = 0
    for cb in range(1, 12):
        l, r, s, root = trees[cb]
        roots[cb] = node_base + int(root)
        for child in l.tolist():
            left.append(-1 if child < 0 else node_base + child)
        for child in r.tolist():
            right.append(-1 if child < 0 else node_base + child)
        sym.extend(s.tolist())
        node_base += len(s)
    return (np.array(left, dtype=np.int32), np.array(right, dtype=np.int32),
            np.array(sym, dtype=np.int32), np.array(roots, dtype=np.int32))


def _codebook_arrays():
    import numpy as np
    dims = np.zeros(12, dtype=np.int32)
    mods = np.zeros(12, dtype=np.int32)
    offs = np.zeros(12, dtype=np.int32)
    unsigned = np.zeros(12, dtype=np.int32)
    for cb in range(1, 12):
        dims[cb] = tables.CB_DIM[cb]
        mods[cb] = tables.CB_MOD[cb]
        offs[cb] = tables.CB_OFF[cb]
        unsigned[cb] = 1 if tables.UNSIGNED_CB[cb] else 0
    return dims, mods, offs, unsigned


def sf(data, framing, trees, sf_tree, snf_tree, offset=0):
    """Decode one channel's ``sf_data`` (TS 103 190-1 4.2.7.3).

    Runs the scale-factor data block over every window group: the section data
    (4.2.8.3), the spectral data (4.2.8.4), the scale factors (Pseudocode 21)
    and the spectral noise fill (Pseudocode 23).  The ``sf_info()`` framing is
    parsed separately (it precedes ``sf_data`` in the element) and passed in.

    Args:
        data: bitstream bytes.
        framing: the packed framing from :func:`framing`.
        trees: ``{cb: (left, right, sym, root)}`` spectrum codebooks.
        sf_tree, snf_tree: scale-factor and SNF codebook trees.
        offset: starting bit position.

    Returns:
        A dict with ``lines``, ``sects``, ``sfb_cb``, ``sfs`` (``groups *
        AC4_SF_MAX_SFB``, ``INT32_MIN`` where no factor was sent), ``snf``,
        ``offsets``, ``max_sfb``, ``groups``, ``has_snf``, ``ref`` and
        ``bitpos``.
    """
    import numpy as np
    sp = _spectral_tables(trees)
    dims, mods, offs, unsigned = _codebook_arrays()
    sp_args = (sp[0], sp[1], sp[2], sp[3], dims, mods, offs, unsigned)
    rows, lens = _sfb_rows()
    sf_args = (sf_tree[0], sf_tree[1], sf_tree[2],
               np.array([int(sf_tree[3])], dtype=np.int32))
    snf_args = (snf_tree[0], snf_tree[1], snf_tree[2],
                np.array([int(snf_tree[3])], dtype=np.int32))
    return _ac4.sf(bytes(data), rows, lens, list(sp_args),
                   list(sf_args), list(snf_args),
                   np.asarray(framing, dtype=np.int32), int(offset))


def _codebook_args(trees, sf_tree, snf_tree):
    import numpy as np
    sp = _spectral_tables(trees)
    dims, mods, offs, unsigned = _codebook_arrays()
    sp_args = (sp[0], sp[1], sp[2], sp[3], dims, mods, offs, unsigned)
    def root_of(t):
        return np.array([int(t[3])], dtype=np.int32)
    sf_args = (sf_tree[0], sf_tree[1], sf_tree[2], root_of(sf_tree))
    snf_args = (snf_tree[0], snf_tree[1], snf_tree[2], root_of(snf_tree))
    return sp_args, sf_args, snf_args


def kbd_window(n, alpha):
    """KBD window (TS 103 190-1 clause 5.5.3) of length ``2N`` as float64."""
    return _ac4.kbd(int(n), float(alpha))


def kbd_alpha(length):
    """The Table 185 alpha for a 48 kHz transform length."""
    if length >= 1536:
        return 3.0
    if length >= 768:
        return 4.0
    if length >= 384:
        return 4.5
    if length >= 192:
        return 5.0
    return 6.0


def imdct(X, window):
    """Inverse MDCT (TS 103 190-1 clause 5.5.2) of ``N`` lines with a ``2N`` window."""
    import numpy as np
    return _ac4.imdct(np.asarray(X, dtype=np.float32), window)


def ungroup(lines, offsets_all, max_sfb, framing, n_full):
    """Ungroup the packed lines into per-window spectra (Pseudocode 25)."""
    import numpy as np
    rows, lens = _sfb_rows()
    return _ac4.ungroup(np.asarray(lines, dtype=np.int32), rows, lens,
                        np.asarray(offsets_all, dtype=np.int32),
                        np.asarray(max_sfb, dtype=np.int32),
                        np.asarray(framing, dtype=np.int32), int(n_full))


def synthesise(spectra, lengths, n_full, overlap=None, n_prev=0, mode=1):
    """The filterbank's window/overlap-add (TS 103 190-1 5.5.2, step 5-6).

    Args:
        spectra: ``(nblocks, n_full)`` float32, one row per transform window.
        lengths: the per-block transform lengths.
        n_full: the full frame length.
        overlap: the persistent overlap state (``n_full`` float64), or None.
        n_prev: the previous block length.
        mode: 0 = the literal step-6 reading, 1 = the time-reversed reading
            (which time-domain aliasing cancellation requires).

    Returns:
        ``(pcm float64, n_prev)``; the caller keeps the updated ``overlap``.
    """
    import numpy as np
    if overlap is None:
        overlap = np.zeros(int(n_full), dtype=np.float64)
        n_prev = int(lengths[0]) if len(lengths) else int(n_full)
    return _ac4.synthesise(np.asarray(spectra, dtype=np.float32),
                           np.asarray(lengths, dtype=np.int32),
                           int(n_full), int(n_prev), overlap, int(mode))


def qmf_analyse(pcm, filt, qwin=None):
    """64-band complex QMF analysis (TS 103 190-1 Pseudocode 65).

    Args:
        pcm: ``nts * 64`` real samples (float64).
        filt: the 640-tap filter state; updated in place.
        qwin: the 640-tap prototype; the committed table by default.

    Returns:
        ``(64, nts)`` complex128 subband matrix.
    """
    import numpy as np
    from . import aspx_tables
    if qwin is None:
        qwin = np.asarray(aspx_tables.QWIN, dtype=np.float64)
    return _ac4.qmf_analyse(np.asarray(pcm, dtype=np.float64),
                            np.asarray(filt, dtype=np.float64),
                            np.asarray(qwin, dtype=np.float64))


def qmf_synthesise(q, filt, qwin=None):
    """64-band complex QMF synthesis (TS 103 190-1 Pseudocode 66).

    Args:
        q: ``(64, nts)`` complex subband matrix.
        filt: the 1280-tap synthesis state; updated in place.
        qwin: the 640-tap prototype; the committed table by default.

    Returns:
        ``nts * 64`` real samples (float64).
    """
    import numpy as np
    from . import aspx_tables
    if qwin is None:
        qwin = np.asarray(aspx_tables.QWIN, dtype=np.float64)
    return _ac4.qmf_synthesise(np.asarray(q, dtype=np.complex128),
                               np.asarray(filt, dtype=np.float64),
                               np.asarray(qwin, dtype=np.float64))


def dequant(lines, offsets_all, n_off_g, sfs, max_sfb, out_stride):
    """Reconstruct and scale quantized lines (TS 103 190-1 clause 5.1.3.2).

    Args:
        lines: the shared quantized spectral lines (``int32``).
        offsets_all, n_off_g, sfs, max_sfb: the per-group arrays from
            :func:`sf`.
        out_stride: number of lines per group in the output.

    Returns:
        A ``(groups, out_stride)`` ``float32`` array of scaled spectral lines.
    """
    import numpy as np
    return _ac4.dequant(np.asarray(lines, dtype=np.int32),
                        np.asarray(offsets_all, dtype=np.int32),
                        np.asarray(n_off_g, dtype=np.int32),
                        np.asarray(sfs, dtype=np.int32),
                        np.asarray(max_sfb, dtype=np.int32),
                        int(out_stride))


def element(data, trees, sf_tree, snf_tree, b_iframe=False, is_pair=False):
    """Decode a 5.X channel element's ASF core (TS 103 190-1 4.2.6.6).

    Args:
        data: the channel element's bytes (a substream's payload).
        trees: ``{cb: (left, right, sym, root)}`` spectrum codebooks.
        sf_tree, snf_tree: scale-factor and SNF codebook trees.
        b_iframe: the ``b_iframe_global`` flag from the TOC.
        is_pair: decode a ``channel_pair_element`` (TS 103 190-1 Table 22)
            instead of the 5_X element; returns ``L``/``R`` only.

    Returns:
        A dict with ``codec_mode``, ``coding_config``, ``channels`` (a dict of
        the present SF data blocks keyed ``lfe``/``L``/``R``/``Ls``/``Rs``/
        ``C``), ``aspx`` (the A-SPX config for an I-frame, else None) and
        ``bitpos``.
    """
    sp_args, sf_args, snf_args = _codebook_args(trees, sf_tree, snf_tree)
    rows, lens = _sfb_rows()
    return _ac4.element(bytes(data), rows, lens, list(sp_args),
                        list(sf_args), list(snf_args),
                        1 if b_iframe else 0, 1 if is_pair else 0)


from . import core
from . import aspx_render
from . import decode
