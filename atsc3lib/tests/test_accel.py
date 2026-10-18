"""Gates for the optional GPU accelerated path.

The CUDA kernels are differentially tested in ``gpubindings/tests``; these
tests gate the receive-path seam: enabling the GPU must leave the decoded
packets byte-identical to the required C/CPU path, and the backend must be
selectable without changing anything on a machine with no GPU.
"""

import json
import os
import types

import numpy as np
import pytest

from atsc3lib import accelerator

pytest.importorskip("gpubindings")

_DATA = os.path.join(os.path.dirname(__file__), 'data')
_SF1 = os.path.join(_DATA, 'rf33_sf1_y.npy')
_META = os.path.join(_DATA, 'rf33_sf1_meta.json')


def _plp(p):
    return types.SimpleNamespace(
        plp_id=p['plp_id'], start=p['start'], size=p['size'],
        modulation=p['mod'], code_rate=p['code_rate'], ti_mode=p['ti_mode'],
        hti_num_ti_blocks=p['nti'] - 1, hti_cell_interleaver=0,
        fec_type=p['fec_type'])


@pytest.fixture(scope='module')
def sf1():
    from atsc3lib.payload import build_data_symbol_pool

    y = np.load(_SF1).astype(np.complex128)
    m = json.load(open(_META))
    pool = build_data_symbol_pool(
        y, 0, m['fft'], m['gi'], m['noc'], m['dx'], m['dy'],
        m['n_data_symbols'], sbs_symbols=tuple(m['sbs_symbols']),
        pattern=m['pattern'], sbs_null=m['sbs_null'], cred_coeff=m['cred'],
        fi_offset=m['fi_offset'], dense=True)
    return pool, _plp(m['plp1'])


def _select(monkeypatch, value):
    monkeypatch.setenv(accelerator.GPU_ENV, value)
    accelerator._probe.cache_clear()


@pytest.mark.skipif(not accelerator.available(), reason="no CUDA backend")
def test_plp1_gpu_matches_cpu(monkeypatch, sf1):
    from atsc3lib.payload import decode_plp_from_pool

    pool, plp = sf1
    _select(monkeypatch, 'off')
    cpu = decode_plp_from_pool(pool, plp, max_iterations=100)
    _select(monkeypatch, 'on')
    gpu = decode_plp_from_pool(pool, plp, max_iterations=100)

    assert gpu.n_converged == gpu.n_fec == 117
    assert [b.packet for b in gpu.fec_blocks] == \
        [b.packet for b in cpu.fec_blocks]
    assert all(np.array_equal(a.bits, b.bits)
               for a, b in zip(cpu.fec_blocks, gpu.fec_blocks))


def test_off_forces_cpu(monkeypatch):
    _select(monkeypatch, 'off')
    assert accelerator.available() is False


def test_on_requires_backend(monkeypatch):
    _select(monkeypatch, 'on')
    if accelerator.available():
        pytest.skip("a CUDA backend is present")
    with pytest.raises(RuntimeError, match="gpubindings"):
        accelerator.require()
    monkeypatch.setenv(accelerator.GPU_ENV, 'off')
    accelerator._probe.cache_clear()


@pytest.mark.skipif(not accelerator.available(), reason="no CUDA backend")
def test_bch_batch_zero_error_path(monkeypatch):
    from atsc3lib.bch import BCHCode

    _select(monkeypatch, 'on')
    bch = BCHCode(64800, 12)
    rng = np.random.default_rng(7)
    kpayload = 47520
    code = np.asarray(bch.encode(rng.integers(0, 2, kpayload,
                                              dtype=np.uint8)),
                      dtype=np.uint8)
    rows = [code]
    for nerr in (1, 3, 12):
        bad = code.copy()
        bad[rng.choice(bad.size, nerr, replace=False)] ^= 1
        rows.append(bad)
    got = bch.decode_batch(np.stack(rows))
    for i, row in enumerate(rows):
        ref = bch.decode(row)
        assert np.array_equal(got[i][0], ref[0])
        assert got[i][1] == ref[1]
        assert bool(got[i][2]) == bool(ref[2])
