/*
 * _demap - max-log NUC/QAM demapper (ATSC A/322 6.3.3) as a CPython extension.
 *
 * The reference atsc3lib.nuc.demap_llr forms a full (N, M) distance matrix and
 * takes masked minima in NumPy; for 256QAM (M=256) that dominates a PLP-1
 * frame (~20 s).  This kernel computes the same max-log LLRs in one pass per
 * cell with no large temporaries:
 *
 *   d2[j]   = |z_i - pts_j|^2
 *   sigma2  = max(mean_i min_j d2, 1e-9)
 *   llr[i,b] = (min_{j: y_b=1} d2 - min_{j: y_b=0} d2) / sigma2
 *
 * with y0 the MSB of the point label (A/322 6.3.3, q-stream order).  Convention
 * matches the reference: llr > 0 means bit 0.  The label of point ``j`` is its
 * index, so no mask table is needed.
 *
 * demap_llr(cells, points, mod_order) -> numpy float64[N * mod_order]
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <math.h>
#include <stdlib.h>

static PyObject *demap_llr(PyObject *self, PyObject *args, PyObject *kwds)
{
    (void)self;
    static char *kwlist[] = {"cells", "points", "mod_order", "block", NULL};
    PyObject *cells_o, *points_o;
    int mod_order;
    int block = 0;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "OOi|i", kwlist,
                                     &cells_o, &points_o, &mod_order,
                                     &block)) {
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
        PyErr_SetString(PyExc_ValueError,
                        "points length must be 2**mod_order");
        return NULL;
    }
    if (n == 0) {
        Py_DECREF(cells);
        Py_DECREF(pts);
        npy_intp dims[1] = {0};
        return PyArray_SimpleNew(1, dims, NPY_FLOAT64);
    }

    const npy_complex128 *z = (const npy_complex128 *)PyArray_DATA(cells);
    const npy_complex128 *p = (const npy_complex128 *)PyArray_DATA(pts);

    /* Scratch: the per-bit minima are kept per cell so the LLRs can be divided
     * by sigma2 after the whole block has been seen.  The old two-pass form
     * recomputed every distance to get sigma2; the first pass below does both
     * jobs at once.  ``block`` (0 = whole array) resets the sigma2 accumulator
     * every ``block`` cells, so one call over many concatenated FEC blocks
     * reproduces the per-block scale of separate calls. */
    const npy_intp blk = (block > 0 && (npy_intp)block < n) ? (npy_intp)block : n;
    const npy_intp nblk = (n + blk - 1) / blk;
    double *min1 = malloc((size_t)mod_order * sizeof(double));
    double *min0 = malloc((size_t)mod_order * sizeof(double));
    double *diff = malloc((size_t)n * (size_t)mod_order * sizeof(double));
    double *sig = malloc((size_t)nblk * sizeof(double));
    npy_intp dims[1] = {n * mod_order};
    PyArrayObject *out = (PyArrayObject *)PyArray_SimpleNew(1, dims,
                                                            NPY_FLOAT64);
    if (!min1 || !min0 || !diff || !sig || out == NULL) {
        free(min1);
        free(min0);
        free(diff);
        free(sig);
        Py_XDECREF(out);
        Py_DECREF(cells);
        Py_DECREF(pts);
        return PyErr_NoMemory();
    }
    double *llr = (double *)PyArray_DATA(out);

    /* Only read-only inputs and private scratch are touched, so release the
     * GIL: demapping a frame's blocks can run across a thread pool. */
    Py_BEGIN_ALLOW_THREADS
    /* Single distance pass: per-cell per-bit minima plus the running sum of the
     * nearest-point distance (sigma2 = max(mean_i min_j |z_i-p_j|^2, 1e-9)).
     * ``best`` is the min over every point, which equals the min over the bits
     * of min(min1, min0) because for each bit the two label sets partition the
     * constellation; the selection is exact, so the value is bit-identical to
     * the reference's np.abs(z-p)**2 min. */
    double sum_min = 0.0;
    for (npy_intp i = 0; i < n; i++) {
        double re = creal(z[i]), im = cimag(z[i]);
        for (int b = 0; b < mod_order; b++) {
            min1[b] = INFINITY;
            min0[b] = INFINITY;
        }
        for (npy_intp j = 0; j < m; j++) {
            double dr = re - creal(p[j]);
            double di = im - cimag(p[j]);
            double d = dr * dr + di * di;
            for (int b = 0; b < mod_order; b++) {
                /* y0 is the MSB of the label. */
                if ((j >> (mod_order - 1 - b)) & 1) {
                    if (d < min1[b]) {
                        min1[b] = d;
                    }
                } else {
                    if (d < min0[b]) {
                        min0[b] = d;
                    }
                }
            }
        }
        double best = INFINITY;
        for (int b = 0; b < mod_order; b++) {
            if (min1[b] < best) {
                best = min1[b];
            }
            if (min0[b] < best) {
                best = min0[b];
            }
            diff[i * mod_order + b] = min1[b] - min0[b];
        }
        sum_min += best;
        /* Close out this block's scale at its last cell (and at the end). */
        if ((i + 1) % blk == 0 || i == n - 1) {
            const npy_intp cnt = (i % blk) + 1;
            const double s = sum_min / (double)cnt;
            sig[i / blk] = (s < 1e-9) ? 1e-9 : s;
            sum_min = 0.0;
        }
    }

    /* Second loop: apply each block's own scale; no distances recomputed.
     * Output index o = cell*mod_order + b, so o/(blk*mod_order) is the cell's
     * block index. */
    const npy_intp nout = n * mod_order;
    for (npy_intp i = 0; i < nout; i++) {
        llr[i] = diff[i] / sig[i / (blk * mod_order)];
    }
    Py_END_ALLOW_THREADS

    free(min1);
    free(min0);
    free(diff);
    free(sig);
    Py_DECREF(cells);
    Py_DECREF(pts);
    return (PyObject *)out;
}

static PyMethodDef methods[] = {
    {"demap_llr", (PyCFunction)(void (*)(void))demap_llr,
     METH_VARARGS | METH_KEYWORDS,
     "Max-log NUC/QAM LLRs (A/322 6.3.3); block resets sigma2 per N cells."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_demap",
    "C max-log demapper for ATSC 3.0 (A/322 6.3.3).",
    -1,
    methods,
};

PyMODINIT_FUNC PyInit__demap(void)
{
    import_array();
    return PyModule_Create(&moduledef);
}
