/*
 * _core - CPython bindings for the SoapySDR C API (SDRplay RSP1B).
 *
 * This replaces the standalone tools/soapy_capture helper: instead of
 * exec'ing a C program, Python opens the device, configures the front end and
 * streams IQ samples directly through a Device object.
 *
 * Only the SoapySDR C API (SoapySDR/Device.h) is used, not the C++ API or the
 * SWIG Python bindings.  The module exposes:
 *
 *   enumerate(driver=None)  -> list[dict]   device argument dictionaries
 *   probe(driver="sdrplay") -> bool
 *   format_to_size(fmt)     -> int          bytes per stream element
 *
 *   Device(driver="sdrplay", index=0, args=None)
 *       .set_sample_rate(rate, direction="RX", channel=0)
 *       .set_frequency(hz, ...)
 *       .set_bandwidth(hz, ...)
 *       .set_antenna(name, ...)
 *       .set_gain_element(name, value, ...)
 *       .set_gain(value, ...)
 *       .get_gain_element_range(name, ...) -> (min, max, step)
 *       .has_gain_mode(...) / .set_gain_mode(automatic, ...)
 *       .setup_stream(format="CS16", direction="RX", channels=None)
 *       .get_stream_mtu() -> int
 *       .activate_stream(flags=0, time_ns=0, num_elems=0)
 *       .deactivate_stream(flags=0, time_ns=0)
 *       .read(num_elems=None, timeout_us=1000000) -> bytes
 *       .close()
 *       .info -> dict
 *
 * Direction is "RX" (SOAPY_SDR_RX) or "TX" (SOAPY_SDR_TX).
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <SoapySDR/Constants.h>
#include <SoapySDR/Device.h>
#include <SoapySDR/Errors.h>
#include <SoapySDR/Formats.h>
#include <SoapySDR/Types.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define SDRBINDINGS_DEFAULT_DRIVER "sdrplay"
#define SDRBINDINGS_DEFAULT_FORMAT SOAPY_SDR_CS16
#define SDRBINDINGS_DEFAULT_TIMEOUT_US 1000000L

/* ------------------------------------------------------------------ */
/* helpers                                                            */
/* ------------------------------------------------------------------ */

static SoapySDRKwargs *enumerate_driver(const char *driver, size_t *length)
{
    if (driver == NULL || driver[0] == '\0') {
        return SoapySDRDevice_enumerate(NULL, length);
    }
    char markup[256];
    if (snprintf(markup, sizeof(markup), "driver=%s", driver)
        >= (int)sizeof(markup)) {
        *length = 0;
        return NULL;
    }
    return SoapySDRDevice_enumerateStrArgs(markup, length);
}

static PyObject *kwargs_to_dict(const SoapySDRKwargs *args)
{
    PyObject *dict = PyDict_New();
    if (dict == NULL)
        return NULL;
    for (size_t i = 0; i < args->size; i++) {
        PyObject *val = PyUnicode_FromString(args->vals[i]);
        if (val == NULL || PyDict_SetItemString(dict, args->keys[i], val) < 0) {
            Py_XDECREF(val);
            Py_DECREF(dict);
            return NULL;
        }
        Py_DECREF(val);
    }
    return dict;
}

static int parse_direction(PyObject *obj, int *direction)
{
    if (obj == NULL || obj == Py_None) {
        *direction = SOAPY_SDR_RX;
        return 0;
    }
    if (PyLong_Check(obj)) {
        *direction = (int)PyLong_AsLong(obj);
        return 0;
    }
    if (PyUnicode_Check(obj)) {
        const char *s = PyUnicode_AsUTF8(obj);
        if (s == NULL)
            return -1;
        if (strcmp(s, "RX") == 0 || strcmp(s, "rx") == 0
            || strcmp(s, "SOAPY_SDR_RX") == 0) {
            *direction = SOAPY_SDR_RX;
            return 0;
        }
        if (strcmp(s, "TX") == 0 || strcmp(s, "tx") == 0
            || strcmp(s, "SOAPY_SDR_TX") == 0) {
            *direction = SOAPY_SDR_TX;
            return 0;
        }
    }
    PyErr_SetString(PyExc_ValueError, "direction must be \"RX\" or \"TX\"");
    return -1;
}

static void set_soapy_error(const char *what, int code)
{
    PyErr_Format(PyExc_RuntimeError, "%s failed: %s (%d)",
                 what, SoapySDR_errToStr(code), code);
}

/* ------------------------------------------------------------------ */
/* Device type                                                        */
/* ------------------------------------------------------------------ */

typedef struct {
    PyObject_HEAD
    SoapySDRDevice *dev;
    SoapySDRStream *stream;
    size_t elem_bytes;
    size_t mtu;
    char *markup;
} DeviceObject;

static void Device_close_stream(DeviceObject *self)
{
    if (self->stream != NULL) {
        SoapySDRDevice_deactivateStream(self->dev, self->stream, 0, 0);
        SoapySDRDevice_closeStream(self->dev, self->stream);
        self->stream = NULL;
    }
}

static void Device_dealloc(DeviceObject *self)
{
    if (self->dev != NULL) {
        Device_close_stream(self);
        SoapySDRDevice_unmake(self->dev);
        self->dev = NULL;
    }
    if (self->markup != NULL) {
        SoapySDR_free(self->markup);
        self->markup = NULL;
    }
    Py_TYPE(self)->tp_free((PyObject *)self);
}

static int Device_init(DeviceObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"driver", "index", "args", NULL};
    const char *driver = SDRBINDINGS_DEFAULT_DRIVER;
    int index = 0;
    PyObject *extra = NULL;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|siO", kwlist,
                                     &driver, &index, &extra))
        return -1;

    self->dev = NULL;
    self->stream = NULL;
    self->elem_bytes = 0;
    self->mtu = 0;
    self->markup = NULL;

    if (extra != NULL && extra != Py_None) {
        if (PyUnicode_Check(extra)) {
            self->markup = strdup(PyUnicode_AsUTF8(extra));
        } else if (PyDict_Check(extra)) {
            PyObject *items = PyDict_Items(extra);
            PyObject *sep = PyUnicode_FromString(",");
            PyObject *parts = PyList_New(0);
            if (items == NULL || sep == NULL || parts == NULL) {
                Py_XDECREF(items);
                Py_XDECREF(sep);
                Py_XDECREF(parts);
                return -1;
            }
            for (Py_ssize_t i = 0; i < PyList_GET_SIZE(items); i++) {
                PyObject *pair = PyList_GET_ITEM(items, i);
                PyObject *piece = PyUnicode_FromFormat(
                    "%S=%S", PyTuple_GET_ITEM(pair, 0),
                    PyTuple_GET_ITEM(pair, 1));
                if (piece == NULL || PyList_Append(parts, piece) < 0) {
                    Py_XDECREF(piece);
                    Py_DECREF(items);
                    Py_DECREF(sep);
                    Py_DECREF(parts);
                    return -1;
                }
                Py_DECREF(piece);
            }
            PyObject *joined = PyUnicode_Join(sep, parts);
            Py_DECREF(items);
            Py_DECREF(sep);
            Py_DECREF(parts);
            if (joined == NULL)
                return -1;
            self->markup = strdup(PyUnicode_AsUTF8(joined));
            Py_DECREF(joined);
        } else {
            PyErr_SetString(PyExc_TypeError, "args must be a str or dict");
            return -1;
        }
        if (self->markup == NULL) {
            PyErr_NoMemory();
            return -1;
        }
    } else {
        size_t length = 0;
        SoapySDRKwargs *results = enumerate_driver(driver, &length);
        if (length == 0 || index < 0 || (size_t)index >= length) {
            if (results != NULL)
                SoapySDRKwargsList_clear(results, length);
            PyErr_Format(PyExc_RuntimeError,
                         "no %s device at index %d", driver, index);
            return -1;
        }
        self->markup = SoapySDRKwargs_toString(&results[index]);
        SoapySDRKwargsList_clear(results, length);
        if (self->markup == NULL) {
            PyErr_SetString(PyExc_RuntimeError, "failed to read device args");
            return -1;
        }
    }

    self->dev = SoapySDRDevice_makeStrArgs(self->markup);
    if (self->dev == NULL) {
        PyErr_Format(PyExc_RuntimeError, "cannot open device (%s)",
                     self->markup);
        return -1;
    }
    return 0;
}

static PyObject *Device_get_info(DeviceObject *self, void *closure)
{
    (void)closure;
    SoapySDRKwargs args = SoapySDRKwargs_fromString(self->markup);
    PyObject *dict = kwargs_to_dict(&args);
    SoapySDRKwargs_clear(&args);
    return dict;
}

static PyObject *Device_set_sample_rate(DeviceObject *self, PyObject *args,
                                        PyObject *kwds)
{
    static char *kwlist[] = {"rate", "direction", "channel", NULL};
    double rate;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "d|On", kwlist,
                                     &rate, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setSampleRate(self->dev, direction,
                                          (size_t)channel, rate);
    if (rc != 0) {
        set_soapy_error("setSampleRate", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_set_frequency(DeviceObject *self, PyObject *args,
                                      PyObject *kwds)
{
    static char *kwlist[] = {"frequency", "direction", "channel", NULL};
    double freq;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "d|On", kwlist,
                                     &freq, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setFrequency(self->dev, direction,
                                         (size_t)channel, freq, NULL);
    if (rc != 0) {
        set_soapy_error("setFrequency", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_set_bandwidth(DeviceObject *self, PyObject *args,
                                      PyObject *kwds)
{
    static char *kwlist[] = {"bandwidth", "direction", "channel", NULL};
    double bw;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "d|On", kwlist,
                                     &bw, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setBandwidth(self->dev, direction,
                                         (size_t)channel, bw);
    if (rc != 0) {
        set_soapy_error("setBandwidth", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_set_antenna(DeviceObject *self, PyObject *args,
                                    PyObject *kwds)
{
    static char *kwlist[] = {"name", "direction", "channel", NULL};
    const char *name;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|On", kwlist,
                                     &name, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setAntenna(self->dev, direction,
                                       (size_t)channel, name);
    if (rc != 0) {
        set_soapy_error("setAntenna", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_set_gain_element(DeviceObject *self, PyObject *args,
                                         PyObject *kwds)
{
    static char *kwlist[] = {"name", "value", "direction", "channel", NULL};
    const char *name;
    double value;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "sd|On", kwlist,
                                     &name, &value, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setGainElement(self->dev, direction,
                                           (size_t)channel, name, value);
    if (rc != 0) {
        set_soapy_error("setGain(RFGR/IFGR)", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_set_gain(DeviceObject *self, PyObject *args,
                                 PyObject *kwds)
{
    static char *kwlist[] = {"value", "direction", "channel", NULL};
    double value;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "d|On", kwlist,
                                     &value, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setGain(self->dev, direction,
                                    (size_t)channel, value);
    if (rc != 0) {
        set_soapy_error("setGain", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_get_gain_element_range(DeviceObject *self,
                                               PyObject *args,
                                               PyObject *kwds)
{
    static char *kwlist[] = {"name", "direction", "channel", NULL};
    const char *name;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "s|On", kwlist,
                                     &name, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    SoapySDRRange range = SoapySDRDevice_getGainElementRange(
        self->dev, direction, (size_t)channel, name);
    return Py_BuildValue("(ddd)", range.minimum, range.maximum, range.step);
}

static PyObject *Device_has_gain_mode(DeviceObject *self, PyObject *args,
                                      PyObject *kwds)
{
    static char *kwlist[] = {"direction", "channel", NULL};
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|On", kwlist,
                                     &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    if (SoapySDRDevice_hasGainMode(self->dev, direction, (size_t)channel))
        Py_RETURN_TRUE;
    Py_RETURN_FALSE;
}

static PyObject *Device_set_gain_mode(DeviceObject *self, PyObject *args,
                                      PyObject *kwds)
{
    static char *kwlist[] = {"automatic", "direction", "channel", NULL};
    int automatic;
    PyObject *dir_obj = NULL;
    Py_ssize_t channel = 0;
    int direction;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "p|On", kwlist,
                                     &automatic, &dir_obj, &channel))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;
    int rc = SoapySDRDevice_setGainMode(self->dev, direction,
                                        (size_t)channel, automatic != 0);
    if (rc != 0) {
        set_soapy_error("setGainMode", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_setup_stream(DeviceObject *self, PyObject *args,
                                     PyObject *kwds)
{
    static char *kwlist[] = {"format", "direction", "channels", NULL};
    const char *format = SDRBINDINGS_DEFAULT_FORMAT;
    PyObject *dir_obj = NULL;
    PyObject *channels = NULL;
    int direction;

    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|sOO", kwlist,
                                     &format, &dir_obj, &channels))
        return NULL;
    if (parse_direction(dir_obj, &direction) < 0)
        return NULL;

    size_t chan_array[1];
    size_t num_chans = 0;
    const size_t *chan_ptr = NULL;
    if (channels != NULL && channels != Py_None) {
        PyObject *seq = PySequence_Fast(channels, "channels must be a sequence");
        if (seq == NULL)
            return NULL;
        Py_ssize_t n = PySequence_Fast_GET_SIZE(seq);
        if (n < 1 || n > 1) {
            Py_DECREF(seq);
            PyErr_SetString(PyExc_ValueError,
                            "only a single channel is supported");
            return NULL;
        }
        chan_array[0] = (size_t)PyLong_AsSsize_t(
            PySequence_Fast_GET_ITEM(seq, 0));
        num_chans = 1;
        chan_ptr = chan_array;
        Py_DECREF(seq);
    }

    if (self->stream != NULL)
        Device_close_stream(self);

    self->stream = SoapySDRDevice_setupStream(self->dev, direction, format,
                                              chan_ptr, num_chans, NULL);
    if (self->stream == NULL) {
        PyErr_Format(PyExc_RuntimeError, "setupStream(%s) failed", format);
        return NULL;
    }
    self->elem_bytes = SoapySDR_formatToSize(format);
    self->mtu = SoapySDRDevice_getStreamMTU(self->dev, self->stream);
    if (self->elem_bytes == 0 || self->mtu == 0) {
        PyErr_SetString(PyExc_RuntimeError, "invalid stream geometry");
        Device_close_stream(self);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_get_stream_mtu(DeviceObject *self, PyObject *Py_UNUSED(ignored))
{
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "no stream configured");
        return NULL;
    }
    return PyLong_FromSize_t(self->mtu);
}

static PyObject *Device_activate_stream(DeviceObject *self, PyObject *args,
                                        PyObject *kwds)
{
    static char *kwlist[] = {"flags", "time_ns", "num_elems", NULL};
    int flags = 0;
    long long time_ns = 0;
    Py_ssize_t num_elems = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|iLn", kwlist,
                                     &flags, &time_ns, &num_elems))
        return NULL;
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "no stream configured");
        return NULL;
    }
    int rc = SoapySDRDevice_activateStream(self->dev, self->stream, flags,
                                           time_ns, (size_t)num_elems);
    if (rc != 0) {
        set_soapy_error("activateStream", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_deactivate_stream(DeviceObject *self, PyObject *args,
                                          PyObject *kwds)
{
    static char *kwlist[] = {"flags", "time_ns", NULL};
    int flags = 0;
    long long time_ns = 0;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|iL", kwlist,
                                     &flags, &time_ns))
        return NULL;
    if (self->stream == NULL)
        Py_RETURN_NONE;
    int rc = SoapySDRDevice_deactivateStream(self->dev, self->stream, flags,
                                             time_ns);
    if (rc != 0) {
        set_soapy_error("deactivateStream", rc);
        return NULL;
    }
    Py_RETURN_NONE;
}

static PyObject *Device_read(DeviceObject *self, PyObject *args, PyObject *kwds)
{
    static char *kwlist[] = {"num_elems", "timeout_us", NULL};
    Py_ssize_t num_elems = 0;
    long timeout_us = SDRBINDINGS_DEFAULT_TIMEOUT_US;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|nl", kwlist,
                                     &num_elems, &timeout_us))
        return NULL;
    if (self->stream == NULL) {
        PyErr_SetString(PyExc_RuntimeError, "no stream configured");
        return NULL;
    }
    size_t elems = num_elems > 0 ? (size_t)num_elems : self->mtu;
    if (elems > self->mtu)
        elems = self->mtu;

    size_t bytes = elems * self->elem_bytes;
    void *buf = malloc(bytes);
    if (buf == NULL)
        return PyErr_NoMemory();

    int flags = 0;
    long long time_ns = 0;
    void *buffs[1] = {buf};
    int n;
    Py_BEGIN_ALLOW_THREADS
    n = SoapySDRDevice_readStream(self->dev, self->stream, buffs, elems,
                                  &flags, &time_ns, timeout_us);
    Py_END_ALLOW_THREADS

    if (n < 0) {
        free(buf);
        if (n == SOAPY_SDR_TIMEOUT) {
            Py_RETURN_NONE;
        }
        if (n == SOAPY_SDR_OVERFLOW) {
            PyErr_SetString(PyExc_BufferError, "SOAPY_SDR_OVERFLOW");
            return NULL;
        }
        set_soapy_error("readStream", n);
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(buf, (Py_ssize_t)n * self->elem_bytes);
    free(buf);
    return out;
}

static PyObject *Device_close(DeviceObject *self, PyObject *Py_UNUSED(ignored))
{
    Device_close_stream(self);
    Py_RETURN_NONE;
}

static PyObject *Device_enter(DeviceObject *self, PyObject *Py_UNUSED(ignored))
{
    Py_INCREF(self);
    return (PyObject *)self;
}

static PyObject *Device_exit(DeviceObject *self, PyObject *args)
{
    (void)args;
    Device_close_stream(self);
    Py_RETURN_FALSE;
}

static PyMethodDef Device_methods[] = {
    {"set_sample_rate", (PyCFunction)(void(*)(void))Device_set_sample_rate,
     METH_VARARGS | METH_KEYWORDS, "Set the sample rate in Hz."},
    {"set_frequency", (PyCFunction)(void(*)(void))Device_set_frequency,
     METH_VARARGS | METH_KEYWORDS, "Set the center frequency in Hz."},
    {"set_bandwidth", (PyCFunction)(void(*)(void))Device_set_bandwidth,
     METH_VARARGS | METH_KEYWORDS, "Set the baseband filter bandwidth in Hz."},
    {"set_antenna", (PyCFunction)(void(*)(void))Device_set_antenna,
     METH_VARARGS | METH_KEYWORDS, "Select an antenna by name."},
    {"set_gain_element", (PyCFunction)(void(*)(void))Device_set_gain_element,
     METH_VARARGS | METH_KEYWORDS, "Set a named gain element in dB."},
    {"set_gain", (PyCFunction)(void(*)(void))Device_set_gain,
     METH_VARARGS | METH_KEYWORDS, "Set the overall gain in dB."},
    {"get_gain_element_range",
     (PyCFunction)(void(*)(void))Device_get_gain_element_range,
     METH_VARARGS | METH_KEYWORDS, "Return (min, max, step) for a gain element."},
    {"has_gain_mode", (PyCFunction)(void(*)(void))Device_has_gain_mode,
     METH_VARARGS | METH_KEYWORDS, "True when AGC can be enabled."},
    {"set_gain_mode", (PyCFunction)(void(*)(void))Device_set_gain_mode,
     METH_VARARGS | METH_KEYWORDS, "Enable/disable automatic gain control."},
    {"setup_stream", (PyCFunction)(void(*)(void))Device_setup_stream,
     METH_VARARGS | METH_KEYWORDS, "Create the RX stream in a sample format."},
    {"get_stream_mtu", (PyCFunction)Device_get_stream_mtu, METH_NOARGS,
     "Maximum elements per read."},
    {"activate_stream", (PyCFunction)(void(*)(void))Device_activate_stream,
     METH_VARARGS | METH_KEYWORDS, "Activate the stream."},
    {"deactivate_stream", (PyCFunction)(void(*)(void))Device_deactivate_stream,
     METH_VARARGS | METH_KEYWORDS, "Deactivate the stream."},
    {"read", (PyCFunction)(void(*)(void))Device_read,
     METH_VARARGS | METH_KEYWORDS,
     "Read up to num_elems; returns bytes, or None on timeout."},
    {"close", (PyCFunction)Device_close, METH_NOARGS, "Close the stream."},
    {"__enter__", (PyCFunction)Device_enter, METH_NOARGS,
     "Context manager entry."},
    {"__exit__", (PyCFunction)Device_exit, METH_VARARGS,
     "Context manager exit."},
    {NULL, NULL, 0, NULL}
};

static PyGetSetDef Device_getset[] = {
    {"info", (getter)Device_get_info, NULL,
     "Device argument dictionary.", NULL},
    {NULL, NULL, NULL, NULL, NULL}
};

static PyTypeObject DeviceType = {
    PyVarObject_HEAD_INIT(NULL, 0)
    .tp_name = "sdrbindings._core.Device",
    .tp_basicsize = sizeof(DeviceObject),
    .tp_dealloc = (destructor)Device_dealloc,
    .tp_flags = Py_TPFLAGS_DEFAULT,
    .tp_doc = "A SoapySDR device handle.",
    .tp_methods = Device_methods,
    .tp_getset = Device_getset,
    .tp_init = (initproc)Device_init,
    .tp_new = PyType_GenericNew,
    .tp_as_number = NULL,
};

/* ------------------------------------------------------------------ */
/* module-level functions                                             */
/* ------------------------------------------------------------------ */

static PyObject *module_enumerate(PyObject *module, PyObject *args,
                                  PyObject *kwds)
{
    (void)module;
    static char *kwlist[] = {"driver", NULL};
    const char *driver = NULL;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|z", kwlist, &driver))
        return NULL;

    size_t length = 0;
    SoapySDRKwargs *results = enumerate_driver(driver, &length);
    if (results == NULL)
        return PyList_New(0);

    PyObject *list = PyList_New(0);
    if (list == NULL) {
        SoapySDRKwargsList_clear(results, length);
        return NULL;
    }
    for (size_t i = 0; i < length; i++) {
        PyObject *dict = kwargs_to_dict(&results[i]);
        if (dict == NULL || PyList_Append(list, dict) < 0) {
            Py_XDECREF(dict);
            Py_DECREF(list);
            SoapySDRKwargsList_clear(results, length);
            return NULL;
        }
        Py_DECREF(dict);
    }
    SoapySDRKwargsList_clear(results, length);
    return list;
}

static PyObject *module_probe(PyObject *module, PyObject *args, PyObject *kwds)
{
    (void)module;
    static char *kwlist[] = {"driver", NULL};
    const char *driver = SDRBINDINGS_DEFAULT_DRIVER;
    if (!PyArg_ParseTupleAndKeywords(args, kwds, "|s", kwlist, &driver))
        return NULL;

    size_t length = 0;
    SoapySDRKwargs *results = enumerate_driver(driver, &length);
    if (results != NULL)
        SoapySDRKwargsList_clear(results, length);
    if (length > 0)
        Py_RETURN_TRUE;
    Py_RETURN_FALSE;
}

static PyObject *module_format_to_size(PyObject *module, PyObject *args)
{
    (void)module;
    const char *format;
    if (!PyArg_ParseTuple(args, "s", &format))
        return NULL;
    return PyLong_FromSize_t(SoapySDR_formatToSize(format));
}

static PyMethodDef module_methods[] = {
    {"enumerate", (PyCFunction)(void(*)(void))module_enumerate,
     METH_VARARGS | METH_KEYWORDS, "Enumerate devices; return arg dictionaries."},
    {"probe", (PyCFunction)(void(*)(void))module_probe,
     METH_VARARGS | METH_KEYWORDS, "True when a device is present."},
    {"format_to_size", (PyCFunction)module_format_to_size, METH_VARARGS,
     "Bytes per element for a SoapySDR format string."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef moduledef = {
    PyModuleDef_HEAD_INIT,
    "_core",
    "CPython bindings for the SoapySDR C API.",
    -1,
    module_methods,
};

PyMODINIT_FUNC PyInit__core(void)
{
    if (PyType_Ready(&DeviceType) < 0)
        return NULL;

    PyObject *module = PyModule_Create(&moduledef);
    if (module == NULL)
        return NULL;

    Py_INCREF(&DeviceType);
    if (PyModule_AddObject(module, "Device", (PyObject *)&DeviceType) < 0) {
        Py_DECREF(&DeviceType);
        Py_DECREF(module);
        return NULL;
    }

    if (PyModule_AddIntConstant(module, "RX", SOAPY_SDR_RX) < 0
        || PyModule_AddIntConstant(module, "TX", SOAPY_SDR_TX) < 0
        || PyModule_AddIntConstant(module, "TIMEOUT", SOAPY_SDR_TIMEOUT) < 0
        || PyModule_AddIntConstant(module, "OVERFLOW", SOAPY_SDR_OVERFLOW) < 0
        || PyModule_AddStringConstant(module, "CS16", SOAPY_SDR_CS16) < 0
        || PyModule_AddStringConstant(module, "CS8", SOAPY_SDR_CS8) < 0
        || PyModule_AddStringConstant(module, "CF32", SOAPY_SDR_CF32) < 0) {
        Py_DECREF(module);
        return NULL;
    }
    return module;
}
