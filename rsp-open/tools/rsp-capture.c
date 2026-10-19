#include "rsp.h"
#include "rsp_internal.h"

#include <errno.h>
#include <getopt.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static void usage(const char *prog)
{
    fprintf(stderr,
            "Usage: %s [-f MHz] [-r Hz] [-b MHz] [--ifgr N] [--rfgr N]\n"
            "          [-d seconds] [-o file] [--cs8]\n"
            "\n"
            "Captures IQ from an SDRplay RSP1B through librsp (libusb).\n"
            "Defaults: 587 MHz, 10 MS/s, 8 MHz bandwidth, IFGR 44, RFGR 4.\n"
            "The driver subtracts the DC estimate measured during a 0.5 s\n"
            "prime and writes interleaved int16 IQ (CS16) by default.\n",
            prog);
}

int main(int argc, char **argv)
{
    rsp_config cfg;
    rsp_dev *dev;
    const char *path = "capture.iq";
    double freq_mhz = 587.0;
    double bw_mhz = 8.0;
    double duration = 8.0;
    int cs8 = 0;
    int ifgr = 44;
    int rfgr = 4;
    char err[256];
    FILE *out;
    int16_t *buf;
    uint64_t target, total = 0;
    int stalls = 0;
    int rc = 0;

    static const struct option opts[] = {
        {"freq", required_argument, NULL, 'f'},
        {"rate", required_argument, NULL, 'r'},
        {"bandwidth", required_argument, NULL, 'b'},
        {"ifgr", required_argument, NULL, 1000},
        {"rfgr", required_argument, NULL, 1001},
        {"duration", required_argument, NULL, 'd'},
        {"output", required_argument, NULL, 'o'},
        {"cs8", no_argument, NULL, 1002},
        {"help", no_argument, NULL, 'h'},
        {NULL, 0, NULL, 0},
    };
    int opt;

    rsp_default_config(&cfg);
    while ((opt = getopt_long(argc, argv, "f:r:b:d:o:h", opts, NULL)) != -1) {
        switch (opt) {
        case 'f': freq_mhz = atof(optarg); break;
        case 'r': cfg.rate_hz = atof(optarg); break;
        case 'b': bw_mhz = atof(optarg); break;
        case 'd': duration = atof(optarg); break;
        case 'o': path = optarg; break;
        case 1000: ifgr = atoi(optarg); break;
        case 1001: rfgr = atoi(optarg); break;
        case 1002: cs8 = 1; break;
        case 'h': usage(argv[0]); return 0;
        default: usage(argv[0]); return 2;
        }
    }
    if (freq_mhz <= 0 || cfg.rate_hz <= 0 || bw_mhz <= 0 || duration <= 0) {
        fprintf(stderr, "invalid capture parameters\n");
        return 2;
    }
    cfg.freq_hz = freq_mhz * 1e6;
    cfg.bandwidth_hz = bw_mhz * 1e6;
    cfg.ifgr = ifgr;
    cfg.rfgr = rfgr;

    dev = rsp_open(0, err, sizeof(err));
    if (!dev) {
        fprintf(stderr, "rsp_open: %s\n", err);
        return 1;
    }
    if (rsp_configure(dev, &cfg, err, sizeof(err)) < 0) {
        fprintf(stderr, "rsp_configure: %s\n", err);
        rsp_close(dev);
        return 1;
    }
    if (rsp_start(dev, err, sizeof(err)) < 0) {
        fprintf(stderr, "rsp_start: %s\n", err);
        rsp_close(dev);
        return 1;
    }

    out = fopen(path, "wb");
    if (!out) {
        fprintf(stderr, "%s: %s\n", path, strerror(errno));
        rsp_close(dev);
        return 1;
    }
    buf = malloc((size_t)RSP_READ_CHUNK * 2 * sizeof(int16_t));
    if (!buf) {
        fprintf(stderr, "out of memory\n");
        fclose(out);
        rsp_close(dev);
        return 1;
    }

    target = (uint64_t)(duration * cfg.rate_hz + 0.5);
    while (total < target) {
        int n = rsp_read(dev, buf, RSP_READ_CHUNK, 1000);
        if (n < 0) {
            fprintf(stderr, "rsp_read: %s\n",
                    rsp_get_stats(dev) ? "device stopped" : "error");
            rc = 1;
            break;
        }
        if (n == 0) {
            if (++stalls > 5) {
                fprintf(stderr, "no samples for 5 s\n");
                rc = 1;
                break;
            }
            continue;
        }
        stalls = 0;
        if (cs8) {
            for (int i = 0; i < n; i++) {
                int8_t i8 = (int8_t)(buf[i * 2] >> 8);
                int8_t q8 = (int8_t)(buf[i * 2 + 1] >> 8);
                if (fputc((unsigned char)i8, out) == EOF ||
                    fputc((unsigned char)q8, out) == EOF) {
                    fprintf(stderr, "write: %s\n", strerror(errno));
                    rc = 1;
                    break;
                }
            }
            if (rc)
                break;
        } else if (fwrite(buf, 2 * sizeof(int16_t), (size_t)n, out) !=
                   (size_t)n) {
            fprintf(stderr, "write: %s\n", strerror(errno));
            rc = 1;
            break;
        }
        total += (uint64_t)n;
    }

    fclose(out);
    free(buf);
    rsp_stop(dev);
    {
        const rsp_stats *st = rsp_get_stats(dev);
        fprintf(stderr,
                "captured %llu samples (%.2f s), overruns %llu, "
                "seq gaps %llu, partial %llu, zero %llu, iso errors %llu, "
                "last seq %u\n",
                (unsigned long long)st->samples,
                st->samples / cfg.rate_hz,
                (unsigned long long)st->overruns,
                (unsigned long long)st->seq_gaps,
                (unsigned long long)st->partial_chunks,
                (unsigned long long)st->zero_frames,
                (unsigned long long)st->iso_errors, st->last_seq);
    }
    rsp_close(dev);
    if (rc == 0)
        fprintf(stderr, "wrote %s\n", path);
    return rc;
}
