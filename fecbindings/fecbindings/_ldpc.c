/*
 * _ldpc - normalized min-sum LDPC decoder (ATSC A/322 6.1.3) as a CPython
 * extension.
 *
 * This is the hot kernel of the receive chain: for RF33 PLP-0 the pure-NumPy
 * decoder spends ~3.9 s of a ~5 s frame over 78 codewords.  The C routine
 * implements exactly the same normalized min-sum belief propagation as
 * atsc3lib.ldpc_exact.ATSC3LDPCExact.decode (same message ordering, same
 * float32 arithmetic, same alpha ladder applied by the caller), so it is a
 * drop-in replacement, not an approximation.
 *
 * The Tanner graph is passed in pre-flattened by the caller:
 *
 *   channel    (n,)            float32   negated LLRs (L'>0 => bit 0)
 *   check_idx  (n_checks, dmax) int32    variable column per edge slot
 *   check_mask (n_checks, dmax) uint8    1 on a real slot
 *   edge_var   (n_edges,)       int32    variable per edge (check-major)
 *
 * decode_ldpc(channel, check_idx, check_mask, edge_var, max_iter, alpha)
 *     -> (numpy uint8[n] hard bits, bool converged)
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <math.h>
#include <stdlib.h>
#include <string.h>

static int syndromes_satisfied(const uint8_t *hard,
                               const int32_t *check_idx,
                               const uint8_t *check_mask,
                               int n_checks, int dmax)
{
    for (int c = 0; c < n_checks; c++) {
        int acc = 0;
        const int32_t *idx = check_idx + (size_t)c * dmax;
        const uint8_t *msk = check_mask + (size_t)c * dmax;
        for (int j = 0; j < dmax; j++) {
            if (msk[j]) {
                acc ^= hard[idx[j]];
            }
        }
        if (acc) {
            return 0;
        }
    }
    return 1;
}

static PyObject *decode_ldpc(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"channel", "check_idx", "check_mask",
                             "edge_var", "max_iter", "alpha", NULL};
    PyObject *channel_o, *idx_o, *mask_o, *var_o;
    int max_iter;
    float alpha = 0.75f;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOOOi|f", kwlist,
                                     &channel_o, &idx_o, &mask_o, &var_o,
                                     &max_iter, &alpha)) {
        return NULL;
    }

    PyArrayObject *channel = (PyArrayObject *)PyArray_FROM_OTF(
        channel_o, NPY_FLOAT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *check_idx = (PyArrayObject *)PyArray_FROM_OTF(
        idx_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *check_mask = (PyArrayObject *)PyArray_FROM_OTF(
        mask_o, NPY_UINT8, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *edge_var = (PyArrayObject *)PyArray_FROM_OTF(
        var_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (channel == NULL || check_idx == NULL || check_mask == NULL
        || edge_var == NULL) {
        Py_XDECREF(channel);
        Py_XDECREF(check_idx);
        Py_XDECREF(check_mask);
        Py_XDECREF(edge_var);
        return NULL;
    }

    const npy_intp n = PyArray_DIM(channel, 0);
    const npy_intp n_checks = PyArray_DIM(check_idx, 0);
    const npy_intp dmax = PyArray_DIM(check_idx, 1);
    (void)edge_var;  /* the graph is walked check-major, matching bincount */

    const float *chan = (const float *)PyArray_DATA(channel);
    const int32_t *cidx = (const int32_t *)PyArray_DATA(check_idx);
    const uint8_t *cmsk = (const uint8_t *)PyArray_DATA(check_mask);

    /* Validate the graph against the channel length before touching memory:
     * every active slot's column must be a valid variable index. */
    for (npy_intp c = 0; c < n_checks; c++) {
        for (npy_intp j = 0; j < dmax; j++) {
            if (cmsk[c * dmax + j]
                && (cidx[c * dmax + j] < 0 || cidx[c * dmax + j] >= n)) {
                Py_DECREF(channel);
                Py_DECREF(check_idx);
                Py_DECREF(check_mask);
                Py_DECREF(edge_var);
                PyErr_SetString(PyExc_ValueError,
                                "check_idx column out of range for channel");
                return NULL;
            }
        }
    }

    float *total = malloc((size_t)n * sizeof(float));
    float *M = calloc((size_t)n_checks * dmax, sizeof(float));
    uint8_t *hard = malloc((size_t)n * sizeof(uint8_t));
    float *W = malloc((size_t)dmax * sizeof(float));
    /* The reference variable-node update sums edge messages with
     * np.bincount (float64) and rounds once, so accumulate in double to stay
     * bit-identical. */
    double *vsum = malloc((size_t)n * sizeof(double));
    if (!total || !M || !hard || !W || !vsum) {
        free(total);
        free(M);
        free(hard);
        free(W);
        free(vsum);
        Py_DECREF(channel);
        Py_DECREF(check_idx);
        Py_DECREF(check_mask);
        Py_DECREF(edge_var);
        return PyErr_NoMemory();
    }

    for (npy_intp i = 0; i < n; i++) {
        total[i] = chan[i];
        hard[i] = total[i] < 0.0f ? 1 : 0;
    }

    int converged = 0;
    if (syndromes_satisfied(hard, cidx, cmsk, (int)n_checks, (int)dmax)) {
        converged = 1;
    }

    /* The decode loop touches only the read-only input arrays and private
     * scratch, so release the GIL: FEC blocks are independent and a caller may
     * run several decodes across a thread pool. */
    Py_BEGIN_ALLOW_THREADS
    for (int it = 0; it < max_iter && !converged; it++) {
        for (npy_intp c = 0; c < n_checks; c++) {
            const int32_t *idx = cidx + c * dmax;
            const uint8_t *msk = cmsk + c * dmax;
            float *Mrow = M + c * dmax;

            float min1 = INFINITY, min2 = INFINITY;
            int first = -1;
            int degree = 0;
            int parity = 0;

            for (npy_intp j = 0; j < dmax; j++) {
                if (!msk[j]) {
                    W[j] = 0.0f;
                    continue;
                }
                float w = total[idx[j]] - Mrow[j];
                W[j] = w;
                degree++;
                if (w < 0.0f) {
                    parity ^= 1;
                }
                float m = fabsf(w);
                if (m < min1) {
                    min2 = min1;
                    min1 = m;
                    first = (int)j;
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

            for (npy_intp j = 0; j < dmax; j++) {
                if (!msk[j]) {
                    Mrow[j] = 0.0f;
                    continue;
                }
                float out_mag = ((int)j == first) ? min2 : min1;
                int neg = W[j] < 0.0f;
                float sign = (parity ^ neg) ? -1.0f : 1.0f;
                Mrow[j] = out_mag * sign * alpha;
            }
        }

        for (npy_intp i = 0; i < n; i++) {
            vsum[i] = (double)chan[i];
        }
        for (npy_intp c = 0; c < n_checks; c++) {
            const int32_t *idx = cidx + c * dmax;
            const uint8_t *msk = cmsk + c * dmax;
            const float *Mrow = M + c * dmax;
            for (npy_intp j = 0; j < dmax; j++) {
                if (msk[j]) {
                    vsum[idx[j]] += (double)Mrow[j];
                }
            }
        }
        for (npy_intp i = 0; i < n; i++) {
            /* Reference: channel + bincount(...).astype(float32), i.e. the
             * edge sum is rounded to float32 before the float32 add. */
            total[i] = chan[i] + (float)vsum[i];
        }

        for (npy_intp i = 0; i < n; i++) {
            hard[i] = total[i] < 0.0f ? 1 : 0;
        }
        if (syndromes_satisfied(hard, cidx, cmsk, (int)n_checks, (int)dmax)) {
            converged = 1;
        }
    }
    Py_END_ALLOW_THREADS

    npy_intp dims[1] = {n};
    PyArrayObject *out = (PyArrayObject *)PyArray_SimpleNew(1, dims, NPY_UINT8);
    if (out == NULL) {
        free(total);
        free(M);
        free(hard);
        free(W);
        free(vsum);
        Py_DECREF(channel);
        Py_DECREF(check_idx);
        Py_DECREF(check_mask);
        Py_DECREF(edge_var);
        return NULL;
    }
    memcpy(PyArray_DATA(out), hard, (size_t)n);

    free(total);
    free(M);
    free(hard);
    free(W);
    free(vsum);
    Py_DECREF(channel);
    Py_DECREF(check_idx);
    Py_DECREF(check_mask);
    Py_DECREF(edge_var);

    PyObject *ret = Py_BuildValue("NO", out,
                                  converged ? Py_True : Py_False);
    return ret;
}

static PyMethodDef methods[] = {
    {"decode_ldpc", (PyCFunction)(void (*)(void))decode_ldpc,
     METH_VARARGS | METH_KEYWORDS,
     "Normalized min-sum LDPC decode (A/322 6.1.3)."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_ldpc",
    "C normalized min-sum LDPC decoder for ATSC 3.0 (A/322 6.1.3).",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__ldpc(void)
{
    import_array();
    return PyModule_Create(&moduledef);
}
