/* Shared CUDA/Python glue for the optional accelerators.
 *
 * Both translation units include this after Python.h, numpy/arrayobject.h and
 * cuda_runtime.h.  The helpers release the GIL around blocking CUDA calls so a
 * live loop can build the next frame's pool while this one runs on the GPU.
 */
#ifndef ATSC3_GPU_CUDA_UTIL_CUH
#define ATSC3_GPU_CUDA_UTIL_CUH

static int _cuda_ok(cudaError_t err, const char *what)
{
    if (err != cudaSuccess) {
        PyErr_Format(PyExc_RuntimeError, "%s: %s", what,
                     cudaGetErrorString(err));
        return 0;
    }
    return 1;
}

static int _copy_h2d(void *dst, const void *src, size_t n, const char *what)
{
    cudaError_t err;
    Py_BEGIN_ALLOW_THREADS
    err = cudaMemcpy(dst, src, n, cudaMemcpyHostToDevice);
    Py_END_ALLOW_THREADS
    return _cuda_ok(err, what);
}

static int _copy_d2h(void *dst, const void *src, size_t n, const char *what)
{
    cudaError_t err;
    Py_BEGIN_ALLOW_THREADS
    err = cudaMemcpy(dst, src, n, cudaMemcpyDeviceToHost);
    Py_END_ALLOW_THREADS
    return _cuda_ok(err, what);
}

static int _sync_device(const char *what)
{
    cudaError_t err;
    Py_BEGIN_ALLOW_THREADS
    err = cudaDeviceSynchronize();
    Py_END_ALLOW_THREADS
    return _cuda_ok(err, what);
}

#endif
