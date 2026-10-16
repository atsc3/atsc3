"""Per-stage timing of the data-PLP decode chain (A1/A2/B1-B4).

Times the receive chain on the RF33 subframe-1 fixture (16K / SP4_4, 256QAM
11/15, 117 FEC blocks) and prints the split the performance work targets:
pool build, demap, LDPC, BCH and the whole-frame wall time at several FEC
thread counts.  Timing only -- no assertions -- so it stays out of the test
suite (see ``meta/TODO-ldpc-demod-enhancements.md`` Track B5).

Example::

    .venv/bin/python -m tools.bench_decode
    .venv/bin/python -m tools.bench_decode --workers 1,2,4,8
"""

import argparse
import json
import logging
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from atsc3lib import nuc, payload, twisted_block  # noqa: E402
from atsc3lib.payload import (  # noqa: E402
    build_data_symbol_pool, DataPlpChain, decode_plp_from_pool,
)
from atsc3lib.ldpc_exact import NINNER_NORMAL, RATE_MIN  # noqa: E402

LOGGER = logging.getLogger('bench_decode')

_HERE = os.path.dirname(__file__)
_DEFAULT_FIXTURE = os.path.join(_HERE, '..', 'tests', 'data', 'rf33_sf1_y.npy')
_DEFAULT_META = os.path.join(_HERE, '..', 'tests', 'data', 'rf33_sf1_meta.json')


def _plp_namespace(p):
    """A PLP config object from the fixture meta (mirrors test_subframe1)."""
    import types
    return types.SimpleNamespace(
        plp_id=p['plp_id'], start=p['start'], size=p['size'],
        modulation=p['mod'], code_rate=p['code_rate'], ti_mode=p['ti_mode'],
        hti_num_ti_blocks=p['nti'] - 1, hti_cell_interleaver=0,
        fec_type=p['fec_type'])


def _time(fn, repeats=1):
    best = float('inf')
    for _ in range(repeats):
        t = time.perf_counter()
        out = fn()
        best = min(best, time.perf_counter() - t)
    return out, best


def stage_split(chain, cells, repeats=3):
    """Return {stage: seconds-per-FEC-block} for demap, LDPC and BCH."""
    c = np.asarray(cells, dtype=np.complex128)
    power = float(np.mean(np.abs(c) ** 2))
    if power > 0.0:
        c = c / np.sqrt(power)

    _, t_demap = _time(lambda: nuc.demap_llr(c, chain.mod_bits, chain.rate),
                       repeats)
    q = nuc.demap_llr(c, chain.mod_bits, chain.rate)
    llr = np.empty(chain.ninner)
    llr[chain.order] = -q
    _, t_ldpc = _time(lambda: chain.ldpc.decode(
        llr, max_iterations=chain.ldpc.max_iterations, alpha=1.0), repeats)
    bits, _ = chain.ldpc.decode(llr, max_iterations=chain.ldpc.max_iterations,
                                alpha=1.0)
    _, t_bch = _time(lambda: chain.bch.decode(bits[:chain.Kldpc]), repeats)
    return {'demap': t_demap, 'ldpc': t_ldpc, 'bch': t_bch}


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fixture', default=_DEFAULT_FIXTURE,
                    help='subframe-1 IQ .npy fixture')
    ap.add_argument('--meta', default=_DEFAULT_META,
                    help='fixture geometry .json')
    ap.add_argument('--workers', default='1,2,4,8',
                    help='comma-separated FEC thread counts to sweep')
    ap.add_argument('--repeats', type=int, default=3,
                    help='repeats per per-stage measurement (best-of)')
    ap.add_argument('-v', '--verbose', action='store_true')
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose
                        else logging.INFO, format='%(levelname)s %(message)s')

    if not os.path.exists(args.fixture):
        LOGGER.error('fixture not found: %s', args.fixture)
        return 1
    y = np.load(args.fixture).astype(np.complex128)
    m = json.load(open(args.meta))
    p = m['plp1']

    LOGGER.info('pool: %d symbols, %s, dense channel estimate',
                m['n_data_symbols'], m['pattern'])
    pool, t_pool = _time(lambda: build_data_symbol_pool(
        y, 0, m['fft'], m['gi'], m['noc'], m['dx'], m['dy'],
        m['n_data_symbols'], sbs_symbols=tuple(m['sbs_symbols']),
        pattern=m['pattern'], sbs_null=m['sbs_null'], cred_coeff=m['cred'],
        fi_offset=m['fi_offset'], dense=True))
    LOGGER.info('pool build: %.3f s (%d cells)', t_pool, len(pool.cells))

    plp = _plp_namespace(p)
    chain = DataPlpChain(mod=payload.MOD_NAME[p['mod']],
                         rate=RATE_MIN + p['code_rate'], ninner=NINNER_NORMAL)
    cpf = chain.cells_per_fec
    n_fec = p['size'] // cpf
    per_ti = p['size'] // p['nti']
    ncols = n_fec // p['nti']
    seg = pool.cells[p['start']:p['start'] + p['size']][:per_ti]
    first = twisted_block.fec_block(seg, 0, cpf, ncols)
    split = stage_split(chain, first, repeats=args.repeats)

    print('\n== per-stage split (1 FEC block, best of %d) ==' % args.repeats)
    for name, t in split.items():
        print(f'  {name:5s} {t * 1e3:8.2f} ms/block   x{n_fec} = '
              f'{t * n_fec:7.2f} s serial')
    serial = sum(split.values()) * n_fec
    print(f'  FEC serial total (demap+ldpc+bch): {serial:.2f} s')

    print('\n== whole-frame decode ==')
    print(f'  pool build           {t_pool:8.3f} s')
    for w in [int(x) for x in args.workers.split(',') if x.strip()]:
        os.environ['ATSC3LIB_FEC_THREADS'] = str(w)
        _, t = _time(lambda: decode_plp_from_pool(pool, plp,
                                                  max_iterations=100))
        print(f'  decode @ {w:2d} worker(s) {t:8.3f} s')
    del os.environ['ATSC3LIB_FEC_THREADS']
    return 0


if __name__ == '__main__':
    sys.exit(main())
