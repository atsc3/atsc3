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
 *     decode(handle, channel, max_iter, alpha) -> (uint8[n] hard, bool converged)
 *     destroy(handle)
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

static int _cuda_ok(cudaError_t err, const char *what)
{
    if (err != cudaSuccess) {
        PyErr_Format(PyExc_RuntimeError, "%s: %s", what,
                     cudaGetErrorString(err));
        return 0;
    }
    return 1;
}

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
    int threads;
    int blocks;
} ldpc_handle;

__global__ void ldpc_kernel(const int *check_idx, const unsigned char *check_mask,
                            int n_checks, int dmax, int n,
                            const int *var_start, const int *var_m,
                            const float *chan, float *M, float *total,
                            unsigned char *hard, int *flag, int *converged,
                            int max_iter, float alpha)
{
    cg::grid_group grid = cg::this_grid();
    const int tid = blockIdx.x * blockDim.x + threadIdx.x;
    const int stride = gridDim.x * blockDim.x;

    if (tid == 0) {
        *flag = 0;
        *converged = 0;
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
        }
    }
    grid.sync();
    if (tid == 0) {
        *converged = (*flag == 0) ? 1 : 0;
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
            }
        }
        grid.sync();
        if (tid == 0) {
            *converged = (*flag == 0) ? 1 : 0;
            *flag = 0;
        }
        grid.sync();
    }
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

    if (!_cuda_ok(cudaMemcpy(h->d_chan, PyArray_DATA(chan),
                             (size_t)h->n * sizeof(float),
                             cudaMemcpyHostToDevice), "chan")) {
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
    const float *d_chan = h->d_chan;
    const int n_checks = h->n_checks, dmax = h->dmax, n = h->n;

    void *kargs[] = {
        (void *)&d_idx, (void *)&d_msk, (void *)&n_checks, (void *)&dmax,
        (void *)&n, (void *)&d_vs, (void *)&d_vm, (void *)&d_chan,
        (void *)&d_M, (void *)&d_total, (void *)&d_hard, (void *)&d_flag,
        (void *)&d_conv, (void *)&max_iter, (void *)&alpha
    };

    cudaError_t err = cudaLaunchCooperativeKernel(
        (void *)ldpc_kernel, dim3(blocks), dim3(threads), kargs, 0, 0);
    if (!_cuda_ok(err, "launch")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }
    if (!_cuda_ok(cudaDeviceSynchronize(), "sync")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }
    if (!_cuda_ok(cudaMemcpy(PyArray_DATA(out), h->d_hard,
                             (size_t)h->n * sizeof(unsigned char),
                             cudaMemcpyDeviceToHost), "hard")) {
        Py_DECREF(chan); Py_DECREF(out);
        return NULL;
    }
    int conv = 0;
    cudaMemcpy(&conv, h->d_converged, sizeof(int), cudaMemcpyDeviceToHost);

    Py_DECREF(chan);
    return Py_BuildValue("NO", out, conv ? Py_True : Py_False);
}

static PyMethodDef methods[] = {
    {"create", create, METH_VARARGS, "Upload a Tanner graph; returns a handle."},
    {"decode", (PyCFunction)(void (*)(void))decode,
     METH_VARARGS | METH_KEYWORDS, "Normalized min-sum decode on the GPU."},
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
