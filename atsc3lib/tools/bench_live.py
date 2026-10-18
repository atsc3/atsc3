"""Sustained-frame benchmark for the accelerated receive chain.

Measures the three numbers that decide whether true-live is reachable on this
box, on the RF33 subframe-1 PLP-1 fixture (256QAM 11/15, 117 FEC blocks):

* pool build (CPU: FFT + dense channel + cell extraction)
* FEC decode (demap + LDPC + BCH) with the selected backend
* sustained frame time with the pool build overlapped against the previous
  frame's FEC, which is what :meth:`atsc3lib.live.LiveReceiver.run_pipelined`
  does with ``fec_async=True``

Example::

    .venv/bin/python -m tools.bench_live --gpu on --frames 12
    .venv/bin/python -m tools.bench_live --gpu off --frames 12
"""

import argparse
import json
import os
import queue
import threading
import time
import types

import numpy as np

from atsc3lib import accelerator
from atsc3lib.payload import build_data_symbol_pool, decode_plp_from_pool

#: RF33 frame period (A/322 frame length, seconds).
LIVE_PERIOD_S = 0.2471

_HERE = os.path.dirname(__file__)
_DEFAULT_FIXTURE = os.path.join(_HERE, '..', 'tests', 'data', 'rf33_sf1_y.npy')
_DEFAULT_META = os.path.join(_HERE, '..', 'tests', 'data', 'rf33_sf1_meta.json')


def _plp_namespace(p):
    return types.SimpleNamespace(
        plp_id=p['plp_id'], start=p['start'], size=p['size'],
        modulation=p['mod'], code_rate=p['code_rate'], ti_mode=p['ti_mode'],
        hti_num_ti_blocks=p['nti'] - 1, hti_cell_interleaver=0,
        fec_type=p['fec_type'])


def _pool(y, m):
    return build_data_symbol_pool(
        y, 0, m['fft'], m['gi'], m['noc'], m['dx'], m['dy'],
        m['n_data_symbols'], sbs_symbols=tuple(m['sbs_symbols']),
        pattern=m['pattern'], sbs_null=m['sbs_null'], cred_coeff=m['cred'],
        fi_offset=m['fi_offset'], dense=True)


def _mean_ms(times):
    return sum(times) / len(times) * 1e3


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--fixture', default=_DEFAULT_FIXTURE)
    ap.add_argument('--meta', default=_DEFAULT_META)
    ap.add_argument('--gpu', default='auto', choices=['auto', 'on', 'off'])
    ap.add_argument('--frames', type=int, default=12,
                    help='iterations per mode (best-of omitted: mean)')
    ap.add_argument('--queue', type=int, default=2,
                    help='bounded pool/FEC queue depth for the overlap run')
    args = ap.parse_args(argv)

    os.environ[accelerator.GPU_ENV] = args.gpu
    accelerator._probe.cache_clear()

    y = np.load(args.fixture).astype(np.complex128)
    m = json.load(open(args.meta))
    plp = _plp_namespace(m['plp1'])

    pool = _pool(y, m)
    decode_plp_from_pool(pool, plp, max_iterations=100)

    pool_t, fec_t, serial_t = [], [], []
    for _ in range(args.frames):
        t = time.perf_counter()
        fresh = _pool(y, m)
        pool_t.append(time.perf_counter() - t)
        t = time.perf_counter()
        out = decode_plp_from_pool(fresh, plp, max_iterations=100)
        fec_t.append(time.perf_counter() - t)
        assert all(b.converged for b in out.fec_blocks)
        serial_t.append(pool_t[-1] + fec_t[-1])

    q: "queue.Queue" = queue.Queue(maxsize=args.queue)

    def producer():
        for _ in range(args.frames):
            q.put(_pool(y, m))
        q.put(None)

    thread = threading.Thread(target=producer, name="bench-pool", daemon=True)
    t0 = time.perf_counter()
    thread.start()
    overlapped = []
    while True:
        item = q.get()
        if item is None:
            break
        t = time.perf_counter()
        out = decode_plp_from_pool(item, plp, max_iterations=100)
        overlapped.append(time.perf_counter() - t)
        assert all(b.converged for b in out.fec_blocks)
    thread.join()
    total = time.perf_counter() - t0
    del overlapped

    print(f"backend: {'GPU' if accelerator.available() else 'CPU'} "
          f"(ATSC3LIB_GPU={args.gpu})")
    print(f"pool build        {_mean_ms(pool_t):8.1f} ms/frame")
    print(f"FEC decode        {_mean_ms(fec_t):8.1f} ms/frame")
    print(f"serial total      {_mean_ms(serial_t):8.1f} ms/frame")
    print(f"overlapped        {total / args.frames * 1e3:8.1f} ms/frame "
          f"(bounded queue {args.queue})")
    print(f"target            {LIVE_PERIOD_S * 1e3:8.1f} ms/frame")
    verdict = "PASS" if total / args.frames <= LIVE_PERIOD_S else "MISS"
    print(f"sustained live    {verdict}")
    return 0 if verdict == "PASS" else 1


if __name__ == '__main__':
    raise SystemExit(main())
