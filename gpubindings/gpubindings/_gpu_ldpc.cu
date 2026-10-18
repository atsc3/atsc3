/*
 * gpubindings._gpu_ldpc - normalized min-sum LDPC decoder (ATSC A/322 6.1.3).
 *
 * Port of atsc3lib._bindings._ldpc.decode_ldpc.  Same message ordering, same
 * float32 arithmetic, same alpha scaling, so the hard decisions are identical.
 *
 * Two details of the reference drive this design:
 *
 *  1. The variable-node sum is accumulated in **double** and only then rounded
 *     to float32 before the float32 add to the channel value:
 *         vsum[i] = (double)chan[i]; vsum[idx[j]] += (double)Mrow[j];
 *         total[i] = chan[i] + (float)vsum[i];
 *     A float32 or atomically-scattered sum would round differently, so the
 *     kernel gathers each variable's edges in check-ascending order (the same
 *     order the C loop accumulates) using a var-major adjacency built once on
 *     the host.  That keeps the result bit-identical rather than merely close.
 *
 *  2. The decode stops at the first iteration whose syndrome checks pass.  A
 *     host loop would need a device sync per iteration (~100 per block), which
 *     alone would exceed the frame budget, so the whole iteration loop runs
 *     inside one cooperative kernel with grid.sync() between phases and the
 *     convergence flag read on device.
 *
 * API (handle-based so the graph is uploaded once per lock, not per block):
 *     create(check_idx, check_mask, var_start, var_m) -> handle
 *     decode(handle, channel, max_iter, alpha) -> (uint8[n] hard, bool, unsat)
 *     decode_many(handle, (B, n) channels, max_iter, alpha) -> three arrays
 *     device_count() -> visible CUDA devices
 *     destroy(handle)
 *
 * decode_many enqueues one single-block kernel per codeword and syncs once; it
 * shares the per-block buffers but keeps the graph and block geometry.  A
 * merged single-launch kernel and a grouped variant were measured and are not
 * faster (one 64K codeword already fills the GPU), so they were removed.
 *
 * Gate: gpubindings/tests/test_ldpc_gpu.py, hard bits identical to the C
 * kernel on real 64800/16200 codewords, plus the air gate
 * tests/test_subframe1.py::TestSf1Air::test_plp1_decodes (117/117).
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <cuda_runtime.h>
#include <cooperative_groups.h>

#include <cmath>
#include <cstdlib>
#include <cstring>

namespace cg = cooperative_groups;

#include "_cuda_util.cuh"

typedef struct {
    int n;
    int n_checks;
    int dmax;
    int n_edges;
    int *d_check_idx;
    unsigned char *d_check_mask;
    int *d_var_start;
    int *d_var_m;
    float *d_M;
    float *d_total;
    float *d_chan;
    unsigned char *d_hard;
    int *d_flag;
    int *d_converged;
    int *d_unsat;
    int *d_last_unsat;
    float *d_Mb;
    float *d_totalb;
    float *d_chanb;
    unsigned char *d_hardb;
    int *d_unsatb;
    int *d_last_unsatb;
    int *d_flagb;
    int *d_convb;
    int batch_capacity;
    int threads;
    int blocks;
} ldpc_handle;

__global__ void ldpc_kernel(const int *check_idx, const unsigned char *check_mask,
                            int n_checks, int dmax, int n,
                            const int *var_start, const int *var_m,
                            const float *chan, float *M, float *total,
                            unsigned char *hard, int *flag, int *converged,
                            int *unsat, int *last_unsat,
                            int max_iter, float alpha)
{
    cg::grid_group grid = cg::this_grid();
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    const int stride = gridDim.x * blockDim.x;

    if (tid == 0) {
        *flag = 0;
        *converged = 0;
        *unsat = 0;
        *last_unsat = 0;
    }
    for (int i = tid; i < n; i += stride) {
        total[i] = chan[i];
        hard[i] = (total[i] < 0.0f) ? 1u : 0u;
    }
    for (int i = tid; i < n_checks * dmax; i += stride) {
        M[i] = 0.0f;
    }
    grid.sync();

    /* Initial syndrome: a valid codeword costs zero iterations. */
    if (tid < n_checks) {
        int acc = 0;
        const int *idx = check_idx + (long)tid * dmax;
        const unsigned char *msk = check_mask + (long)tid * dmax;
        for (int j = 0; j < dmax; ++j) {
            if (msk[j]) {
                acc ^= hard[idx[j]];
            }
        }
        if (acc) {
            atomicExch(flag, 1);
            atomicAdd(unsat, 1);
        }
    }
    grid.sync();
    if (tid == 0) {
        *last_unsat = *unsat;
        *converged = (*flag == 0) ? 1 : 0;
        *unsat = 0;
        *flag = 0;
    }
    grid.sync();

    for (int it = 0; it < max_iter && !(*converged); ++it) {
        /* Check-node update: one thread per check row, in-place like the C
         * kernel (each row is private, so no cross-thread hazard). */
        for (int c = tid; c < n_checks; c += stride) {
            const int *idx = check_idx + (long)c * dmax;
            const unsigned char *msk = check_mask + (long)c * dmax;
            float *Mrow = M + (long)c * dmax;

            float min1 = INFINITY, min2 = INFINITY;
            int first = -1, degree = 0, parity = 0;

            for (int j = 0; j < dmax; ++j) {
                if (!msk[j]) {
                    continue;
                }
                const float w = total[idx[j]] - Mrow[j];
                float *slot = Mrow + j;
                *slot = w;  /* stash W in M temporarily, as C uses W[] */
                degree++;
                if (w < 0.0f) {
                    parity ^= 1;
                }
                const float m = fabsf(w);
                if (m < min1) {
                    min2 = min1;
                    min1 = m;
                    first = j;
                } else if (m < min2) {
                    min2 = m;
                }
            }
            if (first < 0) {
                continue;
            }
            if (degree == 1) {
                min2 = min1;
            }
            for (int j = 0; j < dmax; ++j) {
                if (!msk[j]) {
                    continue;
                }
                const float w = Mrow[j];  /* the stashed W value */
                const float out_mag = (j == first) ? min2 : min1;
                const int neg = (w < 0.0f) ? 1 : 0;
                const float sign = (parity ^ neg) ? -1.0f : 1.0f;
                Mrow[j] = out_mag * sign * alpha;
            }
        }
        grid.sync();

        /* Variable-node update: gather in check-ascending order (the order the
         * host-built var_m preserves) and accumulate in double. */
        for (int i = tid; i < n; i += stride) {
            double vs = (double)chan[i];
            const int end = var_start[i + 1];
            for (int e = var_start[i]; e < end; ++e) {
                vs += (double)M[var_m[e]];
            }
            total[i] = chan[i] + (float)vs;
            hard[i] = (total[i] < 0.0f) ? 1u : 0u;
        }
        grid.sync();

        if (tid < n_checks) {
            int acc = 0;
            const int *idx = check_idx + (long)tid * dmax;
            const unsigned char *msk = check_mask + (long)tid * dmax;
            for (int j = 0; j < dmax; ++j) {
                if (msk[j]) {
                    acc ^= hard[idx[j]];
                }
            }
            if (acc) {
                atomicExch(flag, 1);
                atomicAdd(unsat, 1);
            }
        }
        grid.sync();
        if (tid == 0) {
            *last_unsat = *unsat;
            *converged = (*flag == 0) ? 1 : 0;
            *unsat = 0;
            *flag = 0;
        }
        grid.sync();
    }
}

static void _free_batch(ldpc_handle *h)
{
    if (h->d_Mb) { cudaFree(h->d_Mb); h->d_Mb = NULL; }
    if (h->d_totalb) { cudaFree(h->d_totalb); h->d_totalb = NULL; }
    if (h->d_chanb) { cudaFree(h->d_chanb); h->d_chanb = NULL; }
    if (h->d_hardb) { cudaFree(h->d_hardb); h->d_hardb = NULL; }
    if (h->d_unsatb) { cudaFree(h->d_unsatb); h->d_unsatb = NULL; }
    if (h->d_last_unsatb) { cudaFree(h->d_last_unsatb); h->d_last_unsatb = NULL; }
    if (h->d_flagb) { cudaFree(h->d_flagb); h->d_flagb = NULL; }
    if (h->d_convb) { cudaFree(h->d_convb); h->d_convb = NULL; }
    h->batch_capacity = 0;
}

static int _ensure_batch(ldpc_handle *h, int batch)
{
    if (batch <= h->batch_capacity) {
        return 1;
    }
    _free_batch(h);
    const size_t mrows = (size_t)h->n_checks * h->dmax;
    int ok = 1;
    ok &= _cuda_ok(cudaMalloc(&h->d_Mb, (size_t)batch * mrows * sizeof(float)),
                   "batch M");
    ok &= _cuda_ok(cudaMalloc(&h->d_totalb, (size_t)batch * h->n * sizeof(float)),
                   "batch total");
    ok &= _cuda_ok(cudaMalloc(&h->d_chanb, (size_t)batch * h->n * sizeof(float)),
                   "batch chan");
    ok &= _cuda_ok(cudaMalloc(&h->d_hardb,
                              (size_t)batch * h->n * sizeof(unsigned char)),
                   "batch hard");
    ok &= _cuda_ok(cudaMalloc(&h->d_unsatb, (size_t)batch * sizeof(int)),
                   "batch unsat");
    ok &= _cuda_ok(cudaMalloc(&h->d_last_unsatb, (size_t)batch * sizeof(int)),
                   "batch last unsat");
    ok &= _cuda_ok(cudaMalloc(&h->d_flagb, (size_t)batch * sizeof(int)),
                   "batch flag");
    ok &= _cuda_ok(cudaMalloc(&h->d_convb, (size_t)batch * sizeof(int)),
                   "batch conv");
    if (!ok) {
        _free_batch(h);
        return 0;
    }
    h->batch_capacity = batch;
    return 1;
}

static PyObject *decode_many(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"handle", "channels", "max_iter", "alpha", NULL};
    PyObject *cap, *chan_o;
    int max_iter;
    float alpha = 0.75f;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOi|f", kwlist,
                                     &cap, &chan_o, &max_iter, &alpha)) {
        return NULL;
    }
    ldpc_handle *h = (ldpc_handle *)PyLong_AsVoidPtr(cap);
    if (h == NULL) {
        return NULL;
    }
    PyArrayObject *chan = (PyArrayObject *)PyArray_FROM_OTF(
        chan_o, NPY_FLOAT32, NPY_ARRAY_IN_ARRAY);
    if (chan == NULL) {
        return NULL;
    }
    const long total = (long)PyArray_SIZE(chan);
    if (total % h->n != 0) {
        Py_DECREF(chan);
        PyErr_SetString(PyExc_ValueError, "channel size not a multiple of n");
        return NULL;
    }
    const int batch = (int)(total / h->n);
    if (batch < 1) {
        Py_DECREF(chan);
        PyErr_SetString(PyExc_ValueError, "empty batch");
        return NULL;
    }
    if (!_ensure_batch(h, batch)) {
        Py_DECREF(chan);
        return NULL;
    }

    npy_intp hdims[2] = { batch, h->n };
    npy_intp bdims[1] = { batch };
    PyArrayObject *hard = (PyArrayObject *)PyArray_SimpleNew(2, hdims, NPY_UINT8);
    PyArrayObject *conv = (PyArrayObject *)PyArray_SimpleNew(1, bdims, NPY_BOOL);
    PyArrayObject *unsat = (PyArrayObject *)PyArray_SimpleNew(1, bdims, NPY_INT32);
    if (hard == NULL || conv == NULL || unsat == NULL) {
        Py_XDECREF(hard); Py_XDECREF(conv); Py_XDECREF(unsat);
        Py_DECREF(chan);
        return NULL;
    }

    if (!_copy_h2d(h->d_chanb, PyArray_DATA(chan),
                   (size_t)total * sizeof(float), "many chan")) {
        Py_DECREF(chan); Py_DECREF(hard); Py_DECREF(conv); Py_DECREF(unsat);
        return NULL;
    }

    const long mrows = (long)h->n_checks * h->dmax;
    const int n = h->n, n_checks = h->n_checks, dmax = h->dmax;
    const int *d_idx = h->d_check_idx;
    unsigned char *d_msk = h->d_check_mask;
    const int *d_vs = h->d_var_start;
    const int *d_vm = h->d_var_m;

    cudaError_t launch_err = cudaSuccess;
    Py_BEGIN_ALLOW_THREADS
    for (int b = 0; b < batch; ++b) {
        const float *d_chan = h->d_chanb + (long)b * n;
        float *d_M = h->d_Mb + (long)b * mrows;
        float *d_total = h->d_totalb + (long)b * n;
        unsigned char *d_hard = h->d_hardb + (long)b * n;
        int *d_flag = h->d_flagb + b;
        int *d_conv = h->d_convb + b;
        int *d_unsat = h->d_unsatb + b;
        int *d_last = h->d_last_unsatb + b;

        void *kargs[] = {
            (void *)&d_idx, (void *)&d_msk, (void *)&n_checks, (void *)&dmax,
            (void *)&n, (void *)&d_vs, (void *)&d_vm, (void *)&d_chan,
            (void *)&d_M, (void *)&d_total, (void *)&d_hard, (void *)&d_flag,
            (void *)&d_conv, (void *)&d_unsat, (void *)&d_last,
            (void *)&max_iter, (void *)&alpha
        };

        launch_err = cudaLaunchCooperativeKernel(
            (void *)ldpc_kernel, dim3(h->blocks), dim3(h->threads), kargs, 0, 0);
        if (launch_err != cudaSuccess) {
            break;
        }
    }
    if (launch_err == cudaSuccess) {
        launch_err = cudaDeviceSynchronize();
    }
    Py_END_ALLOW_THREADS
    if (!_cuda_ok(launch_err, "many launch/sync")) {
        Py_DECREF(chan); Py_DECREF(hard); Py_DECREF(conv); Py_DECREF(unsat);
        return NULL;
    }
    if (!_copy_d2h(PyArray_DATA(hard), h->d_hardb,
                   (size_t)total * sizeof(unsigned char), "many hard")) {
        Py_DECREF(chan); Py_DECREF(hard); Py_DECREF(conv); Py_DECREF(unsat);
        return NULL;
    }
    int *h_conv = (int *)malloc((size_t)batch * sizeof(int));
    int *h_unsat = (int *)malloc((size_t)batch * sizeof(int));
    if (h_conv == NULL || h_unsat == NULL) {
        free(h_conv); free(h_unsat);
        Py_DECREF(chan); Py_DECREF(hard); Py_DECREF(conv); Py_DECREF(unsat);
        return PyErr_NoMemory();
    }
    cudaError_t out_err;
    Py_BEGIN_ALLOW_THREADS
    out_err = cudaMemcpy(h_conv, h->d_convb, (size_t)batch * sizeof(int),
                         cudaMemcpyDeviceToHost);
    if (out_err == cudaSuccess) {
        out_err = cudaMemcpy(h_unsat, h->d_last_unsatb,
                             (size_t)batch * sizeof(int),
                             cudaMemcpyDeviceToHost);
    }
    Py_END_ALLOW_THREADS
    if (!_cuda_ok(out_err, "many results")) {
        free(h_conv); free(h_unsat);
        Py_DECREF(chan); Py_DECREF(hard); Py_DECREF(conv); Py_DECREF(unsat);
        return NULL;
    }
    for (int b = 0; b < batch; ++b) {
        *(unsigned char *)(PyArray_GETPTR1(conv, b)) =
            (unsigned char)(h_conv[b] ? 1 : 0);
        *(int *)(PyArray_GETPTR1(unsat, b)) = h_unsat[b];
    }
    free(h_conv); free(h_unsat);

    Py_DECREF(chan);
    return Py_BuildValue("NNN", hard, conv, unsat);
}

static PyObject *device_count(PyObject *self, PyObject *args)
{
    (void)self;
    (void)args;
    int count = 0;
    cudaError_t err = cudaGetDeviceCount(&count);
    if (err != cudaSuccess) {
        cudaGetLastError();
        count = 0;
    }
    return PyLong_FromLong(count);
}

static PyObject *create(PyObject *self, PyObject *args)
{
    (void)self;
    PyObject *idx_o, *msk_o, *vs_o, *vm_o;
    if (!PyArg_ParseTuple(args, "OOOO", &idx_o, &msk_o, &vs_o, &vm_o)) {
        return NULL;
    }
    PyArrayObject *idx = (PyArrayObject *)PyArray_FROM_OTF(
        idx_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *msk = (PyArrayObject *)PyArray_FROM_OTF(
        msk_o, NPY_UINT8, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *vs = (PyArrayObject *)PyArray_FROM_OTF(
        vs_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *vm = (PyArrayObject *)PyArray_FROM_OTF(
        vm_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (!idx || !msk || !vs || !vm) {
        Py_XDECREF(idx); Py_XDECREF(msk); Py_XDECREF(vs); Py_XDECREF(vm);
        return NULL;
    }

    ldpc_handle *h = (ldpc_handle *)calloc(1, sizeof(ldpc_handle));
    if (h == NULL) {
        Py_DECREF(idx); Py_DECREF(msk); Py_DECREF(vs); Py_DECREF(vm);
        return PyErr_NoMemory();
    }
    h->n_checks = (int)PyArray_DIM(idx, 0);
    h->dmax = (int)PyArray_DIM(idx, 1);
    h->n_edges = (int)PyArray_SIZE(vm);
    h->n = (int)PyArray_DIM(vs, 0) - 1;

    const size_t sz_idx = (size_t)h->n_checks * h->dmax;
    int ok = 1;
    ok &= _cuda_ok(cudaMalloc(&h->d_check_idx, sz_idx * sizeof(int)), "idx");
    ok &= _cuda_ok(cudaMalloc(&h->d_check_mask, sz_idx * sizeof(unsigned char)), "mask");
    ok &= _cuda_ok(cudaMalloc(&h->d_var_start, ((size_t)h->n + 1) * sizeof(int)), "vs");
    ok &= _cuda_ok(cudaMalloc(&h->d_var_m, (size_t)h->n_edges * sizeof(int)), "vm");
    ok &= _cuda_ok(cudaMalloc(&h->d_M, sz_idx * sizeof(float)), "M");
    ok &= _cuda_ok(cudaMalloc(&h->d_total, (size_t)h->n * sizeof(float)), "total");
    ok &= _cuda_ok(cudaMalloc(&h->d_chan, (size_t)h->n * sizeof(float)), "chan");
    ok &= _cuda_ok(cudaMalloc(&h->d_hard, (size_t)h->n * sizeof(unsigned char)), "hard");
    ok &= _cuda_ok(cudaMalloc(&h->d_flag, sizeof(int)), "flag");
    ok &= _cuda_ok(cudaMalloc(&h->d_converged, sizeof(int)), "conv");
    ok &= _cuda_ok(cudaMalloc(&h->d_unsat, sizeof(int)), "unsat");
    ok &= _cuda_ok(cudaMalloc(&h->d_last_unsat, sizeof(int)), "last unsat");
    if (!ok) {
        Py_DECREF(idx); Py_DECREF(msk); Py_DECREF(vs); Py_DECREF(vm);
        return NULL;
    }

    cudaMemcpy(h->d_check_idx, PyArray_DATA(idx), sz_idx * sizeof(int), cudaMemcpyHostToDevice);
    cudaMemcpy(h->d_check_mask, PyArray_DATA(msk), sz_idx * sizeof(unsigned char), cudaMemcpyHostToDevice);
    cudaMemcpy(h->d_var_start, PyArray_DATA(vs), ((size_t)h->n + 1) * sizeof(int), cudaMemcpyHostToDevice);
    cudaMemcpy(h->d_var_m, PyArray_DATA(vm), (size_t)h->n_edges * sizeof(int), cudaMemcpyHostToDevice);

    /* Launch geometry is a property of the graph and the device, so resolve it
     * once here: cudaGetDeviceProperties costs ~1 ms and decode() runs once per
     * FEC block (117 per frame). */
    h->threads = 256;
    int dev = 0;
    cudaGetDevice(&dev);
    cudaDeviceProp prop;
    cudaGetDeviceProperties(&prop, dev);
    int per_sm = 0;
    cudaOccupancyMaxActiveBlocksPerMultiprocessor(&per_sm, ldpc_kernel,
                                                  h->threads, 0);
    long want = (h->n_checks > h->n ? h->n_checks : h->n) / h->threads + 1;
    long cap_blocks = (long)per_sm * prop.multiProcessorCount;
    h->blocks = (int)(want < cap_blocks ? want : cap_blocks);
    if (h->blocks < 1) {
        h->blocks = 1;
    }

    Py_DECREF(idx); Py_DECREF(msk); Py_DECREF(vs); Py_DECREF(vm);
    return PyLong_FromVoidPtr((void *)h);
}

static PyObject *destroy(PyObject *self, PyObject *args)
{
    (void)self;
    PyObject *cap;
    if (!PyArg_ParseTuple(args, "O", &cap)) {
        return NULL;
    }
    ldpc_handle *h = (ldpc_handle *)PyLong_AsVoidPtr(cap);
    if (h == NULL) {
        return NULL;
    }
    cudaFree(h->d_check_idx); cudaFree(h->d_check_mask);
    cudaFree(h->d_var_start); cudaFree(h->d_var_m);
    cudaFree(h->d_M); cudaFree(h->d_total); cudaFree(h->d_chan);
    cudaFree(h->d_hard);
    cudaFree(h->d_flag); cudaFree(h->d_converged);
    cudaFree(h->d_unsat); cudaFree(h->d_last_unsat);
    _free_batch(h);
    free(h);
    Py_RETURN_NONE;
}

static PyObject *decode(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"handle", "channel", "max_iter", "alpha", NULL};
    PyObject *cap, *chan_o;
    int max_iter;
    float alpha = 0.75f;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOi|f", kwlist,
                                     &cap, &chan_o, &max_iter, &alpha)) {
        return NULL;
    }
    ldpc_handle *h = (ldpc_handle *)PyLong_AsVoidPtr(cap);
    if (h == NULL) {
        return NULL;
    }
    PyArrayObject *chan = (PyArrayObject *)PyArray_FROM_OTF(
        chan_o, NPY_FLOAT32, NPY_ARRAY_IN_ARRAY);
    if (chan == NULL) {
        return NULL;
    }
    if ((int)PyArray_SIZE(chan) != h->n) {
        Py_DECREF(chan);
        PyErr_SetString(PyExc_ValueError, "channel length != graph n");
        return NULL;
    }

    npy_intp dims[1] = { h->n };
    PyArrayObject *out = (PyArrayObject *)PyArray_SimpleNew(1, dims, NPY_UINT8);
    if (out == NULL) {
        Py_DECREF(chan);
        return NULL;
    }

    if (!_copy_h2d(h->d_chan, PyArray_DATA(chan),
                   (size_t)h->n * sizeof(float), "chan")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }

    int threads = h->threads;
    int blocks = h->blocks;

    const int *d_idx = h->d_check_idx;
    unsigned char *d_msk = h->d_check_mask;
    const int *d_vs = h->d_var_start;
    const int *d_vm = h->d_var_m;
    float *d_M = h->d_M;
    float *d_total = h->d_total;
    unsigned char *d_hard = h->d_hard;
    int *d_flag = h->d_flag;
    int *d_conv = h->d_converged;
    int *d_unsat = h->d_unsat;
    int *d_last = h->d_last_unsat;
    const float *d_chan = h->d_chan;
    const int n_checks = h->n_checks, dmax = h->dmax, n = h->n;

    void *kargs[] = {
        (void *)&d_idx, (void *)&d_msk, (void *)&n_checks, (void *)&dmax,
        (void *)&n, (void *)&d_vs, (void *)&d_vm, (void *)&d_chan,
        (void *)&d_M, (void *)&d_total, (void *)&d_hard, (void *)&d_flag,
        (void *)&d_conv, (void *)&d_unsat, (void *)&d_last,
        (void *)&max_iter, (void *)&alpha
    };

    cudaError_t err = cudaLaunchCooperativeKernel(
        (void *)ldpc_kernel, dim3(blocks), dim3(threads), kargs, 0, 0);
    if (!_cuda_ok(err, "launch")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }
    if (!_sync_device("sync")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }
    if (!_copy_d2h(PyArray_DATA(out), h->d_hard,
                   (size_t)h->n * sizeof(unsigned char), "hard")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }
    int conv = 0, unsat = 0;
    cudaError_t res_err;
    Py_BEGIN_ALLOW_THREADS
    res_err = cudaMemcpy(&conv, h->d_converged, sizeof(int),
                         cudaMemcpyDeviceToHost);
    if (res_err == cudaSuccess) {
        res_err = cudaMemcpy(&unsat, h->d_last_unsat, sizeof(int),
                             cudaMemcpyDeviceToHost);
    }
    Py_END_ALLOW_THREADS
    if (!_cuda_ok(res_err, "conv")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }

    Py_DECREF(chan);
    return Py_BuildValue("NNN", out, PyBool_FromLong(conv),
                         PyLong_FromLong(unsat));
}

static PyMethodDef methods[] = {
    {"device_count", device_count, METH_NOARGS,
     "Number of visible CUDA devices (0 when the driver is absent)."},
    {"create", create, METH_VARARGS, "Upload a Tanner graph; returns a handle."},
    {"decode", (PyCFunction)(void (*)(void))decode,
     METH_VARARGS | METH_KEYWORDS, "Normalized min-sum decode on the GPU."},
    {"decode_many", (PyCFunction)(void (*)(void))decode_many,
     METH_VARARGS | METH_KEYWORDS,
     "Decode a batch as one enqueue per codeword, one sync."},
    {"destroy", destroy, METH_VARARGS, "Free a graph handle."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT, "_gpu_ldpc",
    "CUDA normalized min-sum LDPC decoder (A/322 6.1.3).", -1, methods,
};

extern "C" PyMODINIT_FUNC PyInit__gpu_ldpc(void)
{
    import_array();
    return PyModule_Create(&moduledef);
}
