/*
 * _fi - ATSC 3.0 frequency interleaver address generator (A/322 7.3) as a
 * CPython extension.
 *
 * The address sequence H_l(p) comes from a scalar maximal-length LFSR plus a
 * wire permutation and a per-symbol offset; it is a serial bit recurrence that
 * NumPy cannot vectorise, and in Python it costs ~25 ms per OFDM symbol
 * (a 256QAM frame has hundreds of symbols).  This kernel runs the same
 * recurrence in C.
 *
 * generate_addresses(symbol_index, n_data, pn_degree, pn_mask, max_states,
 *                    logic, logic2, bitperm, bitperm_odd)
 *     -> int64[n_data] address sequence
 *
 * The recurrence and the "accept every value < n_data" rule follow
 * atsc3lib.frequency_interleaver.generate_addresses exactly.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <stdint.h>

static PyObject *generate_addresses(PyObject *self, PyObject *args,
                                    PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"symbol_index", "n_data", "pn_degree",
                             "pn_mask", "max_states", "logic", "logic2",
                             "bitperm", "bitperm_odd", NULL};
    PyObject *logic_o, *logic2_o, *bp_o, *bpo_o;
    long long symbol_index, pn_mask, max_states;
    int n_data, pn_degree;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "LiiLLOOOO", kwlist,
                                     &symbol_index, &n_data, &pn_degree,
                                     &pn_mask, &max_states, &logic_o,
                                     &logic2_o, &bp_o, &bpo_o)) {
        return NULL;
    }
    if (n_data < 0 || pn_degree < 1 || pn_degree > 62) {
        PyErr_SetString(PyExc_ValueError, "bad n_data or pn_degree");
        return NULL;
    }

    PyArrayObject *logic = (PyArrayObject *)PyArray_FROM_OTF(
        logic_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *logic2 = (PyArrayObject *)PyArray_FROM_OTF(
        logic2_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *bp = (PyArrayObject *)PyArray_FROM_OTF(
        bp_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *bpo = (PyArrayObject *)PyArray_FROM_OTF(
        bpo_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (logic == NULL || logic2 == NULL || bp == NULL || bpo == NULL) {
        Py_XDECREF(logic);
        Py_XDECREF(logic2);
        Py_XDECREF(bp);
        Py_XDECREF(bpo);
        return NULL;
    }

    npy_intp dims[1] = {n_data};
    PyArrayObject *out = (PyArrayObject *)PyArray_SimpleNew(1, dims,
                                                            NPY_INT64);
    if (out == NULL) {
        Py_DECREF(logic);
        Py_DECREF(logic2);
        Py_DECREF(bp);
        Py_DECREF(bpo);
        return NULL;
    }
    npy_int64 *dst = (npy_int64 *)PyArray_DATA(out);

    const int32_t *L = (const int32_t *)PyArray_DATA(logic);
    const int32_t *L2 = (const int32_t *)PyArray_DATA(logic2);
    const int32_t *BP = (const int32_t *)PyArray_DATA(bp);
    const int32_t *BPO = (const int32_t *)PyArray_DATA(bpo);
    const npy_intp n_logic = PyArray_SIZE(logic);
    const npy_intp n_logic2 = PyArray_SIZE(logic2);
    const npy_intp n_bp = PyArray_SIZE(bp);
    const npy_intp n_bpo = PyArray_SIZE(bpo);

    npy_int64 count = 0;
    Py_BEGIN_ALLOW_THREADS
    /* Symbol offset generator G, pn_degree+1 bits wide, all ones to start. */
    uint64_t g_word = (1ULL << (pn_degree + 1)) - 1;
    for (long long k = 1; k <= symbol_index / 2; k++) {
        int result = 0;
        for (npy_intp t = 0; t < n_logic2; t++) {
            result ^= (int)((g_word >> L2[t]) & 1ULL);
        }
        g_word &= (1ULL << (pn_degree + 1)) - 1;
        g_word >>= 1;
        g_word |= (uint64_t)result << pn_degree;
    }

    const int use_odd = (symbol_index % 2) == 1;
    const int32_t *table = use_odd ? BPO : BP;
    const npy_intp table_len = use_odd ? n_bpo : n_bp;

    uint64_t lfsr = 0;
    for (long long j = 0; j < max_states && count < n_data; j++) {
        if (j == 0 || j == 1) {
            lfsr = 0;
        } else if (j == 2) {
            lfsr = 1;
        } else {
            int result = 0;
            for (npy_intp t = 0; t < n_logic; t++) {
                result ^= (int)((lfsr >> L[t]) & 1ULL);
            }
            lfsr &= (uint64_t)pn_mask;
            lfsr >>= 1;
            lfsr |= (uint64_t)result << (pn_degree - 1);
        }

        /* Wire permutation: out bit table[n] <- in bit n. */
        uint64_t value = 0;
        for (npy_intp n = 0; n < table_len; n++) {
            value |= ((lfsr >> n) & 1ULL) << table[n];
        }
        value += (uint64_t)(j % 2) * ((uint64_t)max_states / 2);
        value ^= g_word;
        if ((npy_int64)value < n_data) {
            dst[count++] = (npy_int64)value;
        }
    }
    Py_END_ALLOW_THREADS

    Py_DECREF(logic);
    Py_DECREF(logic2);
    Py_DECREF(bp);
    Py_DECREF(bpo);
    if (count != n_data) {
        Py_DECREF(out);
        PyErr_Format(PyExc_RuntimeError,
                     "FI generated %lld addresses, expected %d",
                     (long long)count, n_data);
        return NULL;
    }
    return (PyObject *)out;
}

static PyMethodDef methods[] = {
    {"generate_addresses",
     (PyCFunction)(void (*)(void))generate_addresses,
     METH_VARARGS | METH_KEYWORDS,
     "Frequency-interleaver address sequence (A/322 7.3)."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_fi",
    "C frequency interleaver for ATSC 3.0 (A/322 7.3).",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__fi(void)
{
    import_array();
    return PyModule_Create(&moduledef);
}
