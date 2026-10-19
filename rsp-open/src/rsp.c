#include "rsp_internal.h"

#include <errno.h>
#include <libusb-1.0/libusb.h>
#include <math.h>
#include <pthread.h>
#include <stdatomic.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

struct rsp_dev {
    libusb_context *ctx;
    libusb_device_handle *handle;
    pthread_t event_thread;
    pthread_mutex_t lock;
    pthread_cond_t cond;
    int thread_started;
    int stop;
    int running;
    int primed;

    struct libusb_transfer *urb[RSP_NURB];
    unsigned char *urb_buf[RSP_NURB];

    int16_t *ring;
    size_t ring_cap;
    _Atomic size_t ring_head;
    _Atomic size_t ring_tail;

    uint64_t overruns;
    uint64_t seq_gaps;
    uint64_t delivered;
    uint64_t partial_chunks;
    uint64_t zero_frames;
    uint64_t iso_errors;
    uint32_t next_seq;
    uint32_t last_seq;
    int have_seq;

    uint64_t prime_total;
    uint64_t prime_done;
    int64_t prime_sum_i;
    int64_t prime_sum_q;
    int16_t dc_i;
    int16_t dc_q;

    double rate_hz;
    char err[256];
    rsp_stats stats;
};

static void set_err(rsp_dev *dev, const char *msg)
{
    if (dev)
        snprintf(dev->err, sizeof(dev->err), "%s", msg);
}

static int16_t s8_to_s16(unsigned char b)
{
    int16_t v = (int16_t)(int8_t)b;
    return (int16_t)(v * 256);
}

int rsp_ctrl(rsp_dev *dev, uint8_t req, uint32_t data)
{
    int rc;

    if (!dev || !dev->handle) {
        set_err(dev, "device not open");
        return -1;
    }
    rc = libusb_control_transfer(dev->handle, 0x40, req, data & 0xffffu,
                                 (data >> 16) & 0xffffu, NULL, 0,
                                 RSP_CONTROL_TIMEOUT_MS);
    if (rc < 0) {
        snprintf(dev->err, sizeof(dev->err), "control 0x%02x%08x: %s",
                 req, data, libusb_error_name(rc));
        return -1;
    }
    return 0;
}

static int ctrl_vi(rsp_dev *dev, uint8_t req, uint16_t value, uint16_t index)
{
    int rc;

    if (!dev || !dev->handle) {
        set_err(dev, "device not open");
        return -1;
    }
    rc = libusb_control_transfer(dev->handle, 0x40, req, value, index, NULL, 0,
                                 RSP_CONTROL_TIMEOUT_MS);
    if (rc < 0) {
        snprintf(dev->err, sizeof(dev->err),
                 "control %02x v=%04x i=%04x: %s", req, value, index,
                 libusb_error_name(rc));
        return -1;
    }
    return 0;
}

static int run_table(rsp_dev *dev, const struct rsp1b_ctrl *table, size_t n)
{
    for (size_t i = 0; i < n; i++) {
        if (ctrl_vi(dev, table[i].req, table[i].value, table[i].index) < 0)
            return -1;
    }
    return 0;
}

static int run_vendor_init(rsp_dev *dev, int ifgr)
{
    struct rsp1b_ctrl table[32];

    if (rsp1b_init_vendor_len > sizeof(table) / sizeof(table[0]))
        return -1;
    memcpy(table, rsp1b_init_vendor,
           rsp1b_init_vendor_len * sizeof(table[0]));

    /* The MSi001 gain word: LNA on, mixer on, BB gain reduction = ifgr.
     * The captured 0x010281 is IFGR 40; 0x0102c1 is 44, 0x0102d1 is 45,
     * 0x010301 is 48. */
    rsp1b_gain_word(ifgr, &table[17].value, &table[17].index);

    return run_table(dev, table, rsp1b_init_vendor_len);
}

void rsp_default_config(rsp_config *cfg)
{
    cfg->freq_hz = 587e6;
    cfg->rate_hz = 10e6;
    cfg->bandwidth_hz = 8e6;
    cfg->ifgr = 44;
    cfg->rfgr = 4;
}

static void ring_reset(rsp_dev *dev)
{
    atomic_store(&dev->ring_head, 0);
    atomic_store(&dev->ring_tail, 0);
    dev->overruns = 0;
    dev->delivered = 0;
    dev->seq_gaps = 0;
    dev->partial_chunks = 0;
    dev->zero_frames = 0;
    dev->iso_errors = 0;
    dev->next_seq = 0;
    dev->last_seq = 0;
    dev->have_seq = 0;
    dev->prime_done = 0;
    dev->prime_sum_i = 0;
    dev->prime_sum_q = 0;
    dev->dc_i = 0;
    dev->dc_q = 0;
    dev->primed = 0;
}

static void ring_put(rsp_dev *dev, int16_t i, int16_t q)
{
    size_t head = atomic_load_explicit(&dev->ring_head, memory_order_relaxed);
    size_t tail = atomic_load_explicit(&dev->ring_tail, memory_order_acquire);

    if (head - tail >= dev->ring_cap) {
        dev->overruns++;
        return;
    }
    dev->ring[(head % dev->ring_cap) * 2] = i;
    dev->ring[(head % dev->ring_cap) * 2 + 1] = q;
    atomic_store_explicit(&dev->ring_head, head + 1, memory_order_release);
}

static void iso_callback(struct libusb_transfer *tr)
{
    rsp_dev *dev = (rsp_dev *)tr->user_data;
    int have_data = 0;

    if (!dev || dev->stop || tr->status == LIBUSB_TRANSFER_CANCELLED)
        return;

    if (tr->status != LIBUSB_TRANSFER_COMPLETED) {
        dev->iso_errors++;
        snprintf(dev->err, sizeof(dev->err), "isochronous status %d",
                 tr->status);
    }

    for (int f = 0; f < tr->num_iso_packets; f++) {
        int len = tr->iso_packet_desc[f].actual_length;
        unsigned char *p = libusb_get_iso_packet_buffer_simple(tr, f);
        int off = 0;

        if (len == 0) {
            dev->zero_frames++;
            continue;
        }
        while (off + 16 <= len) {
            int chunk = len - off;
            int n, j;
            uint32_t seq;

            if (chunk > 1024)
                chunk = 1024;
            if (chunk != 1024)
                dev->partial_chunks++;
            n = (chunk - 16) / 2;
            if (n <= 0)
                break;
            seq = (uint32_t)p[off] | ((uint32_t)p[off + 1] << 8) |
                  ((uint32_t)p[off + 2] << 16) |
                  ((uint32_t)p[off + 3] << 24);
            if (dev->have_seq && seq != dev->next_seq) {
                dev->seq_gaps++;
                if (dev->seq_gaps <= 8)
                    fprintf(stderr,
                            "seq gap: got %u expected %u delta %d len %d\n",
                            seq, dev->next_seq, (int32_t)(seq - dev->next_seq),
                            len);
            }
            dev->last_seq = seq;
            dev->next_seq = seq + (uint32_t)n;
            dev->have_seq = 1;

            for (j = 0; j < n; j++) {
                int16_t i16 = s8_to_s16(p[off + 16 + 2 * j]);
                int16_t q16 = s8_to_s16(p[off + 16 + 2 * j + 1]);

                if (dev->prime_done < dev->prime_total) {
                    dev->prime_sum_i += i16;
                    dev->prime_sum_q += q16;
                    dev->prime_done++;
                    if (dev->prime_done == dev->prime_total) {
                        dev->dc_i = (int16_t)(dev->prime_sum_i /
                                              (int64_t)dev->prime_total);
                        dev->dc_q = (int16_t)(dev->prime_sum_q /
                                              (int64_t)dev->prime_total);
                        pthread_mutex_lock(&dev->lock);
                        dev->primed = 1;
                        pthread_cond_broadcast(&dev->cond);
                        pthread_mutex_unlock(&dev->lock);
                    }
                } else {
                    int32_t i = (int32_t)i16 - dev->dc_i;
                    int32_t q = (int32_t)q16 - dev->dc_q;
                    if (i < -32768) i = -32768;
                    if (i > 32767) i = 32767;
                    if (q < -32768) q = -32768;
                    if (q > 32767) q = 32767;
                    ring_put(dev, (int16_t)i, (int16_t)q);
                    dev->delivered++;
                    have_data = 1;
                }
            }
            off += chunk;
        }
    }
    if (have_data) {
        pthread_mutex_lock(&dev->lock);
        pthread_cond_signal(&dev->cond);
        pthread_mutex_unlock(&dev->lock);
    }

    if (!dev->stop)
        libusb_submit_transfer(tr);
}

static void *event_thread(void *arg)
{
    rsp_dev *dev = (rsp_dev *)arg;

    while (!dev->stop) {
        struct timeval tv = {0, 100000};
        libusb_handle_events_timeout(dev->ctx, &tv);
    }
    return NULL;
}

int rsp_probe(int index, char *serial, size_t serial_len)
{
    libusb_context *ctx = NULL;
    libusb_device **list = NULL;
    ssize_t count;
    int found = 0;

    if (serial && serial_len)
        serial[0] = '\0';
    if (libusb_init(&ctx) < 0)
        return 0;
    count = libusb_get_device_list(ctx, &list);
    for (ssize_t i = 0; i < count; i++) {
        struct libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(list[i], &desc) < 0)
            continue;
        if (desc.idVendor != RSP_VID || desc.idProduct != RSP_PID_RSP1B)
            continue;
        if (index-- > 0)
            continue;
        found = 1;
        if (serial && serial_len && desc.iSerialNumber) {
            libusb_device_handle *h = NULL;
            if (libusb_open(list[i], &h) == 0) {
                libusb_get_string_descriptor_ascii(h, desc.iSerialNumber,
                                                   (unsigned char *)serial,
                                                   (int)serial_len);
                libusb_close(h);
            }
        }
        break;
    }
    if (list)
        libusb_free_device_list(list, 1);
    libusb_exit(ctx);
    return found;
}

rsp_dev *rsp_open(int index, char *err, size_t errlen)
{    rsp_dev *dev = calloc(1, sizeof(*dev));
    libusb_device **list = NULL;
    ssize_t count;
    int rc;

    if (!dev) {
        if (err) snprintf(err, errlen, "out of memory");
        return NULL;
    }
    if (libusb_init(&dev->ctx) < 0) {
        if (err) snprintf(err, errlen, "libusb_init failed");
        free(dev);
        return NULL;
    }

    count = libusb_get_device_list(dev->ctx, &list);
    for (ssize_t i = 0; i < count; i++) {
        struct libusb_device_descriptor desc;
        if (libusb_get_device_descriptor(list[i], &desc) < 0)
            continue;
        if (desc.idVendor != RSP_VID || desc.idProduct != RSP_PID_RSP1B)
            continue;
        if (index-- > 0)
            continue;
        dev->handle = NULL;
        rc = libusb_open(list[i], &dev->handle);
        if (rc < 0) {
            snprintf(dev->err, sizeof(dev->err), "open: %s",
                     libusb_error_name(rc));
            goto fail;
        }
        break;
    }
    libusb_free_device_list(list, 1);
    if (!dev->handle) {
        snprintf(dev->err, sizeof(dev->err), "no RSP1B found");
        goto fail;
    }

    libusb_set_auto_detach_kernel_driver(dev->handle, 1);
    rc = libusb_claim_interface(dev->handle, 0);
    if (rc < 0) {
        snprintf(dev->err, sizeof(dev->err),
                 "claim: %s (stop sdrplay.service and other SDR apps)",
                 libusb_error_name(rc));
        goto fail;
    }
    rc = libusb_set_interface_alt_setting(dev->handle, 0, 1);
    if (rc < 0) {
        snprintf(dev->err, sizeof(dev->err), "alt setting: %s",
                 libusb_error_name(rc));
        goto fail;
    }

    dev->ring_cap = RSP_RING_SAMPLES;
    dev->ring = calloc(dev->ring_cap * 2, sizeof(int16_t));
    if (!dev->ring) {
        snprintf(dev->err, sizeof(dev->err), "out of memory (ring)");
        goto fail;
    }
    pthread_mutex_init(&dev->lock, NULL);
    pthread_cond_init(&dev->cond, NULL);
    dev->rate_hz = 10e6;
    return dev;

fail:
    if (err) snprintf(err, errlen, "%s", dev->err);
    if (dev->handle) {
        libusb_release_interface(dev->handle, 0);
        libusb_close(dev->handle);
    }
    libusb_exit(dev->ctx);
    free(dev);
    return NULL;
}

int rsp_configure(rsp_dev *dev, const rsp_config *cfg, char *err,
                  size_t errlen)
{
    int if_gain, lna, mixer;

    if (!dev || !cfg) {
        if (err) snprintf(err, errlen, "invalid argument");
        return -1;
    }
    if (cfg->rate_hz < 1e6 || cfg->rate_hz > 12.096e6) {
        if (err) snprintf(err, errlen, "rate out of range");
        return -1;
    }
    if (cfg->freq_hz < 42e6 || cfg->freq_hz > 1000e6) {
        if (err) snprintf(err, errlen,
                          "frequency outside the validated 42-1000 MHz path");
        return -1;
    }
    if (fabs(cfg->freq_hz - 587e6) < 1e3 &&
        fabs(cfg->rate_hz - 10e6) < 1.0 &&
        fabs(cfg->bandwidth_hz - 8e6) < 1.0 &&
        cfg->ifgr >= 20 && cfg->ifgr <= 59 && cfg->rfgr == 4) {
        if (run_vendor_init(dev, cfg->ifgr) < 0) {
            if (err) snprintf(err, errlen, "%s", dev->err);
            return -1;
        }
    } else {
        if (cfg->ifgr < 0) if_gain = 59;
        else if_gain = 59 - cfg->ifgr;
        if (if_gain < 0) if_gain = 0;
        if (if_gain > 59) if_gain = 59;
        lna = cfg->rfgr >= 4 ? 0 : 1;
        mixer = 1;
        if (msi2500_set_adc(dev, cfg->rate_hz) < 0 ||
            msi001_set_tuner(dev, cfg->freq_hz, cfg->bandwidth_hz, lna,
                             mixer, if_gain) < 0) {
            if (err) snprintf(err, errlen, "%s", dev->err);
            return -1;
        }
    }

    dev->rate_hz = cfg->rate_hz;
    dev->prime_total = (uint64_t)(cfg->rate_hz * RSP_PRIME_SECONDS + 0.5);
    return 0;
}

int rsp_start(rsp_dev *dev, char *err, size_t errlen)
{
    int rc;

    if (!dev) {
        if (err) snprintf(err, errlen, "invalid argument");
        return -1;
    }
    pthread_mutex_lock(&dev->lock);
    ring_reset(dev);
    dev->stop = 0;
    pthread_mutex_unlock(&dev->lock);

    dev->thread_started = 1;
    if (pthread_create(&dev->event_thread, NULL, event_thread, dev) != 0) {
        dev->thread_started = 0;
        if (err) snprintf(err, errlen, "cannot create event thread");
        return -1;
    }

    for (int i = 0; i < RSP_NURB; i++) {
        dev->urb_buf[i] = malloc(RSP_ISO_FRAMES * RSP_ISO_FRAME_BYTES);
        dev->urb[i] = libusb_alloc_transfer(RSP_ISO_FRAMES);
        if (!dev->urb_buf[i] || !dev->urb[i]) {
            if (err) snprintf(err, errlen, "out of memory (URB)");
            return -1;
        }
        libusb_fill_iso_transfer(dev->urb[i], dev->handle, RSP_EP_IN,
                                 dev->urb_buf[i],
                                 RSP_ISO_FRAMES * RSP_ISO_FRAME_BYTES,
                                 RSP_ISO_FRAMES, iso_callback, dev, 0);
        libusb_set_iso_packet_lengths(dev->urb[i], RSP_ISO_FRAME_BYTES);
        rc = libusb_submit_transfer(dev->urb[i]);
        if (rc < 0) {
            snprintf(dev->err, sizeof(dev->err), "submit URB: %s",
                     libusb_error_name(rc));
            if (err) snprintf(err, errlen, "%s", dev->err);
            return -1;
        }
    }

    if (rsp_ctrl(dev, RSP_CMD_START, 0) < 0) {
        if (err) snprintf(err, errlen, "%s", dev->err);
        return -1;
    }
    dev->running = 1;
    return 0;
}

int rsp_read(rsp_dev *dev, int16_t *dst, size_t max_samples, int timeout_ms)
{
    struct timespec deadline;
    size_t got, head, tail, avail;

    if (!dev || !dst || max_samples == 0)
        return -1;

    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += timeout_ms / 1000;
    deadline.tv_nsec += (long)(timeout_ms % 1000) * 1000000L;
    if (deadline.tv_nsec >= 1000000000L) {
        deadline.tv_sec++;
        deadline.tv_nsec -= 1000000000L;
    }

    pthread_mutex_lock(&dev->lock);
    while (!dev->stop && !dev->primed &&
           atomic_load_explicit(&dev->ring_head, memory_order_acquire) ==
               atomic_load_explicit(&dev->ring_tail, memory_order_relaxed)) {
        if (pthread_cond_timedwait(&dev->cond, &dev->lock, &deadline) ==
            ETIMEDOUT)
            break;
    }
    while (!dev->stop &&
           atomic_load_explicit(&dev->ring_head, memory_order_acquire) ==
               atomic_load_explicit(&dev->ring_tail, memory_order_relaxed)) {
        if (pthread_cond_timedwait(&dev->cond, &dev->lock, &deadline) ==
            ETIMEDOUT)
            break;
    }
    if (dev->stop) {
        pthread_mutex_unlock(&dev->lock);
        return -1;
    }
    pthread_mutex_unlock(&dev->lock);

    head = atomic_load_explicit(&dev->ring_head, memory_order_acquire);
    tail = atomic_load_explicit(&dev->ring_tail, memory_order_relaxed);
    avail = head - tail;
    if (avail > max_samples)
        avail = max_samples;
    got = avail;
    for (size_t i = 0; i < got; i++) {
        size_t idx = (tail + i) % dev->ring_cap;
        dst[i * 2] = dev->ring[idx * 2];
        dst[i * 2 + 1] = dev->ring[idx * 2 + 1];
    }
    atomic_store_explicit(&dev->ring_tail, tail + got, memory_order_release);
    return (int)got;
}

void rsp_stop(rsp_dev *dev)
{
    if (!dev)
        return;

    pthread_mutex_lock(&dev->lock);
    dev->stop = 1;
    pthread_cond_broadcast(&dev->cond);
    pthread_mutex_unlock(&dev->lock);

    if (dev->thread_started) {
        for (int i = 0; i < RSP_NURB; i++) {
            if (dev->urb[i])
                libusb_cancel_transfer(dev->urb[i]);
        }
        pthread_join(dev->event_thread, NULL);
        dev->thread_started = 0;
    }

    if (dev->running) {
        run_table(dev, rsp1b_close_vendor, rsp1b_close_vendor_len);
        dev->running = 0;
    }
    if (dev->handle)
        libusb_set_interface_alt_setting(dev->handle, 0, 0);

    for (int i = 0; i < RSP_NURB; i++) {
        if (dev->urb[i]) {
            libusb_free_transfer(dev->urb[i]);
            dev->urb[i] = NULL;
        }
        free(dev->urb_buf[i]);
        dev->urb_buf[i] = NULL;
    }
}

void rsp_close(rsp_dev *dev)
{
    if (!dev)
        return;
    rsp_stop(dev);
    dev->stats.samples = dev->delivered;
    dev->stats.overruns = dev->overruns;
    dev->stats.seq_gaps = dev->seq_gaps;
    dev->stats.partial_chunks = dev->partial_chunks;
    dev->stats.zero_frames = dev->zero_frames;
    dev->stats.iso_errors = dev->iso_errors;
    dev->stats.last_seq = dev->last_seq;
    if (dev->handle) {
        libusb_release_interface(dev->handle, 0);
        libusb_close(dev->handle);
    }
    libusb_exit(dev->ctx);
    pthread_mutex_destroy(&dev->lock);
    pthread_cond_destroy(&dev->cond);
    free(dev->ring);
    free(dev);
}

const rsp_stats *rsp_get_stats(const rsp_dev *dev)
{
    rsp_dev *d = (rsp_dev *)dev;
    if (!d)
        return NULL;
    d->stats.samples = d->delivered;
    d->stats.overruns = d->overruns;
    d->stats.seq_gaps = d->seq_gaps;
    d->stats.partial_chunks = d->partial_chunks;
    d->stats.zero_frames = d->zero_frames;
    d->stats.iso_errors = d->iso_errors;
    d->stats.last_seq = d->last_seq;
    return &d->stats;
}
