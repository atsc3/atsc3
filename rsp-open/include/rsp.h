#ifndef RSP_H
#define RSP_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct rsp_dev rsp_dev;

typedef struct {
    double freq_hz;
    double rate_hz;
    double bandwidth_hz;
    int ifgr;
    int rfgr;
} rsp_config;

typedef struct {
    uint64_t samples;
    uint64_t overruns;
    uint64_t seq_gaps;
    uint64_t partial_chunks;
    uint64_t zero_frames;
    uint64_t iso_errors;
    uint32_t last_seq;
} rsp_stats;

rsp_dev *rsp_open(int index, char *err, size_t errlen);
int rsp_probe(int index, char *serial, size_t serial_len);
int rsp_configure(rsp_dev *dev, const rsp_config *cfg, char *err, size_t errlen);
int rsp_start(rsp_dev *dev, char *err, size_t errlen);
int rsp_read(rsp_dev *dev, int16_t *dst, size_t max_samples, int timeout_ms);
void rsp_stop(rsp_dev *dev);
void rsp_close(rsp_dev *dev);
const rsp_stats *rsp_get_stats(const rsp_dev *dev);
void rsp_default_config(rsp_config *cfg);

#ifdef __cplusplus
}
#endif

#endif
