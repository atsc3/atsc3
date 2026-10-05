/*
 * _bch - ATSC 3.0 BCH outer-code decoder (A/322 6.1.2.1) as a CPython extension.
 *
 * The reference atsc3lib.bch.BCHCode.decode is pure Python (GF(2^m) tables,
 * Berlekamp-Massey, Chien search).  It holds the GIL and does not parallelise,
 * so on a 256QAM frame it is the largest serial cost (~3 s over 117 blocks).
 * This kernel runs the same algorithm in C and releases the GIL.
 *
 * decode_bits(rx, mouter, t, exp, log, order, kpayload)
 *     -> (numpy uint8[kpayload] corrected message, int nerr, bool ok)
 *
 * ``rx`` is the Nouter-bit shortened codeword, MSB first, as ints 0/1; ``exp``
 * and ``log`` are the GF(2^m) tables the caller already built (so the field is
 * defined in one place, atsc3lib.bch).  Semantics match the reference exactly,
 * including the fail conditions.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define BCH_MAX_T 32

static inline int gf_mul(const int32_t *exp, const int32_t *log, long order,
                         int a, int b)
{
    if (a == 0 || b == 0) {
        return 0;
    }
    return exp[(log[a] + log[b]) % order];
}

static inline int gf_inv(const int32_t *exp, const int32_t *log, long order,
                         int a)
{
    return exp[(order - log[a]) % order];
}

/* Syndromes S[1..2t]: S[j] = XOR_{i: rx[i]=1} alpha^(j*(n-1-i)).

   The exponent for a fixed j decreases by j each step (i -> i+1), so it is
   carried and decremented with one conditional instead of a modulo per bit. */
static void syndromes(const uint8_t *rx, int n, int t,
                      const int32_t *exp, long order, int32_t *synd)
{
    for (int j = 1; j <= 2 * t; j++) {
        long e = ((long)j * (long)(n - 1)) % order;
        long step = j % order;
        int acc = 0;
        for (int i = 0; i < n; i++) {
            if (rx[i]) {
                acc ^= exp[e];
            }
            e -= step;
            if (e < 0) {
                e += order;
            }
        }
        synd[j] = acc;
    }
}

/* Berlekamp-Massey; returns locator length (degree) and fills c[]. */
static int berlekamp_massey(const int32_t *synd, int t,
                            const int32_t *exp, const int32_t *log,
                            long order, int *c_out)
{
    int c[BCH_MAX_T + 2], b_poly[BCH_MAX_T + 2], tmp[BCH_MAX_T + 2];
    int c_len = 1, b_len = 1;
    c[0] = 1;
    b_poly[0] = 1;
    int L = 0, m = 1, b = 1;

    for (int nn = 0; nn < 2 * t; nn++) {
        int d = synd[nn + 1];
        for (int i = 1; i <= L; i++) {
            if (i < c_len) {
                d ^= gf_mul(exp, log, order, c[i], synd[nn + 1 - i]);
            }
        }
        if (d == 0) {
            m += 1;
        } else if (2 * L <= nn) {
            memcpy(tmp, c, sizeof(int) * c_len);
            int tmp_len = c_len;
            int coef = gf_mul(exp, log, order, d, gf_inv(exp, log, order, b));
            for (int i = 0; i < b_len; i++) {
                int idx = i + m;
                if (idx >= c_len) {
                    for (int k = c_len; k <= idx; k++) {
                        c[k] = 0;
                    }
                    c_len = idx + 1;
                }
                c[idx] ^= gf_mul(exp, log, order, coef, b_poly[i]);
            }
            L = nn + 1 - L;
            memcpy(b_poly, tmp, sizeof(int) * tmp_len);
            b_len = tmp_len;
            b = d;
            m = 1;
        } else {
            int coef = gf_mul(exp, log, order, d, gf_inv(exp, log, order, b));
            for (int i = 0; i < b_len; i++) {
                int idx = i + m;
                if (idx >= c_len) {
                    for (int k = c_len; k <= idx; k++) {
                        c[k] = 0;
                    }
                    c_len = idx + 1;
                }
                c[idx] ^= gf_mul(exp, log, order, coef, b_poly[i]);
            }
            m += 1;
        }
    }

    if (c_len - 1 != L) {
        return -1;
    }
    for (int i = 0; i < c_len; i++) {
        c_out[i] = c[i];
    }
    return c_len;
}

/* Chien search: positions p in [0,n) with locator value 0. */
static int chien_search(const int *sigma, int sigma_len, int n, int t,
                        const int32_t *exp, const int32_t *log, long order,
                        int *positions)
{
    int npos = 0;
    for (int p = 0; p < n; p++) {
        int location = n - 1 - p;
        /* Horner over the reversed locator: reference iterates sigma_rev in
         * order with value = (value*x_inv) ^ coeff, where x_inv = alpha^(-p).
         * Track value's exponent so each multiply is a table lookup, no
         * per-term modulo. */
        int acc = 0;
        long acc_log = 0;
        int has = 0;
        for (int i = 0; i < sigma_len; i++) {
            int coeff = sigma[sigma_len - 1 - i];
            int prod = 0;
            if (has) {
                long e = acc_log - (long)location;   /* log(x_inv) = -location */
                if (e < 0) {
                    e += order;
                }
                prod = exp[e];
            }
            acc = prod ^ coeff;
            if (acc != 0) {
                acc_log = log[acc];
                has = 1;
            } else {
                has = 0;
            }
        }
        if (acc == 0) {
            if (npos >= t + 1) {
                return -1;   /* more roots than the code can correct */
            }
            positions[npos++] = p;
        }
    }
    return npos;
}

static PyObject *decode_bits(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"rx", "mouter", "t", "exp", "log", "order",
                             "kpayload", NULL};
    PyObject *rx_o, *exp_o, *log_o;
    int mouter, t, kpayload;
    long order;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OiiOOli", kwlist,
                                     &rx_o, &mouter, &t, &exp_o, &log_o,
                                     &order, &kpayload)) {
        return NULL;
    }
    if (t < 1 || t > BCH_MAX_T) {
        PyErr_SetString(PyExc_ValueError, "t out of range");
        return NULL;
    }

    PyArrayObject *rx_a = (PyArrayObject *)PyArray_FROM_OTF(
        rx_o, NPY_UINT8, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *exp_a = (PyArrayObject *)PyArray_FROM_OTF(
        exp_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *log_a = (PyArrayObject *)PyArray_FROM_OTF(
        log_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (rx_a == NULL || exp_a == NULL || log_a == NULL) {
        Py_XDECREF(rx_a);
        Py_XDECREF(exp_a);
        Py_XDECREF(log_a);
        return NULL;
    }

    const int n = (int)PyArray_SIZE(rx_a);
    const int32_t *exp = (const int32_t *)PyArray_DATA(exp_a);
    const int32_t *log = (const int32_t *)PyArray_DATA(log_a);

    if (kpayload <= 0 || kpayload != n - mouter) {
        Py_DECREF(rx_a);
        Py_DECREF(exp_a);
        Py_DECREF(log_a);
        PyErr_SetString(PyExc_ValueError, "kpayload != n - mouter");
        return NULL;
    }

    uint8_t *corrected = malloc((size_t)n);
    if (!corrected) {
        Py_DECREF(rx_a);
        Py_DECREF(exp_a);
        Py_DECREF(log_a);
        return PyErr_NoMemory();
    }

    int nerr = 0;
    int ok = 0;

    Py_BEGIN_ALLOW_THREADS
    {
        const uint8_t *rx = (const uint8_t *)PyArray_DATA(rx_a);
        int32_t synd[BCH_MAX_T * 2 + 1];
        syndromes(rx, n, t, exp, order, synd);

        int all_zero = 1;
        for (int j = 1; j <= 2 * t; j++) {
            if (synd[j] != 0) {
                all_zero = 0;
                break;
            }
        }
        if (all_zero) {
            memcpy(corrected, rx, (size_t)kpayload);
            nerr = 0;
            ok = 1;
        } else {
            int sigma[BCH_MAX_T + 2];
            int sigma_len = berlekamp_massey(synd, t, exp, log, order, sigma);
            if (sigma_len > 0) {
                int positions[BCH_MAX_T + 1];
                int npos = chien_search(sigma, sigma_len, n, t, exp, log,
                                        order, positions);
                if (npos > 0 && npos <= t) {
                    memcpy(corrected, rx, (size_t)n);
                    for (int k = 0; k < npos; k++) {
                        corrected[positions[k]] ^= 1;
                    }
                    int32_t check[BCH_MAX_T * 2 + 1];
                    syndromes(corrected, n, t, exp, order, check);
                    int check_ok = 1;
                    for (int j = 1; j <= 2 * t; j++) {
                        if (check[j] != 0) {
                            check_ok = 0;
                            break;
                        }
                    }
                    if (check_ok) {
                        nerr = npos;
                        ok = 1;
                    }
                }
            }
        }
    }
    Py_END_ALLOW_THREADS

    if (!ok) {
        memcpy(corrected, (const uint8_t *)PyArray_DATA(rx_a),
               (size_t)kpayload);
        nerr = 0;
    }

    npy_intp dims[1] = {kpayload};
    PyArrayObject *out = (PyArrayObject *)PyArray_SimpleNew(1, dims,
                                                            NPY_UINT8);
    if (out == NULL) {
        free(corrected);
        Py_DECREF(rx_a);
        Py_DECREF(exp_a);
        Py_DECREF(log_a);
        return NULL;
    }
    memcpy(PyArray_DATA(out), corrected, (size_t)kpayload);
    free(corrected);
    Py_DECREF(rx_a);
    Py_DECREF(exp_a);
    Py_DECREF(log_a);

    return Py_BuildValue("NiO", out, nerr, ok ? Py_True : Py_False);
}

static PyMethodDef methods[] = {
    {"decode_bits", (PyCFunction)(void (*)(void))decode_bits,
     METH_VARARGS | METH_KEYWORDS,
     "BCH decode of a shortened codeword (A/322 6.1.2.1)."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_bch",
    "C BCH outer-code decoder for ATSC 3.0 (A/322 6.1.2.1).",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__bch(void)
{
    import_array();
    return PyModule_Create(&moduledef);
}
