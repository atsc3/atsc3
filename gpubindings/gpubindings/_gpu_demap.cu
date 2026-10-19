/*
 * gpubindings._gpu_demap - CUDA max-log NUC/QAM demapper (ATSC A/322 6.3.3).
 *
 * Port of atsc3lib._bindings._demap.demap_llr.  Reference algorithm:
 *
 *   d2[j]    = |z_i - p_j|^2
 *   best_i   = min_j d2[j]
 *   sigma2_k = max(mean_{i in block k} best_i, 1e-9)
 *   llr[i,b] = (min_{j: y_b=1} d2 - min_{j: y_b=0} d2) / sigma2_k
 *
 * y0 is the MSB of the point label (q-stream order); llr > 0 means bit 0, the
 * same convention as nuc.demap_llr and the C kernel.
 *
 * Split into three stages so sigma2 keeps the C kernel's summation order:
 *   1. minima_kernel   per cell: per-bit min1/min0 -> diff, and best_i
 *   2. host            sigma2 per block, summing best_i in ascending cell order
 *   3. scale_kernel    out[i] = diff[i] / sigma2[block(i)]
 *
 * Stage 2 is 8100 doubles; doing it on the host rather than as a GPU reduction
 * costs nothing and avoids a reordered sum.  The C demapper's own differential
 * test allows 1e-9 for exactly that reordering, so a GPU reduction would still
 * be legal -- matching the order just makes the kernel bit-identical to C.
 *
 * Gate: gpubindings/tests/test_demap_gpu.py against nuc._demap_llr_numpy at
 * atol 1e-9 (the same gate the C kernel uses), with the air gate
 * tests/test_subframe1.py::TestSf1Air::test_plp1_decodes (117/117) downstream.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <cuda_runtime.h>
#include <cuComplex.h>

#include <cmath>
#include <cstdlib>
#include <cstdio>

#include "_cuda_util.cuh"

#define CUDA_CHECK(call)                                                      \
    do {                                                                      \
        cudaError_t err_ = (call);                                            \
        if (err_ != cudaSuccess) {                                            \
            PyErr_Format(PyExc_RuntimeError, "%s: %s", #call,                 \
                         cudaGetErrorString(err_));                           \
            return NULL;                                                      \
        }                                                                     \
    } while (0)

/* One thread per cell.  B is a compile-time constant so the per-bit minima stay
 * in registers: the 2**mod_order x mod_order inner loop is the whole cost. */
template <int B>
__global__ void minima_kernel(const cuDoubleComplex *__restrict__ z,
                              const cuDoubleComplex *__restrict__ p,
                              int n, int m,
                              double *__restrict__ diff,
                              double *__restrict__ best)
{
    const int i = blockIdx.x * blockDim.x + threadIdx.x;
    if (i >= n) {
        return;
    }
    const double re = cuCreal(z[i]);
    const double im = cuCimag(z[i]);

    double min1[B], min0[B];
#pragma unroll
    for (int b = 0; b < B; ++b) {
        min1[b] = INFINITY;
        min0[b] = INFINITY;
    }

    for (int j = 0; j < m; ++j) {
        const double dr = re - cuCreal(p[j]);
        const double di = im - cuCimag(p[j]);
        const double d = dr * dr + di * di;
#pragma unroll
        for (int b = 0; b < B; ++b) {
            if ((j >> (B - 1 - b)) & 1) {
                if (d < min1[b]) min1[b] = d;
            } else {
                if (d < min0[b]) min0[b] = d;
            }
        }
    }

    double bst = INFINITY;
#pragma unroll
    for (int b = 0; b < B; ++b) {
        if (min1[b] < bst) bst = min1[b];
        if (min0[b] < bst) bst = min0[b];
        diff[(long)i * B + b] = min1[b] - min0[b];
    }
    best[i] = bst;
}

__global__ void scale_kernel(const double *__restrict__ diff,
                             const double *__restrict__ sig,
                             long nout, long denom,
                             double *__restrict__ out)
{
    const long i = (long)blockIdx.x * blockDim.x + threadIdx.x;
    if (i < nout) {
        out[i] = diff[i] / sig[i / denom];
    }
}

static PyObject *demap_llr(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"cells", "points", "mod_order", "block", NULL};
    PyObject *cells_o, *points_o;
    int mod_order;
    int block = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOi|i", kwlist,
                                     &cells_o, &points_o, &mod_order, &block)) {
        return NULL;
    }
    if (mod_order < 1 || mod_order > 16) {
        PyErr_SetString(PyExc_ValueError, "mod_order out of range");
        return NULL;
    }

    PyArrayObject *cells = (PyArrayObject *)PyArray_FROM_OTF(
        cells_o, NPY_COMPLEX128, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *pts = (PyArrayObject *)PyArray_FROM_OTF(
        points_o, NPY_COMPLEX128, NPY_ARRAY_IN_ARRAY);
    if (cells == NULL || pts == NULL) {
        Py_XDECREF(cells);
        Py_XDECREF(pts);
        return NULL;
    }

    const npy_intp n = PyArray_SIZE(cells);
    const npy_intp m = PyArray_SIZE(pts);
    if (m != (npy_intp)1 << mod_order) {
        Py_DECREF(cells);
        Py_DECREF(pts);
        PyErr_SetString(PyExc_ValueError, "points length must be 2**mod_order");
        return NULL;
    }

    npy_intp dims[1] = { n * mod_order };
    PyArrayObject *out = (PyArrayObject *)PyArray_SimpleNew(1, dims,
                                                            NPY_FLOAT64);
    if (out == NULL) {
        Py_DECREF(cells);
        Py_DECREF(pts);
        return NULL;
    }
    if (n == 0) {
        Py_DECREF(cells);
        Py_DECREF(pts);
        return (PyObject *)out;
    }

    const long blk = (block > 0 && (long)block < (long)n) ? (long)block : (long)n;
    const long nblk = ((long)n + blk - 1) / blk;
    const long nout = (long)n * mod_order;

    cuDoubleComplex *d_z = NULL, *d_p = NULL;
    double *d_diff = NULL, *d_best = NULL, *d_sig = NULL, *d_out = NULL;
    double *h_best = NULL, *h_sig = NULL;

    const size_t z_bytes = (size_t)n * sizeof(cuDoubleComplex);
    const size_t p_bytes = (size_t)m * sizeof(cuDoubleComplex);

    CUDA_CHECK(cudaMalloc(&d_z, z_bytes));
    CUDA_CHECK(cudaMalloc(&d_p, p_bytes));
    CUDA_CHECK(cudaMalloc(&d_diff, (size_t)nout * sizeof(double)));
    CUDA_CHECK(cudaMalloc(&d_best, (size_t)n * sizeof(double)));
    CUDA_CHECK(cudaMalloc(&d_sig, (size_t)nblk * sizeof(double)));
    CUDA_CHECK(cudaMalloc(&d_out, (size_t)nout * sizeof(double)));
    h_best = (double *)malloc((size_t)n * sizeof(double));
    h_sig = (double *)malloc((size_t)nblk * sizeof(double));
    if (h_best == NULL || h_sig == NULL) {
        cudaFree(d_z); cudaFree(d_p); cudaFree(d_diff); cudaFree(d_best);
        cudaFree(d_sig); cudaFree(d_out);
        free(h_best); free(h_sig);
        Py_DECREF(out);
        Py_DECREF(cells);
        Py_DECREF(pts);
        return PyErr_NoMemory();
    }

    if (!_copy_h2d(d_z, PyArray_DATA(cells), z_bytes, "cells")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }
    if (!_copy_h2d(d_p, PyArray_DATA(pts), p_bytes, "points")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }

    const int threads = 256;
    const int blocks = (int)(((long)n + threads - 1) / threads);

#define LAUNCH_MINIMA(BITS)                                                   \
    minima_kernel<BITS><<<blocks, threads>>>(d_z, d_p, (int)n, (int)m,        \
                                             d_diff, d_best)
    switch (mod_order) {
        case 1:  LAUNCH_MINIMA(1);  break;
        case 2:  LAUNCH_MINIMA(2);  break;
        case 3:  LAUNCH_MINIMA(3);  break;
        case 4:  LAUNCH_MINIMA(4);  break;
        case 5:  LAUNCH_MINIMA(5);  break;
        case 6:  LAUNCH_MINIMA(6);  break;
        case 7:  LAUNCH_MINIMA(7);  break;
        case 8:  LAUNCH_MINIMA(8);  break;
        case 9:  LAUNCH_MINIMA(9);  break;
        case 10: LAUNCH_MINIMA(10); break;
        case 11: LAUNCH_MINIMA(11); break;
        case 12: LAUNCH_MINIMA(12); break;
        case 13: LAUNCH_MINIMA(13); break;
        case 14: LAUNCH_MINIMA(14); break;
        case 15: LAUNCH_MINIMA(15); break;
        case 16: LAUNCH_MINIMA(16); break;
        default:
            PyErr_SetString(PyExc_ValueError, "mod_order out of range");
            break;
    }
#undef LAUNCH_MINIMA
    if (!_sync_device("minima")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }

    if (!_copy_d2h(h_best, d_best, (size_t)n * sizeof(double), "best")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }

    /* sigma2 per block, summed in ascending cell order: the same order the C
     * kernel accumulates, so no rounding difference is introduced here. */
    for (long k = 0; k < nblk; ++k) {
        const long start = k * blk;
        long end = start + blk;
        if (end > (long)n) {
            end = (long)n;
        }
        double sum = 0.0;
        for (long i = start; i < end; ++i) {
            sum += h_best[i];
        }
        const double s = sum / (double)(end - start);
        h_sig[k] = (s < 1e-9) ? 1e-9 : s;
    }
    if (!_copy_h2d(d_sig, h_sig, (size_t)nblk * sizeof(double), "sig")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }

    const long scale_blocks = (nout + threads - 1) / threads;
    scale_kernel<<<(int)scale_blocks, threads>>>(d_diff, d_sig, nout,
                                                 blk * (long)mod_order, d_out);
    if (!_sync_device("scale")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }

    if (!_copy_d2h(PyArray_DATA(out), d_out, (size_t)nout * sizeof(double),
                   "out")) {
        Py_DECREF(out); Py_DECREF(cells); Py_DECREF(pts);
        return NULL;
    }

    cudaFree(d_z); cudaFree(d_p); cudaFree(d_diff); cudaFree(d_best);
    cudaFree(d_sig); cudaFree(d_out);
    free(h_best); free(h_sig);
    Py_DECREF(cells);
    Py_DECREF(pts);
    return (PyObject *)out;
}

static PyMethodDef methods[] = {
    {"demap_llr", (PyCFunction)(void (*)(void))demap_llr,
     METH_VARARGS | METH_KEYWORDS,
     "Max-log NUC/QAM LLRs (A/322 6.3.3) on the GPU; block resets sigma2 per N "
     "cells."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_gpu_demap",
    "CUDA max-log demapper for ATSC 3.0 (A/322 6.3.3).",
    -1,
    methods,
};

extern "C" PyMODINIT_FUNC PyInit__gpu_demap(void)
{
    import_array();
    return PyModule_Create(&moduledef);
}
