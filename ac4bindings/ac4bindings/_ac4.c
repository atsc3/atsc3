/*
 * ac4bindings/_ac4.c -- AC-4 (ETSI TS 103 190) decoder, as a CPython extension.
 *
 * STATUS: FOUNDATION.  This file currently implements the front of the
 * bitstream only, written from ETSI TS 103 190-2 V1.2.1 (the part that governs
 * ATSC 3.0, whose streams are bitstream_version 2).  Implemented here:
 *
 *   - ac4_parse_toc()      clause 6.2.1.1 ac4_toc(), through the substream
 *                          index table (clause 6.2.1.15), returning the fields
 *                          the rest of the decoder is driven by.
 *   - ac4_frame_sizes()    the sum(substream_size) + toc_bytes frame-length
 *                          identity that gates the parse.
 *
 * NOT YET IMPLEMENTED: presentations, substream groups, audio elements,
 * spectral decode, MDCT, A-SPX.  Those land in later revisions; this kernel is
 * deliberately staged so every rung is gated before the next.
 *
 * The bit reader follows TS 103 190-1 clause 4.2.2 variable_bits() exactly:
 * on each continuation it shifts by n_bits AND adds (1 << n_bits), so successive
 * length ranges do not overlap.
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#define NPY_NO_DEPRECATED_API NPY_1_7_API_VERSION
#include <numpy/arrayobject.h>

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

/* ------------------------------------------------------------------ */
/* Bit reader (TS 103 190-2 6.1 conventions; MSB first)               */
/* ------------------------------------------------------------------ */

typedef struct {
    const uint8_t *d;
    size_t nbits;
    size_t pos;
} bitreader;



static int br_init(bitreader *r, const uint8_t *d, size_t nbytes)
{
    r->d = d;
    r->nbits = nbytes * 8u;
    r->pos = 0;
    return 0;
}

/* Read n bits (n <= 32), MSB first.  Returns -1 on end of data. */
static int64_t br_read(bitreader *r, int n)
{
    int64_t v = 0;
    if (n < 0 || n > 32) {
        return -1;
    }
    if (r->pos + (size_t)n > r->nbits) {
        return -1;
    }
    for (int i = 0; i < n; i++) {
        v = (v << 1) |
            ((r->d[r->pos >> 3] >> (7 - (r->pos & 7))) & 1);
        r->pos++;
    }
    return v;
}

/* variable_bits(n_bits): TS 103 190-1 Table 3.  Returns -1 on end of data. */
static int64_t br_variable_bits(bitreader *r, int n_bits)
{
    int64_t value = 0;
    for (;;) {
        int64_t chunk = br_read(r, n_bits);
        if (chunk < 0) {
            return -1;
        }
        value += chunk;
        int64_t more = br_read(r, 1);
        if (more < 0) {
            return -1;
        }
        if (!more) {
            return value;
        }
        value <<= n_bits;
        value += (INT64_C(1) << n_bits);
    }
}

static void br_align(bitreader *r)
{
    while (r->pos & 7u) {
        r->pos++;
    }
}

/* ------------------------------------------------------------------ */
/* ac4_toc() -- ETSI TS 103 190-2 6.2.1.1 (bitstream_version >= 2)    */
/* ------------------------------------------------------------------ */

/* The TOC is parsed far enough to reach the substream index table, which is
   what frame de-framing needs.  Presentation parsing is stubbed at the point
   where it stops mattering for sizing (the index table follows it). */
#define AC4_MAX_SUBSTREAMS 64
#define AC4_PROT_PRIMARY_0 0 /* reserved */

typedef struct {
    int bitstream_version;
    int sequence_counter;
    int fs_index;
    int frame_rate_index;
    int b_iframe_global;
    int n_presentations;
    int payload_base;
    int n_substreams;
    int substream_sizes[AC4_MAX_SUBSTREAMS];
    int toc_bytes;
    int total_n_substream_groups;
    int channel_modes[AC4_MAX_SUBSTREAMS];
    int n_channel_modes;
} ac4_toc;

/* channel_mode prefix code, ETSI TS 103 190-2 V1.2.1 Table 78.  Returns the
   ch_mode index (the column the rest of the decoder switches on) or -1. */
static int ac4_channel_mode(bitreader *r)
{
    int32_t a = br_read(r, 1);
    if (a < 0) {
        return -1;
    }
    if (a == 0) {
        return 0;                        /* 0b0       Mono   */
    }
    int32_t b = br_read(r, 1);
    if (b < 0) {
        return -1;
    }
    if (b == 0) {
        return 1;                        /* 0b10      Stereo */
    }
    int32_t two = br_read(r, 2);         /* past 0b11 */
    if (two < 0) {
        return -1;
    }
    if (two != 0b11) {
        return 2 + two;                  /* 1100/1101/1110 -> 2/3/4 */
    }
    int32_t tail = br_read(r, 3);        /* past 0b1111 */
    if (tail < 0) {
        return -1;
    }
    if (tail < 0b110) {
        return 5 + tail;                 /* 1111000..1111101 -> 5..10 */
    }
    if (tail == 0b110) {
        int32_t x = br_read(r, 1);       /* 11111100/11111101 -> 11/12 */
        return x < 0 ? -1 : 11 + x;
    }
    /* 0b1111111 + 2 bits: 13, 14, 15, then the variable_bits escape. */
    int32_t x = br_read(r, 1);
    int32_t y = br_read(r, 1);
    if (x < 0 || y < 0) {
        return -1;
    }
    if (x == 0) {
        return 13 + y;                   /* 111111100/111111101 -> 13/14 */
    }
    if (y == 0) {
        return 15;                       /* 111111110 -> 15 */
    }
    int64_t v = br_variable_bits(r, 2);
    return v < 0 ? -1 : (int)(16 + v);
    
}

/* Consume content_type() (TS 103 190-2). */
static int ac4_content_type(bitreader *r)
{
    if (br_read(r, 3) < 0) {
        return -1;
    }
    int32_t b_lang = br_read(r, 1);
    if (b_lang < 0) {
        return -1;
    }
    if (b_lang) {
        int32_t serialized = br_read(r, 1);
        if (serialized < 0) {
            return -1;
        }
        if (serialized) {
            if (br_read(r, 1) < 0 || br_read(r, 16) < 0) {
                return -1;
            }
        } else {
            int32_t n = br_read(r, 6);
            if (n < 0) {
                return -1;
            }
            for (int i = 0; i < n; i++) {
                if (br_read(r, 8) < 0) {
                    return -1;
                }
            }
        }
    }
    return 0;
}

/* ac4_substream_info_chan(b_substreams_present) -- clause 6.2.1.8. */
static int ac4_substream_info_chan(bitreader *r, int fs_index, int frf,
                                   int b_substreams_present, int *ch_modes,
                                   int *n_ch_modes)
{
    int cm = ac4_channel_mode(r);
    if (cm < 0) {
        return -1;
    }
    if (ch_modes != NULL && n_ch_modes != NULL
            && *n_ch_modes < AC4_MAX_SUBSTREAMS) {
        ch_modes[*n_ch_modes] = cm;
    }
    if (n_ch_modes != NULL) {
        (*n_ch_modes)++;
    }
    /* Modes 11..14 (0b11111100..0b111111101) carry the presence flags. */
    if (cm >= 11 && cm <= 14) {
        if (br_read(r, 1) < 0 || br_read(r, 1) < 0 || br_read(r, 2) < 0) {
            return -1;
        }
    }
    if (fs_index == 1) {
        int32_t b_sf = br_read(r, 1);
        if (b_sf < 0) {
            return -1;
        }
        if (b_sf && br_read(r, 1) < 0) {
            return -1;
        }
    }
    int32_t b_bitrate = br_read(r, 1);
    if (b_bitrate < 0) {
        return -1;
    }
    if (b_bitrate) {
        int32_t lo = br_read(r, 3);
        if (lo < 0) {
            return -1;
        }
        if (lo == 0b111 && br_read(r, 2) < 0) {
            return -1;
        }
    }
    if (cm >= 7 && cm <= 10) {
        if (br_read(r, 1) < 0) {         /* add_ch_base */
            return -1;
        }
    }
    for (int i = 0; i < frf; i++) {
        if (br_read(r, 1) < 0) {         /* b_audio_ndot */
            return -1;
        }
    }
    if (b_substreams_present) {
        int32_t si = br_read(r, 2);
        if (si < 0) {
            return -1;
        }
        if (si == 3 && br_variable_bits(r, 2) < 0) {
            return -1;
        }
    }
    return 0;
}

/* emdf_info() -- clause 6.2.1.3.x; consume the fields in order. */
static int ac4_emdf_info(bitreader *r)
{
    int32_t ver = br_read(r, 2);
    if (ver < 0) {
        return -1;
    }
    if (ver == 3 && br_variable_bits(r, 2) < 0) {
        return -1;
    }
    int32_t key = br_read(r, 3);
    if (key < 0) {
        return -1;
    }
    if (key == 7 && br_variable_bits(r, 3) < 0) {
        return -1;
    }
    int32_t b_pay = br_read(r, 1);
    if (b_pay < 0) {
        return -1;
    }
    if (b_pay) {
        int32_t si = br_read(r, 2);
        if (si < 0) {
            return -1;
        }
        if (si == 3 && br_variable_bits(r, 2) < 0) {
            return -1;
        }
    }
    /* emdf_protection(): protection_length_primary(2) + secondary(2) */
    static const int prim_bits[4] = {0, 8, 32, 128};
    static const int sec_bits[4] = {0, 8, 32, 128};
    int32_t prim = br_read(r, 2);
    int32_t sec = br_read(r, 2);
    if (prim < 0 || sec < 0) {
        return -1;
    }
    if (prim == AC4_PROT_PRIMARY_0) {
        return -1; /* reserved */
    }
    int pbits = prim_bits[prim] + sec_bits[sec];
    for (int i = 0; i < pbits; i += 32) {
        int chunk = (pbits - i) < 32 ? (pbits - i) : 32;
        if (br_read(r, chunk) < 0) {
            return -1;
        }
    }
    return 0;
}

/* frame_rate_multiply_info() -- clause 6.2.1.4; sets frame_rate_factor. */
static int ac4_frame_rate_multiply_info(bitreader *r, int frame_rate_index,
                                        int *frf)
{
    *frf = 1;
    if (frame_rate_index == 2 || frame_rate_index == 3 ||
        frame_rate_index == 4) {
        int32_t b = br_read(r, 1);
        if (b < 0) {
            return -1;
        }
        if (b) {
            int32_t mb = br_read(r, 1);
            if (mb < 0) {
                return -1;
            }
            *frf = mb ? 4 : 2;
        }
    } else if (frame_rate_index == 0 || frame_rate_index == 1 ||
               frame_rate_index == 7 || frame_rate_index == 8 ||
               frame_rate_index == 9) {
        int32_t b = br_read(r, 1);
        if (b < 0) {
            return -1;
        }
        if (b) {
            *frf = 2;
        }
    }
    return 0;
}

/* frame_rate_fractions_info() -- clause 6.2.1.4. */
static int ac4_frame_rate_fractions_info(bitreader *r, int frame_rate_index,
                                         int frame_rate_factor)
{
    if (frame_rate_index >= 5 && frame_rate_index <= 9 &&
        frame_rate_factor == 1) {
        if (br_read(r, 1) < 0) {
            return -1;
        }
    }
    if (frame_rate_index >= 10 && frame_rate_index <= 12) {
        int32_t b = br_read(r, 1);
        if (b < 0) {
            return -1;
        }
        if (b && br_read(r, 1) < 0) {
            return -1;
        }
    }
    return 0;
}

/* presentation_config -> number of ac4_sgi_specifier() calls (clause 6.2.1.3).
   Returns the count, or -1 for the ext-info default case. */
static int ac4_n_sgi_specifiers(int cfg)
{
    switch (cfg) {
    case 0: return 2;
    case 1: return 2;
    case 2: return 2;
    case 3: return 3;
    case 4: return 3;
    default: return -1;              /* 5 handled inline; 6+/7 ext */
    }
}

/* ac4_sgi_specifier() -- clause 6.2.1.7 (bitstream_version >= 2). */
static int ac4_sgi_specifier(bitreader *r)
{
    int32_t group = br_read(r, 3);
    if (group < 0) {
        return -1;
    }
    if (group == 7 && br_variable_bits(r, 2) < 0) {
        return -1;
    }
    return 0;
}

/* ac4_presentation_substream_info() -- clause 6.2.1.12. */
static int ac4_presentation_substream_info(bitreader *r)
{
    if (br_read(r, 1) < 0) {         /* b_alternative */
        return -1;
    }
    if (br_read(r, 1) < 0) {         /* b_pres_ndot */
        return -1;
    }
    int32_t si = br_read(r, 2);      /* substream_index */
    if (si < 0) {
        return -1;
    }
    if (si == 3 && br_variable_bits(r, 2) < 0) {
        return -1;
    }
    return 0;
}

/* ac4_presentation_v1_info() -- clause 6.2.1.3.  Returns the number of
   referenced substream groups (>= 0), or a negative value on error. */
static int ac4_presentation_v1_info(bitreader *r, int frame_rate_index,
                                    int fs_index, int bitstream_version,
                                    int *frf_out)
{
    int32_t b_single_sg = br_read(r, 1);
    if (b_single_sg < 0) {
        return -1;
    }
    int cfg = 0;
    if (b_single_sg != 1) {
        int32_t c = br_read(r, 3);
        if (c < 0) {
            return -1;
        }
        if (c == 7) {
            int64_t v = br_variable_bits(r, 2);
            if (v < 0) {
                return -1;
            }
            c += (int32_t)v;
        }
        cfg = c;
    }
    if (bitstream_version != 1) {
        for (;;) {                        /* presentation_version(): unary */
            int32_t bit = br_read(r, 1);
            if (bit < 0) {
                return -1;
            }
            if (!bit) {
                break;
            }
        }
    }
    int add_emdf = 0;
    int n_sg = 0;
    if (b_single_sg != 1 && cfg == 6) {
        add_emdf = 1;
    } else {
        if (bitstream_version != 1 && br_read(r, 3) < 0) {
            return -1;                    /* mdcompat */
        }
        int32_t b_pres_id = br_read(r, 1);         /* b_presentation_id */
        if (b_pres_id < 0) {
            return -1;
        }
        if (b_pres_id && br_variable_bits(r, 2) < 0) {
            return -1;                    /* presentation_id */
        }
        int frf;
        if (ac4_frame_rate_multiply_info(r, frame_rate_index, &frf) < 0) {
            return -1;
        }
        if (frf_out) {
            *frf_out = frf;
        }
        if (ac4_frame_rate_fractions_info(r, frame_rate_index, frf) < 0) {
            return -1;
        }
        if (ac4_emdf_info(r) < 0) {
            return -1;
        }
        int32_t b_filter = br_read(r, 1);          /* b_presentation_filter */
        if (b_filter < 0) {
            return -1;
        }
        if (b_filter && br_read(r, 1) < 0) {
            return -1;                    /* b_enable_presentation */
        }
        if (b_single_sg == 1) {
            if (ac4_sgi_specifier(r) < 0) {
                return -1;
            }
            n_sg = 1;
        } else {
            if (br_read(r, 1) < 0) {      /* b_multi_pid */
                return -1;
            }
            int n_spec = ac4_n_sgi_specifiers(cfg);
            if (n_spec >= 0) {
                for (int i = 0; i < n_spec; i++) {
                    if (ac4_sgi_specifier(r) < 0) {
                        return -1;
                    }
                }
                n_sg = (cfg == 1 || cfg == 4) ? (cfg == 1 ? 1 : 2) : n_spec;
            } else if (cfg == 5) {
                int32_t nm2 = br_read(r, 2);
                if (nm2 < 0) {
                    return -1;
                }
                n_sg = nm2 + 2;
                if (n_sg == 5) {
                    int64_t v = br_variable_bits(r, 2);
                    if (v < 0) {
                        return -1;
                    }
                    n_sg += (int)v;
                }
                for (int i = 0; i < n_sg; i++) {
                    if (ac4_sgi_specifier(r) < 0) {
                        return -1;
                    }
                }
            } else {
                /* presentation_config_ext_info(): n_skip_bytes(5) + flag +
                   skip.  Not seen on RF33; handled so the cursor stays true. */
                int32_t nsb = br_read(r, 5);
                if (nsb < 0) {
                    return -1;
                }
                int64_t more = br_read(r, 1);
                if (more < 0) {
                    return -1;
                }
                if (more) {
                    int64_t v = br_variable_bits(r, 2);
                    if (v < 0) {
                        return -1;
                    }
                    nsb += (int32_t)(v << 5);
                }
                for (int i = 0; i < nsb; i++) {
                    if (br_read(r, 8) < 0) {
                        return -1;
                    }
                }
            }
        }
        if (br_read(r, 1) < 0) {          /* b_pre_virtualized */
            return -1;
        }
        int32_t b_add = br_read(r, 1);             /* b_add_emdf_substreams */
        if (b_add < 0) {
            return -1;
        }
        add_emdf = b_add;
        if (ac4_presentation_substream_info(r) < 0) {
            return -1;
        }
    }
    if (add_emdf) {
        int32_t n = br_read(r, 2);
        if (n < 0) {
            return -1;
        }
        if (n == 0) {
            int64_t v = br_variable_bits(r, 2);
            if (v < 0) {
                return -1;
            }
            n = (int32_t)v + 4;
        }
        for (int i = 0; i < n; i++) {
            if (ac4_emdf_info(r) < 0) {
                return -1;
            }
        }
    }
    (void)fs_index;
    return n_sg;
}

/* ac4_substream_group_info() -- clause 6.2.1.6. */
static int ac4_substream_group_info(bitreader *r, int fs_index, int frf,
                                    int bitstream_version, int *ch_modes,
                                    int *n_ch_modes)
{
    int32_t b_sub = br_read(r, 1);
    int32_t b_hsf = br_read(r, 1);
    int32_t b_single = br_read(r, 1);
    if (b_sub < 0 || b_hsf < 0 || b_single < 0) {
        return -1;
    }
    int n_lf;
    if (b_single) {
        n_lf = 1;
    } else {
        int32_t nm2 = br_read(r, 2);
        if (nm2 < 0) {
            return -1;
        }
        n_lf = nm2 + 2;
        if (n_lf == 5) {
            int64_t extra = br_variable_bits(r, 2);
            if (extra < 0) {
                return -1;
            }
            n_lf += (int)extra;
        }
    }
    int32_t b_chan = br_read(r, 1);
    if (b_chan < 0) {
        return -1;
    }
    if (b_chan) {
        for (int sus = 0; sus < n_lf; sus++) {
            if (bitstream_version == 1 && br_read(r, 1) < 0) {
                return -1;            /* sus_ver */
            }
            if (ac4_substream_info_chan(r, fs_index, frf, b_sub,
                                        ch_modes, n_ch_modes) < 0) {
                return -1;
            }
            if (b_hsf) {
                int32_t si = br_read(r, 2);
                if (si < 0) {
                    return -1;
                }
                if (si == 3 && br_variable_bits(r, 2) < 0) {
                    return -1;
                }
            }
        }
    } else {
        /* Object/A-JOC coded: not present on RF33 channel-based content. */
        return -1;
    }
    int32_t b_ct = br_read(r, 1);    /* b_content_type */
    if (b_ct < 0) {
        return -1;
    }
    if (b_ct && ac4_content_type(r) < 0) {
        return -1;
    }
    return 0;
}

/* Parse ac4_toc() and the substream index table.  0 on success. */
static int ac4_parse_toc_impl(const uint8_t *data, size_t nbytes, ac4_toc *toc)
{
    bitreader r;
    bitreader *b = &r;
    br_init(b, data, nbytes);
    memset(toc, 0, sizeof(*toc));

    int32_t bv = br_read(b, 2);
    if (bv < 0) {
        return -1;
    }
    if (bv == 3) {
        int64_t v = br_variable_bits(b, 2);
        if (v < 0) {
            return -1;
        }
        bv += (int32_t)v;
    }
    toc->bitstream_version = bv;
    int32_t seq = br_read(b, 10);
    if (seq < 0) {
        return -1;
    }
    toc->sequence_counter = seq;
    int32_t b_wait = br_read(b, 1);
    if (b_wait < 0) {
        return -1;
    }
    if (b_wait) {
        int32_t wf = br_read(b, 3);
        if (wf < 0) {
            return -1;
        }
        if (wf > 0 && br_read(b, 2) < 0) {
            return -1;
        }
    }
    int32_t fs = br_read(b, 1);
    int32_t fri = br_read(b, 4);
    if (fs < 0 || fri < 0) {
        return -1;
    }
    toc->fs_index = fs;
    toc->frame_rate_index = fri;
    int32_t ifr = br_read(b, 1);
    if (ifr < 0) {
        return -1;
    }
    toc->b_iframe_global = ifr;
    int32_t b_single = br_read(b, 1);
    if (b_single < 0) {
        return -1;
    }
    int n_pres;
    if (b_single) {
        n_pres = 1;
    } else {
        int32_t b_more = br_read(b, 1);
        if (b_more < 0) {
            return -1;
        }
        if (b_more) {
            int64_t v = br_variable_bits(b, 2);
            if (v < 0) {
                return -1;
            }
            n_pres = (int)v + 2;
        } else {
            n_pres = 0;
        }
    }
    toc->n_presentations = n_pres;
    int32_t b_pb = br_read(b, 1);
    if (b_pb < 0) {
        return -1;
    }
    if (b_pb) {
        int32_t pb = br_read(b, 5);
        if (pb < 0) {
            return -1;
        }
        pb += 1;
        if (pb == 0x20) {
            int64_t v = br_variable_bits(b, 3);
            if (v < 0) {
                return -1;
            }
            pb += (int32_t)v;
        }
        toc->payload_base = pb;
    }

    if (toc->bitstream_version <= 1) {
        /* Version 0/1 presentations are out of scope for this kernel (ATSC 3.0
           uses version 2).  Reported rather than guessed. */
        return 1;
    }

    int32_t b_pid = br_read(b, 1);
    if (b_pid < 0) {
        return -1;
    }
    if (b_pid) {
        if (br_read(b, 16) < 0) {
            return -1;
        }
        int32_t b_uuid = br_read(b, 1);
        if (b_uuid < 0) {
            return -1;
        }
        if (b_uuid) {
            for (int i = 0; i < 16; i++) {
                if (br_read(b, 8) < 0) {
                    return -1;
                }
            }
        }
    }
    int total_sg = 0;
    int frf = 1;
    for (int i = 0; i < n_pres; i++) {
        int n_sg = ac4_presentation_v1_info(b, fri, fs, toc->bitstream_version,
                                            &frf);
        if (n_sg < 0) {
            return -1;
        }
        total_sg += n_sg;
    }
    toc->total_n_substream_groups = total_sg;
    for (int j = 0; j < total_sg; j++) {
        if (ac4_substream_group_info(b, fs, frf, toc->bitstream_version,
                                     toc->channel_modes,
                                     &toc->n_channel_modes) < 0) {
            return -1;
        }
    }

    /* substream_index_table() -- clause 6.2.1.15 */
    int32_t n_sub = br_read(b, 2);
    if (n_sub < 0) {
        return -1;
    }
    if (n_sub == 0) {
        int64_t v = br_variable_bits(b, 2);
        if (v < 0) {
            return -1;
        }
        n_sub = (int)v + 4;
    }
    if (n_sub > AC4_MAX_SUBSTREAMS) {
        return -1;
    }
    int size_present = 1;
    if (n_sub == 1) {
        int32_t b_size = br_read(b, 1);
        if (b_size < 0) {
            return -1;
        }
        size_present = b_size;
    }
    int sizes[AC4_MAX_SUBSTREAMS] = {0};
    if (size_present) {
        for (int s = 0; s < n_sub; s++) {
            int32_t more = br_read(b, 1);
            int32_t sz = br_read(b, 10);
            if (more < 0 || sz < 0) {
                return -1;
            }
            if (more) {
                int64_t extra = br_variable_bits(b, 2);
                if (extra < 0) {
                    return -1;
                }
                sz += (int32_t)(extra << 10);
            }
            sizes[s] = sz;
        }
    }
    br_align(b);
    toc->n_substreams = n_sub;
    for (int s = 0; s < n_sub; s++) {
        toc->substream_sizes[s] = sizes[s];
    }
    toc->toc_bytes = (int)(b->pos / 8);
    return 0;
}

/* Locate substream ``index`` inside a raw AC-4 frame.

   TS 103 190-1 4.3.3.2.11 / Pseudocode 1: the substream data begins after the
   byte-aligned ac4_toc, and the offsets START at payload_base:

       substream_n_offset = payload_base
       for (s = 0; s < n; s++) offset += substream_size[s]

   payload_base is 0 only when b_payload_base is false.  On RF33 every lane
   signals b_payload_base = false, but a stream that signals it does not is read
   one byte early if this is omitted.  Returns 0 on success and fills ``out``
   with (offset, size); ``size`` is 0 for an out-of-range index. */
static int ac4_substream_span(const uint8_t *data, size_t nbytes, int index,
                              size_t *offset_out, size_t *size_out)
{
    ac4_toc toc;
    if (ac4_parse_toc_impl(data, nbytes, &toc) != 0) {
        return -1;
    }
    if (index < 0 || index >= toc.n_substreams) {
        *offset_out = 0;
        *size_out = 0;
        return 0;
    }
    size_t off = (size_t)toc.toc_bytes + (size_t)toc.payload_base;
    for (int s = 0; s < index; s++) {
        off += (size_t)toc.substream_sizes[s];
    }
    size_t size = (size_t)toc.substream_sizes[index];
    if (off + size > nbytes) {
        return -1;
    }
    *offset_out = off;
    *size_out = size;
    return 0;
}

/* ------------------------------------------------------------------ */
/* Huffman codebook decoding (TS 103 190-1 4.2.8.5, Pseudocode 19)     */
/* ------------------------------------------------------------------ */

/* Decode one codeword from a canonical Huffman codebook.

   The tables are passed in from Python (the committed Annex A data), so this
   kernel owns only the bit-level prefix walk: read one bit at a time, MSB
   first, and match (length, value) against the codebook.  ``lens`` and
   ``words`` have ``n`` entries; ``words`` are the codeword values (already
   right-aligned to their length, as the spec prints them).  Returns the symbol
   index, or -1 when no codeword matches inside the maximum length. */
static int huff_decode(bitreader *r, const int32_t *lens,
                       const int32_t *words, int n)
{
    int maxlen = 0;
    for (int i = 0; i < n; i++) {
        if (lens[i] > maxlen) {
            maxlen = lens[i];
        }
    }
    int64_t code = 0;
    for (int len = 1; len <= maxlen; len++) {
        int64_t bit = br_read(r, 1);
        if (bit < 0) {
            return -1;
        }
        code = (code << 1) | bit;
        for (int i = 0; i < n; i++) {
            if (lens[i] == len && words[i] == code) {
                return i;
            }
        }
    }
    return -1;
}

/* Decode a Huffman symbol by walking a flattened prefix tree.

   ``left``/``right`` are child indices (-1 = none) and ``sym`` the symbol at a
   leaf (-1 at internal nodes).  Returns the symbol, or -1 on no match / EOF;
   ``*consumed`` receives the bits read. */
static int tree_decode(bitreader *r, const int32_t *left, const int32_t *right,
                       const int32_t *sym, int root, int *consumed)
{
    int node = root;
    int n = 0;
    while (sym[node] < 0) {
        int64_t bit = br_read(r, 1);
        if (bit < 0) {
            return -1;
        }
        n++;
        node = bit ? right[node] : left[node];
        if (node < 0) {
            return -1;
        }
    }
    *consumed = n;
    return sym[node];
}

/* asf_spectral_data() for one window group (TS 103 190-1 4.2.8.4).

   ``sects`` is ``n_sects`` triples (cb, sfb_start, sfb_end); ``offsets`` maps
   sfb -> spectral line.  Codebook metadata (dim/mod/off) and the flattened
   trees are passed in.  ``lines`` receives the quantized spectral lines;
   ``*bitpos`` is updated in place.  Returns 0 on success, negative on a
   malformed section. */
static int asf_spectral_group(bitreader *r, const int32_t *sects, int n_sects,
                              const int32_t *offsets, int n_offsets,
                              const int32_t *left, const int32_t *right,
                              const int32_t *sym, const int32_t *roots,
                              const int32_t *dims, const int32_t *mods,
                              const int32_t *offs_cb, const int32_t *unsigned_cb,
                              int32_t *lines, int n_lines)
{
    for (int si = 0; si < n_sects; si++) {
        int cb = sects[si * 3 + 0];
        int s0 = sects[si * 3 + 1];
        int s1 = sects[si * 3 + 2];
        if (cb <= 0 || cb > 11) {
            continue;                      /* cb 0 = all-zero band, no bits */
        }
        int dim = dims[cb];
        int mod = mods[cb];
        int off = offs_cb[cb];
        int k = offsets[s0 < n_offsets ? s0 : n_offsets - 1];
        int end = offsets[s1 < n_offsets ? s1 : n_offsets - 1];
        while (k < end) {
            int consumed = 0;
            int idx = tree_decode(r, left, right, sym, roots[cb], &consumed);
            if (idx < 0) {
                return -1;
            }
            int vals[4];
            if (dim == 4) {
                int m3 = mod * mod * mod;
                int m2 = mod * mod;
                vals[0] = idx / m3 - off;
                vals[1] = (idx / m2) % mod - off;
                vals[2] = (idx / mod) % mod - off;
                vals[3] = idx % mod - off;
            } else {
                vals[0] = idx / mod - off;
                vals[1] = idx % mod - off;
            }
            if (unsigned_cb[cb]) {
                for (int j = 0; j < dim; j++) {
                    if (vals[j]) {
                        int64_t s = br_read(r, 1);
                        if (s < 0) {
                            return -1;
                        }
                        if (s) {
                            vals[j] = -vals[j];
                        }
                    }
                }
            }
            if (cb == 11) {
                for (int j = 0; j < dim; j++) {
                    if (vals[j] == 16 || vals[j] == -16) {
                        int n = 0;
                        for (;;) {
                            int64_t b1 = br_read(r, 1);
                            if (b1 < 0) {
                                return -1;
                            }
                            if (!b1) {
                                break;
                            }
                            n++;
                            if (n > 16) {
                                return -1;
                            }
                        }
                        int64_t mag = (INT64_C(1) << (n + 4));
                        int64_t ext = br_read(r, n + 4);
                        if (ext < 0) {
                            return -1;
                        }
                        mag += ext;
                        vals[j] = (vals[j] < 0) ? -(int)mag : (int)mag;
                    }
                }
            }
            for (int j = 0; j < dim && k + j < end && k + j < n_lines; j++) {
                lines[k + j] = vals[j];
            }
            k += dim;
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* ASF sf_data (TS 103 190-1 4.2.7.3; Pseudocode 3-5, 21, 23)          */
/* ------------------------------------------------------------------ */

/* The full scale-factor data block of one channel: asf_transform_info +
   asf_psy_info (the "framing"), then per window group the sections, the
   spectral data, the scale factors and the spectral noise fill.  Every stage
   runs group by group in the spec's order.  Window-agnostic inputs are the
   committed Annex B band offsets (Tables B.4..B.7) and the Annex A codebooks,
   passed in from Python. */

#define AC4_SF_MAX_GROUPS 16
#define AC4_SF_MAX_SFB 64
#define AC4_SF_MAX_WINDOWS 16
#define AC4_SF_MAX_LINES 2048
#define AC4_SF_MAX_SECTS 64

/* Table 99: short transform length index -> samples (48 kHz). */
static const int ac4_short_len[4] = {96, 192, 384, 768};

/* Table 108: n_grp_bits[transf_length[0]][transf_length[1]]. */
static const int ac4_n_grp_bits[4][4] = {
    {15, 10, 8, 7}, {10, 7, 4, 3}, {8, 4, 3, 1}, {7, 3, 1, 1}};

/* Table 105: the LFE's max_sfb is always a 3-bit field (sf_info_lfe). */
#define AC4_N_MSFBL_BITS 3

/* Table 105: max_sfb field width indexed by the transform length. */
static int ac4_n_msfb_bits(int length)
{
    if (length >= 384) {
        return 6;
    }
    if (length >= 192) {
        return 5;
    }
    return 4;
}

/* Serialised framing layout shared with the Python wrapper.  The C sf_data
   takes the framing the caller already parsed (TS 103 190-1: sf_info() /
   sf_info_lfe() precede sf_data()), so it must round-trip through Python. */
#define AC4_FR_W2G_OFF 9
#define AC4_FR_NWIN_OFF (AC4_FR_W2G_OFF + AC4_SF_MAX_WINDOWS)
#define AC4_FR_SIZE (AC4_FR_NWIN_OFF + AC4_SF_MAX_GROUPS)

typedef struct {
    int b_long;
    int tl[2];
    int different;
    int max_sfb[2];
    int n_half;
    int num_windows;
    int num_groups;
    int w2g[AC4_SF_MAX_WINDOWS];
    int nwin[AC4_SF_MAX_GROUPS];
} ac4_framing;

static void ac4_framing_pack(const ac4_framing *fr, int32_t *out)
{
    memset(out, 0, AC4_FR_SIZE * sizeof(int32_t));
    out[0] = fr->b_long;
    out[1] = fr->tl[0];
    out[2] = fr->tl[1];
    out[3] = fr->different;
    out[4] = fr->n_half;
    out[5] = fr->num_windows;
    out[6] = fr->num_groups;
    out[7] = fr->max_sfb[0];
    out[8] = fr->max_sfb[1];
    for (int i = 0; i < fr->num_windows; i++) {
        out[AC4_FR_W2G_OFF + i] = fr->w2g[i];
    }
    for (int i = 0; i < fr->num_groups; i++) {
        out[AC4_FR_NWIN_OFF + i] = fr->nwin[i];
    }
}

static void ac4_framing_unpack(const int32_t *in, ac4_framing *fr)
{
    memset(fr, 0, sizeof(*fr));
    fr->b_long = in[0];
    fr->tl[0] = in[1];
    fr->tl[1] = in[2];
    fr->different = in[3];
    fr->n_half = in[4];
    fr->num_windows = in[5];
    fr->num_groups = in[6];
    fr->max_sfb[0] = in[7];
    fr->max_sfb[1] = in[8];
    for (int i = 0; i < fr->num_windows; i++) {
        fr->w2g[i] = in[AC4_FR_W2G_OFF + i];
    }
    for (int i = 0; i < fr->num_groups; i++) {
        fr->nwin[i] = in[AC4_FR_NWIN_OFF + i];
    }
}

/* sf_info_lfe -- the LFE is always a long frame with a 3-bit max_sfb. */
static int ac4_parse_framing_lfe(bitreader *b, ac4_framing *fr)
{
    memset(fr, 0, sizeof(*fr));
    fr->b_long = 1;
    fr->different = 0;
    fr->n_half = 1;
    int64_t v = br_read(b, AC4_N_MSFBL_BITS);
    if (v < 0) {
        return -1;
    }
    fr->max_sfb[0] = (int)v;
    fr->num_windows = 1;
    fr->num_groups = 1;
    fr->w2g[0] = 0;
    fr->nwin[0] = 1;
    return 0;
}

/* asf_transform_info + asf_psy_info -- Pseudocode 3-5.  Returns 0 or -1. */
static int ac4_parse_framing(bitreader *b, ac4_framing *fr)
{
    memset(fr, 0, sizeof(*fr));
    int64_t v = br_read(b, 1);
    if (v < 0) {
        return -1;
    }
    fr->b_long = (int)v;
    if (!fr->b_long) {
        int64_t t0 = br_read(b, 2);
        int64_t t1 = br_read(b, 2);
        if (t0 < 0 || t1 < 0) {
            return -1;
        }
        fr->tl[0] = (int)t0;
        fr->tl[1] = (int)t1;
    }
    fr->different = (!fr->b_long && fr->tl[0] != fr->tl[1]);
    int len0 = fr->b_long ? 1536 : ac4_short_len[fr->tl[0]];
    v = br_read(b, ac4_n_msfb_bits(len0));
    if (v < 0) {
        return -1;
    }
    fr->max_sfb[0] = (int)v;
    fr->n_half = 1;
    if (fr->different) {
        int len1 = ac4_short_len[fr->tl[1]];
        v = br_read(b, ac4_n_msfb_bits(len1));
        if (v < 0) {
            return -1;
        }
        fr->max_sfb[1] = (int)v;
        fr->n_half = 2;
    }
    int n_grp = fr->b_long ? 0
                           : ac4_n_grp_bits[fr->tl[0]][fr->tl[1]];
    if (n_grp > AC4_SF_MAX_WINDOWS - 2) {
        return -1;
    }
    int sfg[AC4_SF_MAX_WINDOWS + 2];
    for (int i = 0; i < n_grp; i++) {
        v = br_read(b, 1);
        if (v < 0) {
            return -1;
        }
        sfg[i] = (int)v;
    }
    fr->num_windows = 1;
    fr->num_groups = 1;
    for (int i = 0; i < AC4_SF_MAX_WINDOWS; i++) {
        fr->w2g[i] = 0;
    }
    fr->w2g[0] = 0;
    if (!fr->b_long) {
        fr->num_windows = n_grp + 1;
        if (fr->different) {
            int nw0 = 1 << (3 - fr->tl[0]);
            sfg[n_grp] = 0;
            for (int i = n_grp; i > nw0 - 1; i--) {
                sfg[i] = sfg[i - 1];
            }
            sfg[nw0 - 1] = 0;
            fr->num_windows++;
        }
        if (fr->num_windows > AC4_SF_MAX_WINDOWS) {
            return -1;
        }
        for (int i = 0; i < fr->num_windows - 1; i++) {
            if (sfg[i] == 0) {
                fr->num_groups++;
            }
            fr->w2g[i + 1] = fr->num_groups - 1;
        }
    }
    if (fr->num_groups > AC4_SF_MAX_GROUPS) {
        return -1;
    }
    for (int g = 0; g < fr->num_groups; g++) {
        int n = 0;
        for (int w = 0; w < fr->num_windows; w++) {
            if (fr->w2g[w] == g) {
                n++;
            }
        }
        fr->nwin[g] = n;
    }
    return 0;
}

/* Pseudocode 5: which half of the frame group g belongs to. */
static int ac4_fr_idx(const ac4_framing *fr, int g)
{
    if (!fr->different) {
        return 0;
    }
    int nw0 = 1 << (3 - fr->tl[0]);
    return g >= fr->w2g[nw0] ? 1 : 0;
}

static int ac4_fr_length_g(const ac4_framing *fr, int g)
{
    if (fr->b_long) {
        return 1536;
    }
    int i = ac4_fr_idx(fr, g);
    return ac4_short_len[fr->tl[i]];
}

static int ac4_fr_max_sfb_g(const ac4_framing *fr, int g)
{
    int i = ac4_fr_idx(fr, g);
    if (i < 0) {
        i = 0;
    }
    if (i >= fr->n_half || i > 1) {
        i = fr->n_half - 1;
    }
    if (i < 0 || i > 1) {
        i = 0;
    }
    return fr->max_sfb[i];
}

/* Clause 4.2.8.3: 3 bits for short transform lengths <= 384. */
static int ac4_fr_n_sect_bits(const ac4_framing *fr, int g)
{
    if (fr->b_long) {
        return 5;
    }
    int i = ac4_fr_idx(fr, g);
    return fr->tl[i] <= 2 ? 3 : 5;
}

/* The row of SFB_OFFSET (Tables B.4..B.7) for a transform length, or NULL. */
static const int32_t *ac4_sfb_row(int length, const int32_t *rows,
                                  const int32_t *lens, int nrows)
{
    for (int i = 0; i < nrows; i++) {
        if (lens[i] == length) {
            return rows + (size_t)i * AC4_SF_MAX_SFB;
        }
    }
    return NULL;
}

static int ac4_row_count(const int32_t *row)
{
    int n = 0;
    while (n < AC4_SF_MAX_SFB && row[n] >= 0) {
        n++;
    }
    return n;
}

/* max_quant_idx: largest |line| in a band (Pseudocode 21/23 helper). */
static int ac4_band_max(const int32_t *lines, int n_lines,
                        const int32_t *off, int n_off, int sfb)
{
    if (sfb + 1 >= n_off) {
        return 0;
    }
    int lo = off[sfb];
    int hi = off[sfb + 1];
    if (hi > n_lines) {
        hi = n_lines;
    }
    int m = 0;
    for (int i = lo; i < hi; i++) {
        int a = lines[i] < 0 ? -lines[i] : lines[i];
        if (a > m) {
            m = a;
        }
    }
    return m;
}

/* asf_section_data -- clause 4.2.8.3.  Fills sfb_cb and returns the section
   triples (cb, start, end) with the unclamped end the spec records. */
static int ac4_section_data(bitreader *b, int max_sfb, int n_sect_bits,
                            int32_t *sfb_cb, int32_t *sects, int *n_sects)
{
    if (max_sfb > AC4_SF_MAX_SFB) {
        return -1;
    }
    for (int i = 0; i < max_sfb; i++) {
        sfb_cb[i] = 0;
    }
    int esc = (1 << n_sect_bits) - 1;
    int k = 0;
    int ns = 0;
    while (k < max_sfb) {
        int64_t cb = br_read(b, 4);
        if (cb < 0) {
            return -1;
        }
        int sect_len = 1;
        for (;;) {
            int64_t incr = br_read(b, n_sect_bits);
            if (incr < 0) {
                return -1;
            }
            if (incr != esc) {
                sect_len += (int)incr;
                break;
            }
            sect_len += esc;
        }
        int end = k + sect_len;
        int fill = end > max_sfb ? max_sfb : end;
        for (int sfb = k; sfb < fill; sfb++) {
            sfb_cb[sfb] = (int)cb;
        }
        if (ns >= AC4_SF_MAX_SECTS) {
            return -1;
        }
        sects[ns * 3 + 0] = (int)cb;
        sects[ns * 3 + 1] = k;
        sects[ns * 3 + 2] = end;
        ns++;
        k += sect_len;
    }
    *n_sects = ns;
    return 0;
}

/* The full asf_sf_data() of one channel. */
typedef struct {
    int32_t lines[AC4_SF_MAX_LINES];
    int n_lines;
    int32_t sects[AC4_SF_MAX_GROUPS * AC4_SF_MAX_SECTS * 3];
    int n_sects;
    int32_t sfb_cb[AC4_SF_MAX_GROUPS * AC4_SF_MAX_SFB];
    int n_cb;
    int32_t sfs[AC4_SF_MAX_GROUPS * AC4_SF_MAX_SFB];
    int32_t snf[AC4_SF_MAX_GROUPS * AC4_SF_MAX_SFB];
    int has_snf;
    int32_t offsets[AC4_SF_MAX_SFB];
    int n_offsets;
    int32_t offsets_all[AC4_SF_MAX_GROUPS * AC4_SF_MAX_SFB];
    int32_t n_off_g[AC4_SF_MAX_GROUPS];
    int32_t max_sfb[AC4_SF_MAX_GROUPS];
    int32_t framing[AC4_FR_SIZE];
    int groups;
    int ref;
} ac4_sf_result;

static int ac4_sf_impl(
    bitreader *b, const int32_t *sfb_rows, const int32_t *sfb_lens,
    int nrows,
    const int32_t *sp_left, const int32_t *sp_right, const int32_t *sp_sym,
    const int32_t *sp_roots, const int32_t *sp_dims, const int32_t *sp_mods,
    const int32_t *sp_off, const int32_t *sp_uns,
    const int32_t *sfL, const int32_t *sfR, const int32_t *sfS, int sfRoot,
    const int32_t *snfL, const int32_t *snfR, const int32_t *snfS, int snfRoot,
    const int32_t *framing32, ac4_sf_result *res)
{
    ac4_framing fr;
    ac4_framing_unpack(framing32, &fr);
    memcpy(res->framing, framing32, sizeof(res->framing));
    res->groups = fr.num_groups;
    memset(res->lines, 0, sizeof(res->lines));
    res->n_sects = 0;
    res->n_cb = 0;
    res->has_snf = 0;

    int32_t cb_g[AC4_SF_MAX_GROUPS][AC4_SF_MAX_SFB];
    int32_t sects_g[AC4_SF_MAX_GROUPS][AC4_SF_MAX_SECTS * 3];
    int ns_g[AC4_SF_MAX_GROUPS];
    int32_t off_g[AC4_SF_MAX_GROUPS][AC4_SF_MAX_SFB];
    int noff_g[AC4_SF_MAX_GROUPS];
    int lines_max = 0;
    memset(noff_g, 0, sizeof(noff_g));

    for (int g = 0; g < fr.num_groups; g++) {
        int m = ac4_fr_max_sfb_g(&fr, g);
        res->max_sfb[g] = m;
        const int32_t *row = ac4_sfb_row(ac4_fr_length_g(&fr, g), sfb_rows,
                                         sfb_lens, nrows);
        if (row == NULL) {
            return -1;
        }
        int n_off = ac4_row_count(row);
        int base = 0;
        for (int gg = 0; gg < g; gg++) {
            const int32_t *r2 = ac4_sfb_row(ac4_fr_length_g(&fr, gg), sfb_rows,
                                            sfb_lens, nrows);
            if (r2 == NULL) {
                return -1;
            }
            int m2 = ac4_fr_max_sfb_g(&fr, gg);
            int n2 = ac4_row_count(r2);
            if (m2 > n2 - 1) {
                m2 = n2 - 1;
            }
            base += r2[m2] * fr.nwin[gg];
        }
        noff_g[g] = n_off;
        for (int i = 0; i < n_off; i++) {
            off_g[g][i] = base + row[i] * fr.nwin[g];
        }
        int end = off_g[g][m < n_off ? m : n_off - 1];
        if (end > lines_max) {
            lines_max = end;
        }
        if (ac4_section_data(b, m, ac4_fr_n_sect_bits(&fr, g), cb_g[g],
                             sects_g[g], &ns_g[g]) != 0) {
            return -1;
        }
    }
    if (lines_max > AC4_SF_MAX_LINES) {
        return -1;
    }
    res->n_lines = lines_max;

    for (int g = 0; g < fr.num_groups; g++) {
        int m = res->max_sfb[g];
        int32_t part[AC4_SF_MAX_LINES];
        int part_end = off_g[g][m < noff_g[g] ? m : noff_g[g] - 1];
        memset(part, 0, sizeof(part));
        if (asf_spectral_group(b, sects_g[g], ns_g[g], off_g[g], noff_g[g],
                               sp_left, sp_right, sp_sym, sp_roots, sp_dims,
                               sp_mods, sp_off, sp_uns, part, part_end) != 0) {
            return -1;
        }
        for (int i = 0; i < part_end && i < res->n_lines; i++) {
            if (part[i] != 0) {
                res->lines[i] = part[i];
            }
        }
        for (int sfb = 0; sfb < m; sfb++) {
            res->sfb_cb[res->n_cb++] = cb_g[g][sfb];
        }
        for (int i = 0; i < ns_g[g] * 3; i++) {
            res->sects[res->n_sects++] = sects_g[g][i];
        }
    }
    for (int i = 0; i < noff_g[0]; i++) {
        res->offsets[i] = off_g[0][i];
    }
    res->n_offsets = noff_g[0];
    for (int g = 0; g < fr.num_groups; g++) {
        res->n_off_g[g] = noff_g[g];
        for (int i = 0; i < noff_g[g]; i++) {
            res->offsets_all[g * AC4_SF_MAX_SFB + i] = off_g[g][i];
        }
    }

    int64_t ref = br_read(b, 8);
    if (ref < 0) {
        return -1;
    }
    res->ref = (int)ref;
    int cur = (int)ref;
    int first = 0;
    for (int g = 0; g < fr.num_groups; g++) {
        int m = res->max_sfb[g];
        for (int sfb = 0; sfb < m; sfb++) {
            int idx = g * AC4_SF_MAX_SFB + sfb;
            res->sfs[idx] = INT32_MIN;
            if (cb_g[g][sfb] != 0 &&
                ac4_band_max(res->lines, res->n_lines, off_g[g], noff_g[g],
                             sfb) > 0) {
                if (first) {
                    int consumed = 0;
                    int c = tree_decode(b, sfL, sfR, sfS, sfRoot, &consumed);
                    if (c < 0) {
                        return -1;
                    }
                    cur += c - 60;
                } else {
                    first = 1;
                }
                res->sfs[idx] = cur;
            }
        }
    }

    int64_t has = br_read(b, 1);
    if (has < 0) {
        return -1;
    }
    if (has) {
        res->has_snf = 1;
        for (int g = 0; g < fr.num_groups; g++) {
            int m = res->max_sfb[g];
            for (int sfb = 0; sfb < m; sfb++) {
                int idx = g * AC4_SF_MAX_SFB + sfb;
                res->snf[idx] = INT32_MIN;
                int bm = ac4_band_max(res->lines, res->n_lines, off_g[g],
                                      noff_g[g], sfb);
                if (cb_g[g][sfb] == 0 || bm == 0) {
                    int consumed = 0;
                    int c = tree_decode(b, snfL, snfR, snfS, snfRoot,
                                        &consumed);
                    if (c < 0) {
                        return -1;
                    }
                    res->snf[idx] = c - 17;
                }
            }
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* Channel element walk (TS 103 190-1 4.2.6, Table 25)                 */
/* ------------------------------------------------------------------ */

#define AC4_MAX_CHANNELS 6

typedef struct {
    int codec_mode;
    int b_iframe;
    int has_aspx;
    int aspx_quant_mode_env;
    int aspx_start_freq;
    int aspx_stop_freq;
    int aspx_master_freq_scale;
    int aspx_interpolation;
    int aspx_preflat;
    int aspx_limiter;
    int aspx_noise_sbg;
    int aspx_num_env_bits_fixfix;
    int aspx_freq_res_mode;
    int coding_config;
    int is_pair;
    int stereo_sap;
    int stereo_sap_sr;
    int present[AC4_MAX_CHANNELS];
    ac4_sf_result sf[AC4_MAX_CHANNELS];
    long bitpos;
} ac4_element_result;

/* companding_control() -- clause 4.2.11. */
static int ac4_companding_control(bitreader *b, int num_chan)
{
    int sync = 0;
    if (num_chan > 1) {
        int64_t v = br_read(b, 1);
        if (v < 0) {
            return -1;
        }
        sync = (int)v;
    }
    int nc = sync ? 1 : num_chan;
    int all_on = 1;
    for (int i = 0; i < nc; i++) {
        int64_t v = br_read(b, 1);
        if (v < 0) {
            return -1;
        }
        if (!v) {
            all_on = 0;
        }
    }
    if (!all_on) {
        if (br_read(b, 1) < 0) {
            return -1;
        }
    }
    return 0;
}

/* chparam_info() -- clause 4.2.10.1; stereo side information, per group.

   ``sap`` and, for sap == 1, the per-group ``ms_used`` flags are what the
   packed-spectrum unmix (Table 113) needs, so they are returned rather than
   discarded.  ``ms_used`` is a flat ``groups * AC4_SF_MAX_SFB`` array. */
static int ac4_chparam_info(bitreader *b, const ac4_framing *fr,
                            const int32_t *sfL, const int32_t *sfR,
                            const int32_t *sfS, int sfRoot,
                            int *sap_out, int32_t *ms_used)
{
    int64_t sap = br_read(b, 2);
    if (sap < 0) {
        return -1;
    }
    if (sap_out != NULL) {
        *sap_out = (int)sap;
    }
    if (sap == 1) {
        for (int g = 0; g < fr->num_groups; g++) {
            int m = ac4_fr_max_sfb_g(fr, g);
            for (int sfb = 0; sfb < m; sfb++) {
                int64_t v = br_read(b, 1);
                if (v < 0) {
                    return -1;
                }
                if (ms_used != NULL) {
                    ms_used[g * AC4_SF_MAX_SFB + sfb] = (int)v;
                }
            }
        }
    } else if (sap == 3) {
        int64_t all_on = br_read(b, 1);
        if (all_on < 0) {
            return -1;
        }
        int used[AC4_SF_MAX_GROUPS][AC4_SF_MAX_SFB];
        memset(used, 0, sizeof(used));
        for (int g = 0; g < fr->num_groups; g++) {
            int m = ac4_fr_max_sfb_g(fr, g);
            if (m > AC4_SF_MAX_SFB) {
                return -1;
            }
            if (all_on) {
                for (int sfb = 0; sfb < m; sfb++) {
                    used[g][sfb] = 1;
                }
                continue;
            }
            for (int sfb = 0; sfb < m; sfb += 2) {
                int64_t v = br_read(b, 1);
                if (v < 0) {
                    return -1;
                }
                used[g][sfb] = (int)v;
                if (sfb + 1 < m) {
                    used[g][sfb + 1] = (int)v;
                }
            }
        }
        if (fr->num_groups != 1) {
            if (br_read(b, 1) < 0) {
                return -1;
            }
        }
        for (int g = 0; g < fr->num_groups; g++) {
            int m = ac4_fr_max_sfb_g(fr, g);
            for (int sfb = 0; sfb < m; sfb += 2) {
                if (used[g][sfb]) {
                    int consumed = 0;
                    if (tree_decode(b, sfL, sfR, sfS, sfRoot, &consumed) < 0) {
                        return -1;
                    }
                }
            }
        }
    }
    return 0;
}

/* asf_psy_info helpers need the framing; parse sf_info() here. */
static int ac4_parse_sf_info(bitreader *b, int spec_frontend, int b_lfe,
                             ac4_framing *fr)
{
    if (b_lfe) {
        return ac4_parse_framing_lfe(b, fr);
    }
    if (spec_frontend != 0) {
        return -1;                 /* SSF (speech frontend) is out of scope */
    }
    return ac4_parse_framing(b, fr);
}

/* sf_data() for one channel, using an already-parsed framing. */
static int ac4_sf_with_framing(
    bitreader *b, const ac4_framing *fr, const int32_t *sfb_rows,
    const int32_t *sfb_lens, int nrows,
    const int32_t *sp_left, const int32_t *sp_right, const int32_t *sp_sym,
    const int32_t *sp_roots, const int32_t *sp_dims, const int32_t *sp_mods,
    const int32_t *sp_off, const int32_t *sp_uns,
    const int32_t *sfL, const int32_t *sfR, const int32_t *sfS, int sfRoot,
    const int32_t *snfL, const int32_t *snfR, const int32_t *snfS, int snfRoot,
    ac4_sf_result *res)

{
    int32_t packed[AC4_FR_SIZE];
    ac4_framing_pack(fr, packed);
    return ac4_sf_impl(b, sfb_rows, sfb_lens, nrows,
                       sp_left, sp_right, sp_sym, sp_roots, sp_dims, sp_mods,
                       sp_off, sp_uns, sfL, sfR, sfS, sfRoot,
                       snfL, snfR, snfS, snfRoot, packed, res);
}

/* aspx_config() -- TS 103 190-1 Table 50; present only in I-frames. */
static int ac4_aspx_config(bitreader *b, ac4_element_result *er)
{
    int64_t v;
    v = br_read(b, 1); if (v < 0) { return -1; } er->aspx_quant_mode_env = v;
    v = br_read(b, 3); if (v < 0) { return -1; } er->aspx_start_freq = v;
    v = br_read(b, 2); if (v < 0) { return -1; } er->aspx_stop_freq = v;
    v = br_read(b, 1); if (v < 0) { return -1; } er->aspx_master_freq_scale = v;
    v = br_read(b, 1); if (v < 0) { return -1; } er->aspx_interpolation = v;
    v = br_read(b, 1); if (v < 0) { return -1; } er->aspx_preflat = v;
    v = br_read(b, 1); if (v < 0) { return -1; } er->aspx_limiter = v;
    v = br_read(b, 2); if (v < 0) { return -1; } er->aspx_noise_sbg = v;
    v = br_read(b, 1); if (v < 0) { return -1; } er->aspx_num_env_bits_fixfix = v;
    v = br_read(b, 2); if (v < 0) { return -1; } er->aspx_freq_res_mode = v;
    er->has_aspx = 1;
    return 0;
}

/* two_channel_data() -- clause 4.2.6.7.  Fills channels ``c0`` and ``c1``. */
static int ac4_two_channel_data(bitreader *b, int c0, int c1,
    const int32_t *sfb_rows, const int32_t *sfb_lens, int nrows,
    const int32_t *sp0, const int32_t *sp1, const int32_t *sp2,
    const int32_t *sp3, const int32_t *sp4, const int32_t *sp5,
    const int32_t *sp6, const int32_t *sp7,
    const int32_t *sfL, const int32_t *sfR, const int32_t *sfS, int sfRoot,
    const int32_t *snfL, const int32_t *snfR, const int32_t *snfS, int snfRoot,
    int *sap_out, ac4_element_result *er)
{
    ac4_framing fr0, fr1;
    int64_t mdct = br_read(b, 1);
    if (mdct < 0) {
        return -1;
    }
    if (mdct) {
        if (ac4_parse_framing(b, &fr0) != 0) {
            return -1;
        }
        if (ac4_chparam_info(b, &fr0, sfL, sfR, sfS, sfRoot, sap_out,
                             NULL) != 0) {
            return -1;
        }
        fr1 = fr0;
    } else {
        if (ac4_parse_framing(b, &fr0) != 0) {
            return -1;
        }
        if (ac4_parse_framing(b, &fr1) != 0) {
            return -1;
        }
    }
    if (ac4_sf_with_framing(b, &fr0, sfb_rows, sfb_lens, nrows,
                            sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                            sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                            &er->sf[c0]) != 0) {
        return -1;
    }
    er->present[c0] = 1;
    if (ac4_sf_with_framing(b, &fr1, sfb_rows, sfb_lens, nrows,
                            sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                            sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                            &er->sf[c1]) != 0) {
        return -1;
    }
    er->present[c1] = 1;
    return 0;
}

/* mono_data() -- clause 4.2.6.2. */
static int ac4_mono_data(bitreader *b, int channel, int b_lfe,
    const int32_t *sfb_rows, const int32_t *sfb_lens, int nrows,
    const int32_t *sp0, const int32_t *sp1, const int32_t *sp2,
    const int32_t *sp3, const int32_t *sp4, const int32_t *sp5,
    const int32_t *sp6, const int32_t *sp7,
    const int32_t *sfL, const int32_t *sfR, const int32_t *sfS, int sfRoot,
    const int32_t *snfL, const int32_t *snfR, const int32_t *snfS, int snfRoot,
    ac4_element_result *er)
{
    ac4_framing fr;
    if (b_lfe) {
        if (ac4_parse_framing_lfe(b, &fr) != 0) {
            return -1;
        }
    } else {
        int64_t frontend = br_read(b, 1);
        if (frontend < 0) {
            return -1;
        }
        if (ac4_parse_sf_info(b, (int)frontend, 0, &fr) != 0) {
            return -1;
        }
    }
    if (ac4_sf_with_framing(b, &fr, sfb_rows, sfb_lens, nrows,
                            sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                            sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                            &er->sf[channel]) != 0) {
        return -1;
    }
    er->present[channel] = 1;
    return 0;
}

/* 5_X_channel_element() -- Table 25, the codec-mode-1 (ASF) core.

   Stops after the core channels, as the reference does: the A-SPX data that
   follows is a separate rung. */
static int ac4_element_impl(
    bitreader *b, int b_iframe_global,
    const int32_t *sfb_rows, const int32_t *sfb_lens, int nrows,
    const int32_t *sp0, const int32_t *sp1, const int32_t *sp2,
    const int32_t *sp3, const int32_t *sp4, const int32_t *sp5,
    const int32_t *sp6, const int32_t *sp7,
    const int32_t *sfL, const int32_t *sfR, const int32_t *sfS, int sfRoot,
    const int32_t *snfL, const int32_t *snfR, const int32_t *snfS, int snfRoot,
    ac4_element_result *er)
{
    memset(er, 0, sizeof(*er));
    er->b_iframe = b_iframe_global;
    int64_t size = br_read(b, 15);
    if (size < 0) {
        return -1;
    }
    int64_t more = br_read(b, 1);
    if (more < 0) {
        return -1;
    }
    if (more && br_variable_bits(b, 7) < 0) {
        return -1;
    }
    int64_t mode = br_read(b, 3);
    if (mode < 0) {
        return -1;
    }
    er->codec_mode = (int)mode;
    if (b_iframe_global && (mode == 1 || mode == 2 || mode == 3 || mode == 4)) {
        if (ac4_aspx_config(b, er) != 0) {
            return -1;
        }
    }
    if (mode == 1) {
        if (ac4_mono_data(b, 0, 1, sfb_rows, sfb_lens, nrows,
                          sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                          sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                          er) != 0) {
            return -1;
        }
        if (ac4_companding_control(b, 5) != 0) {
            return -1;
        }
        int64_t cfg = br_read(b, 2);
        if (cfg < 0) {
            return -1;
        }
        er->coding_config = (int)cfg;
        if (cfg == 0) {
            if (br_read(b, 1) < 0) {         /* 2ch_mode */
                return -1;
            }
            if (ac4_two_channel_data(b, 1, 2, sfb_rows, sfb_lens, nrows,
                                     sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                                     sfL, sfR, sfS, sfRoot,
                                     snfL, snfR, snfS, snfRoot,
                                     &er->stereo_sap, er) != 0) {
                return -1;
            }
            if (ac4_two_channel_data(b, 3, 4, sfb_rows, sfb_lens, nrows,
                                     sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                                     sfL, sfR, sfS, sfRoot,
                                     snfL, snfR, snfS, snfRoot,
                                     &er->stereo_sap_sr, er) != 0) {
                return -1;
            }
            if (ac4_mono_data(b, 5, 0, sfb_rows, sfb_lens, nrows,
                              sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                              sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                              er) != 0) {
                return -1;
            }
        } else {
            return -1;                       /* coding_config 1..3: later */
        }
    } else if (mode == 4) {
        /* ASPX_ACPL_3: stereo core only (as the reference does). */
        if (b_iframe_global && br_read(b, 4) < 0) {  /* acpl_config_2ch */
            return -1;
        }
        if (ac4_mono_data(b, 0, 1, sfb_rows, sfb_lens, nrows,
                          sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                          sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                          er) != 0) {
            return -1;
        }
        if (ac4_companding_control(b, 2) != 0) {
            return -1;
        }
        if (ac4_two_channel_data(b, 1, 2, sfb_rows, sfb_lens, nrows,
                                 sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                                 sfL, sfR, sfS, sfRoot,
                                 snfL, snfR, snfS, snfRoot,
                                 &er->stereo_sap, er) != 0) {
            return -1;
        }
    } else {
        return -1;
    }
    er->bitpos = (long)b->pos;
    return 0;
}

/* channel_pair_element() -- TS 103 190-1 Table 22, the STEREO element.

   Different header from the 5_X element: after the 15-bit audio_size_value and
   its extension, the element is a 2-bit ``stereo_codec_mode`` (0 SIMPLE,
   1 ASPX), not a 3-bit 5_X codec_mode.  The pair shares the 5_X syntax for
   its A-SPX config (Table 50), companding control and ``two_channel_data``
   (4.2.6.7), so those are reused rather than re-derived.  The channels are
   L=1 and R=2, the same indices the render uses. */
static int ac4_pair_impl(
    bitreader *b, int b_iframe_global,
    const int32_t *sfb_rows, const int32_t *sfb_lens, int nrows,
    const int32_t *sp0, const int32_t *sp1, const int32_t *sp2,
    const int32_t *sp3, const int32_t *sp4, const int32_t *sp5,
    const int32_t *sp6, const int32_t *sp7,
    const int32_t *sfL, const int32_t *sfR, const int32_t *sfS, int sfRoot,
    const int32_t *snfL, const int32_t *snfR, const int32_t *snfS, int snfRoot,
    ac4_element_result *er)
{
    memset(er, 0, sizeof(*er));
    er->b_iframe = b_iframe_global;
    er->is_pair = 1;
    int64_t size = br_read(b, 15);
    if (size < 0) {
        return -1;
    }
    int64_t more = br_read(b, 1);
    if (more < 0) {
        return -1;
    }
    if (more && br_variable_bits(b, 7) < 0) {
        return -1;
    }
    int64_t mode = br_read(b, 2);         /* stereo_codec_mode */
    if (mode < 0) {
        return -1;
    }
    er->codec_mode = (int)mode;
    if (mode >= 2) {
        return -1;                        /* ASPX_ACPL pair: later */
    }
    if (b_iframe_global && mode == 1) {
        if (ac4_aspx_config(b, er) != 0) {
            return -1;
        }
    }
    if (mode == 1) {
        if (ac4_companding_control(b, 2) != 0) {
            return -1;
        }
    }
    if (ac4_two_channel_data(b, 1, 2, sfb_rows, sfb_lens, nrows,
                             sp0, sp1, sp2, sp3, sp4, sp5, sp6, sp7,
                             sfL, sfR, sfS, sfRoot, snfL, snfR, snfS, snfRoot,
                             &er->stereo_sap, er) != 0) {
        return -1;
    }
    er->bitpos = (long)b->pos;
    return 0;
}



#define AC4_SF_OFFSET 100   /* Pseudocode 21: sf_gain = 2^(0.25*(sf-100)) */

/* Per window group: rec_spec = sign(q)*|q|^(4/3); scaled = sf_gain*rec_spec.

   ``lines`` is the shared spectral lines array; ``offsets`` is
   ``offsets_all`` (``groups * AC4_SF_MAX_SFB``), ``sfs`` likewise.  ``out`` is
   ``groups * AC4_SF_MAX_LINES`` and holds each group's scaled lines.  Returns
   0 or -1. */
static int ac4_dequant_impl(
    const int32_t *lines, int n_lines, int groups,
    const int32_t *offsets, const int32_t *n_off,
    const int32_t *sfs, const int32_t *max_sfb,
    float *out, int out_stride)
{
    (void)n_lines;
    for (int g = 0; g < groups; g++) {
        int m = max_sfb[g];
        int no = n_off[g];
        const int32_t *off = offsets + (size_t)g * AC4_SF_MAX_SFB;
        const int32_t *sf = sfs + (size_t)g * AC4_SF_MAX_SFB;
        float *dst = out + (size_t)g * out_stride;
        int span = off[m < no ? m : no - 1];
        for (int i = 0; i < span && i < out_stride; i++) {
            dst[i] = 0.0f;
        }
        for (int sfb = 0; sfb < m && sfb + 1 < no; sfb++) {
            int32_t sfv = sf[sfb];
            if (sfv == INT32_MIN) {
                continue;
            }
            float gain = powf(2.0f, 0.25f * ((float)sfv - AC4_SF_OFFSET));
            int lo = off[sfb];
            int hi = off[sfb + 1];
            for (int i = lo; i < hi && i < out_stride; i++) {
                float q = (float)lines[i];
                float mag = powf(fabsf(q), 4.0f / 3.0f);
                dst[i] = (q < 0.0f ? -mag : mag) * gain;
            }
        }
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* IMDCT + window (TS 103 190-1 5.5.2 / 5.5.3, Pseudocode 60-64)        */
/* ------------------------------------------------------------------ */

/* I0(x): the modified Bessel function of the first kind, order 0, by the
   series in clause 5.5.3.  Converges quickly for the alpha range used here. */
static double ac4_bessel_i0(double x)
{
    double term = 1.0;
    double sum = 1.0;
    double half = x / 2.0;
    for (int k = 1; k < 64; k++) {
        term *= (half / k) * (half / k);
        sum += term;
        if (term < sum * 1e-16) {
            break;
        }
    }
    return sum;
}

/* KBD left/right halves for window length ``n`` and alpha (clause 5.5.3).

   W(N,n,alpha) = I0(pi*alpha*sqrt(1-(2n/N -1)^2)) / I0(pi*alpha);
   KBD_LEFT is the normalised cumulative sum over 0..n-1, KBD_RIGHT over
   n..2N-1.  ``out`` receives 2N values. */
static int ac4_kbd_window(int n, double alpha, double *out)
{
    double i0a = ac4_bessel_i0(M_PI * alpha);
    double c = 0.0;
    for (int i = 0; i < n; i++) {
        double t = 2.0 * i / (double)n - 1.0;
        c += ac4_bessel_i0(M_PI * alpha * sqrt(1.0 - t * t)) / i0a;
        out[i] = c;
    }
    c += ac4_bessel_i0(M_PI * alpha * sqrt(1.0 - 1.0)) / i0a; /* the N+1th */
    if (c <= 0.0) {
        return -1;
    }
    for (int i = 0; i < n; i++) {
        out[i] = sqrt(out[i] / c);
    }
    for (int i = 0; i < n; i++) {
        out[n + i] = out[n - 1 - i];
    }
    return 0;
}

/* The inverse MDCT of N coefficients to 2N windowed samples.

   Direct DCT-IV unfolding, identical to TS 103 190-1 clause 5.5.2
   (steps 1-5) evaluated in the cosine domain rather than via the N/2-point
   IFFT, so the result is independent of any FFT decomposition.  ``w`` is the
   2N synthesis window.  ``out`` receives 2N samples. */
static void ac4_imdct_direct(const float *X, int n, const double *w, double *out)
{
    double *folded = (double *)calloc((size_t)n, sizeof(double));
    if (folded == NULL) {
        return;
    }
    for (int i = 0; i < n; i++) {
        double s = 0.0;
        double ci = M_PI / n * (i + 0.5);
        for (int k = 0; k < n; k++) {
            s += (double)X[k] * cos(ci * (k + 0.5));
        }
        folded[i] = 2.0 / n * s;
    }
    int h = n / 2;
    for (int i = 0; i < h; i++) {
        out[i] = folded[h + i] * w[i];
        out[h + i] = -folded[n - 1 - i] * w[h + i];
        out[n + i] = -folded[h - 1 - i] * w[n + i];
        out[n + h + i] = -folded[i] * w[n + h + i];
    }
    free(folded);
}

/* ------------------------------------------------------------------ */
/* Spectral ungrouping (Pseudocode 25) + filterbank (Pseudocode 63-64)  */
/* ------------------------------------------------------------------ */

/* Spread the packed per-group lines into one spectrum per transform window.

   Grouped short blocks interleave their windows band by band, so undoing the
   grouping is what turns the packed vector into the per-window spectra the
   IMDCT consumes.  ``out`` is ``(num_windows, n_full)`` row-major. */
static int ac4_ungroup_impl(
    const int32_t *lines, int n_lines,
    const int32_t *sfb_rows, const int32_t *sfb_lens, int nrows,
    const int32_t *offsets_all, const int32_t *max_sfb,
    const int32_t *framing32, int n_full, float *out)
{
    ac4_framing fr;
    ac4_framing_unpack(framing32, &fr);
    for (int i = 0; i < fr.num_windows * n_full; i++) {
        out[i] = 0.0f;
    }
    (void)offsets_all;
    int win = 0;
    int k = 0;
    for (int g = 0; g < fr.num_groups; g++) {
        int m = max_sfb[g];
        int nwin = fr.nwin[g];
        int length = ac4_fr_length_g(&fr, g);
        const int32_t *row = ac4_sfb_row(length, sfb_rows, sfb_lens, nrows);
        if (row == NULL) {
            return -1;
        }
        for (int sfb = 0; sfb < m && sfb + 1 < AC4_SF_MAX_SFB; sfb++) {
            int lo = row[sfb];
            int hi = row[sfb + 1];
            int n = hi - lo;
            for (int w = 0; w < nwin; w++) {
                float *dst = out + (size_t)(win + w) * n_full + lo;
                for (int j = 0; j < n; j++) {
                    if (k + j < n_lines && lo + j < n_full) {
                        dst[j] = (float)lines[k + j];
                    }
                }
                k += n;
            }
        }
        win += nwin;
    }
    return 0;
}

/* ------------------------------------------------------------------ */
/* 64-band complex QMF bank (TS 103 190-1 5.7.3.2 / 5.7.4.2)          */
/* ------------------------------------------------------------------ */

#define AC4_QMF_SUBBANDS 64
#define AC4_QMF_WIN_COEF 640
#define AC4_QMF_FILT_LEN 640
#define AC4_QMF_SYN_LEN 1280

/* M[k][n] = exp(j*pi/128*(k+0.5)*(2n-1))  (Pseudocode 65). */
static void ac4_qmf_analysis_matrix(double _Complex *m)
{
    for (int k = 0; k < AC4_QMF_SUBBANDS; k++) {
        for (int n = 0; n < 2 * AC4_QMF_SUBBANDS; n++) {
            double ang = M_PI / (2 * AC4_QMF_SUBBANDS)
                       * (k + 0.5) * (2 * n - 1);
            m[k * 2 * AC4_QMF_SUBBANDS + n] = cos(ang) + I * sin(ang);
        }
    }
}

/* N[n][k] = exp(j*pi/128*(k+0.5)*(2n-255))/64  (clause 5.7.4.2 step 2). */
static void ac4_qmf_synthesis_matrix(double _Complex *n_mat)
{
    const int c = 4 * AC4_QMF_SUBBANDS - 1;
    for (int n = 0; n < 2 * AC4_QMF_SUBBANDS; n++) {
        for (int k = 0; k < AC4_QMF_SUBBANDS; k++) {
            double ang = M_PI / (2 * AC4_QMF_SUBBANDS)
                       * (k + 0.5) * (2 * n - c);
            n_mat[n * AC4_QMF_SUBBANDS + k] =
                (cos(ang) + I * sin(ang)) / AC4_QMF_SUBBANDS;
        }
    }
}

/* Pseudocode 65.  ``pcm`` is ``nts*64`` samples; ``filt`` is 640 in/out;
   ``out`` receives ``(64, nts)`` complex row-major (subband, timeslot). */
static int ac4_qmf_analyse_impl(const double *pcm, int nts,
                                const double *qwin, double *filt,
                                double _Complex *out)
{
    double _Complex *m = (double _Complex *)malloc(
        (size_t)AC4_QMF_SUBBANDS * 2 * AC4_QMF_SUBBANDS * sizeof(double _Complex));
    double *z = (double *)malloc((size_t)AC4_QMF_WIN_COEF * sizeof(double));
    double *u = (double *)malloc(
        (size_t)2 * AC4_QMF_SUBBANDS * sizeof(double));
    if (m == NULL || z == NULL || u == NULL) {
        free(m); free(z); free(u);
        return -1;
    }
    ac4_qmf_analysis_matrix(m);
    for (int ts = 0; ts < nts; ts++) {
        for (int sb = AC4_QMF_WIN_COEF - 1; sb >= AC4_QMF_SUBBANDS; sb--) {
            filt[sb] = filt[sb - AC4_QMF_SUBBANDS];
        }
        for (int sb = AC4_QMF_SUBBANDS - 1; sb >= 0; sb--) {
            filt[sb] = pcm[ts * AC4_QMF_SUBBANDS + AC4_QMF_SUBBANDS - 1 - sb];
        }
        for (int n = 0; n < AC4_QMF_WIN_COEF; n++) {
            z[n] = filt[n] * qwin[n];
        }
        for (int n = 0; n < 2 * AC4_QMF_SUBBANDS; n++) {
            double acc = z[n];
            for (int k = 1; k < 5; k++) {
                acc += z[n + k * 2 * AC4_QMF_SUBBANDS];
            }
            u[n] = acc;
        }
        for (int sb = 0; sb < AC4_QMF_SUBBANDS; sb++) {
            double _Complex acc = 0.0;
            for (int n = 0; n < 2 * AC4_QMF_SUBBANDS; n++) {
                acc += m[sb * 2 * AC4_QMF_SUBBANDS + n] * u[n];
            }
            out[(size_t)sb * nts + ts] = acc;
        }
    }
    free(m); free(z); free(u);
    return 0;
}

/* Pseudocode 66.  ``q`` is ``(64, nts)`` complex row-major; ``filt`` is 1280
   in/out; ``out`` receives ``nts*64`` real samples. */
static int ac4_qmf_synthesise_impl(const double _Complex *q, int nts,
                                   const double *qwin, double *filt,
                                   double *out)
{
    double _Complex *n_mat = (double _Complex *)malloc(
        (size_t)2 * AC4_QMF_SUBBANDS * AC4_QMF_SUBBANDS * sizeof(double _Complex));
    double *g = (double *)malloc((size_t)AC4_QMF_WIN_COEF * sizeof(double));
    if (n_mat == NULL || g == NULL) {
        free(n_mat); free(g);
        return -1;
    }
    ac4_qmf_synthesis_matrix(n_mat);
    for (int ts = 0; ts < nts; ts++) {
        for (int n = AC4_QMF_SYN_LEN - 1; n >= 2 * AC4_QMF_SUBBANDS; n--) {
            filt[n] = filt[n - 2 * AC4_QMF_SUBBANDS];
        }
        for (int n = 0; n < 2 * AC4_QMF_SUBBANDS; n++) {
            double _Complex acc = 0.0;
            for (int sb = 0; sb < AC4_QMF_SUBBANDS; sb++) {
                acc += n_mat[n * AC4_QMF_SUBBANDS + sb]
                     * q[(size_t)sb * nts + ts];
            }
            filt[n] = creal(acc);
        }
        for (int n = 0; n < 5; n++) {
            for (int sb = 0; sb < AC4_QMF_SUBBANDS; sb++) {
                g[128 * n + sb] = filt[256 * n + sb];
                g[128 * n + AC4_QMF_SUBBANDS + sb] =
                    filt[256 * n + 192 + sb];
            }
        }
        for (int sb = 0; sb < AC4_QMF_SUBBANDS; sb++) {
            double temp = g[sb] * qwin[sb];
            for (int n = 1; n < 10; n++) {
                temp += g[AC4_QMF_SUBBANDS * n + sb] * qwin[AC4_QMF_SUBBANDS * n + sb];
            }
            out[ts * AC4_QMF_SUBBANDS + sb] = temp;
        }
    }
    free(n_mat); free(g);
    return 0;
}

/* Step 5's w[n] for a block of length ``n`` following one of length ``n_prev``:
   zero shoulder, KBD transition of the shorter length, flat shoulder.  ``out``
   receives ``n`` values. */
static int ac4_left_window(int n, int n_prev, double *out)
{
    int nw = n < n_prev ? n : n_prev;
    int nskip = (n - nw) / 2;
    double *kbd = (double *)calloc((size_t)2 * nw, sizeof(double));
    if (kbd == NULL) {
        return -1;
    }
    if (ac4_kbd_window(nw, 0.0, kbd) != 0) {   /* alpha filled below */
        free(kbd);
        return -1;
    }
    free(kbd);
    for (int i = 0; i < n; i++) {
        out[i] = 1.0;
    }
    for (int i = 0; i < nskip; i++) {
        out[i] = 0.0;
    }
    double alpha = (nw >= 1536) ? 3.0 : (nw >= 768) ? 4.0
                 : (nw >= 384) ? 4.5 : (nw >= 192) ? 5.0 : 6.0;
    double *full = (double *)calloc((size_t)2 * nw, sizeof(double));
    if (full == NULL) {
        return -1;
    }
    if (ac4_kbd_window(nw, alpha, full) != 0) {
        free(full);
        return -1;
    }
    for (int i = 0; i < nw; i++) {
        out[nskip + i] = full[i];
    }
    free(full);
    return 0;
}

/* N spectral lines -> 2N UNWINDOWED samples, orthonormal (Pseudocode 60's
   unfolding with a sqrt(N/2) normalisation so every block has unit gain). */
static int ac4_imdct_raw(const float *X, int n, double *out)
{
    double *folded = (double *)calloc((size_t)n, sizeof(double));
    if (folded == NULL) {
        return -1;
    }
    double scale = 2.0 / n * sqrt(n / 2.0);
    for (int i = 0; i < n; i++) {
        double s = 0.0;
        double ci = M_PI / n * (i + 0.5);
        for (int k = 0; k < n; k++) {
            s += (double)X[k] * cos(ci * (k + 0.5));
        }
        folded[i] = scale * s;
    }
    int h = n / 2;
    double *a_b = folded + h;
    double *c_d = folded;
    for (int i = 0; i < h; i++) {
        out[i] = a_b[i];
        out[h + i] = -a_b[h - 1 - i];
        out[n + i] = -c_d[h - 1 - i];
        out[n + h + i] = -c_d[i];
    }
    free(folded);
    return 0;
}

/* Steps 5-6 over a sequence of blocks.  ``spectra`` is row-major
   ``sum(lengths) * n_full`` (one row per block, using only its first
   ``lengths[b]`` entries).  ``overlap`` is the inter-block state of
   ``n_full`` values; it is updated in place and ``n_prev`` in/out.  ``out``
   receives ``sum(lengths)`` PCM samples.  ``mode`` 0 = literal, 1 = reversed
   (the reading time-domain aliasing cancellation requires). */
static int ac4_synthesise_impl(
    const float *spectra, const int *lengths, int nblocks, int n_full,
    double *overlap, int *n_prev, int mode, double *out)
{
    int pos = 0;
    for (int b = 0; b < nblocks; b++) {
        int n = lengths[b];
        const float *X = spectra + (size_t)b * n_full;
        double *w = (double *)calloc((size_t)n, sizeof(double));
        double *raw = (double *)calloc((size_t)2 * n, sizeof(double));
        double *wr = (double *)calloc((size_t)*n_prev, sizeof(double));
        if (w == NULL || raw == NULL || wr == NULL) {
            free(w); free(raw); free(wr);
            return -1;
        }
        if (ac4_left_window(n, *n_prev, w) != 0 ||
            ac4_imdct_raw(X, n, raw) != 0 ||
            ac4_left_window(*n_prev, n, wr) != 0) {
            free(w); free(raw); free(wr);
            return -1;
        }
        int nskip = (n_full - n) / 2;
        int nskip_prev = (n_full - *n_prev) / 2;
        if (mode == 0) {
            int m = *n_prev < n ? *n_prev : n;
            for (int i = 0; i < m; i++) {
                overlap[nskip_prev + i] *= w[i];
            }
        } else {
            for (int i = 0; i < *n_prev; i++) {
                overlap[nskip_prev + i] *= wr[*n_prev - 1 - i];
            }
        }
        for (int i = 0; i < n; i++) {
            overlap[nskip + i] += raw[i] * w[i];
        }
        for (int i = 0; i < n; i++) {
            out[pos + i] = overlap[i];
        }
        int tlen = n_full - n;
        double tail[AC4_SF_MAX_LINES];
        for (int i = 0; i < tlen; i++) {
            tail[i] = overlap[n + i];
        }
        for (int i = 0; i < n_full; i++) {
            overlap[i] = 0.0;
        }
        for (int i = 0; i < tlen; i++) {
            overlap[i] = tail[i];
        }
        for (int i = 0; i < n; i++) {
            overlap[nskip + i] = raw[n + i];
        }
        *n_prev = n;
        pos += n;
        free(w); free(raw); free(wr);
    }
    return pos;
}

/* ------------------------------------------------------------------ */
/* CPython surface                                                     */
/* ------------------------------------------------------------------ */

static PyObject *ac4_spectral(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    PyObject *sects_o, *offs_o, *left_o, *right_o, *sym_o, *roots_o;
    PyObject *dims_o, *mods_o, *offs_cb_o, *unsigned_o;
    int bit_offset = 0;
    if (!PyArg_ParseTuple(args, "y*OOOOOOOOOO|i", &buf, &sects_o, &offs_o,
                          &left_o, &right_o, &sym_o, &roots_o, &dims_o,
                          &mods_o, &offs_cb_o, &unsigned_o, &bit_offset)) {
        return NULL;
    }
    PyArrayObject *arrays[8] = {
        (PyArrayObject *)PyArray_FROM_OTF(sects_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(offs_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(left_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(right_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(sym_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(roots_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(dims_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
        (PyArrayObject *)PyArray_FROM_OTF(mods_o, NPY_INT32, NPY_ARRAY_IN_ARRAY),
    };
    PyArrayObject *offs_cb = (PyArrayObject *)PyArray_FROM_OTF(
        offs_cb_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *uns = (PyArrayObject *)PyArray_FROM_OTF(
        unsigned_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    for (int i = 0; i < 8; i++) {
        if (arrays[i] == NULL) {
            for (int j = 0; j < 8; j++) {
                Py_XDECREF(arrays[j]);
            }
            Py_XDECREF(offs_cb);
            Py_XDECREF(uns);
            PyBuffer_Release(&buf);
            return NULL;
        }
    }
    PyArrayObject *sects = arrays[0];
    PyArrayObject *offs = arrays[1];
    PyArrayObject *left = arrays[2];
    PyArrayObject *right = arrays[3];
    PyArrayObject *sym = arrays[4];
    PyArrayObject *roots = arrays[5];
    PyArrayObject *dims = arrays[6];
    PyArrayObject *mods = arrays[7];

    int n_sects = (int)(PyArray_SIZE(sects) / 3);
    int n_offsets = (int)PyArray_SIZE(offs);
    int last_end = 0;
    if (n_sects > 0) {
        int s1 = ((int32_t *)PyArray_DATA(sects))[n_sects * 3 - 1];
        last_end = ((int32_t *)PyArray_DATA(offs))[
            s1 < n_offsets ? s1 : n_offsets - 1];
    }
    npy_intp dim = last_end > 0 ? last_end : 0;
    PyObject *lines_o = PyArray_ZEROS(1, &dim, NPY_INT32, 0);
    if (lines_o == NULL) {
        for (int j = 0; j < 8; j++) {
            Py_XDECREF(arrays[j]);
        }
        Py_XDECREF(offs_cb);
        Py_XDECREF(uns);
        PyBuffer_Release(&buf);
        return NULL;
    }
    bitreader r;
    br_init(&r, (const uint8_t *)buf.buf, (size_t)buf.len);
    r.pos = (size_t)bit_offset;
    int rc;
    Py_ssize_t newpos = (Py_ssize_t)bit_offset;
    Py_BEGIN_ALLOW_THREADS
    rc = asf_spectral_group(
        &r, (const int32_t *)PyArray_DATA(sects), n_sects,
        (const int32_t *)PyArray_DATA(offs), n_offsets,
        (const int32_t *)PyArray_DATA(left), (const int32_t *)PyArray_DATA(right),
        (const int32_t *)PyArray_DATA(sym), (const int32_t *)PyArray_DATA(roots),
        (const int32_t *)PyArray_DATA(dims), (const int32_t *)PyArray_DATA(mods),
        (const int32_t *)PyArray_DATA(offs_cb),
        (const int32_t *)PyArray_DATA(uns),
        (int32_t *)PyArray_DATA((PyArrayObject *)lines_o), (int)dim);
    newpos = (Py_ssize_t)r.pos;
    Py_END_ALLOW_THREADS

    for (int j = 0; j < 8; j++) {
        Py_DECREF(arrays[j]);
    }
    Py_DECREF(offs_cb);
    Py_DECREF(uns);
    PyBuffer_Release(&buf);
    if (rc != 0) {
        Py_DECREF(lines_o);
        PyErr_SetString(PyExc_ValueError, "malformed ASF spectral data");
        return NULL;
    }
    return Py_BuildValue("(Nn)", lines_o, newpos);
}

static PyObject *ac4_sf_to_dict(const ac4_sf_result *res, Py_ssize_t newpos)
{
    npy_intp nl = res->n_lines;
    PyObject *lines_o = PyArray_ZEROS(1, &nl, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)lines_o), res->lines,
           (size_t)nl * sizeof(int32_t));
    npy_intp ns = res->n_sects;
    PyObject *sects_o = PyArray_ZEROS(1, &ns, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)sects_o), res->sects,
           (size_t)ns * sizeof(int32_t));
    npy_intp nc = res->n_cb;
    PyObject *cb_o = PyArray_ZEROS(1, &nc, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)cb_o), res->sfb_cb,
           (size_t)nc * sizeof(int32_t));
    npy_intp ng = res->groups * AC4_SF_MAX_SFB;
    PyObject *sfs_o = PyArray_ZEROS(1, &ng, NPY_INT32, 0);
    PyObject *snf_arr = PyArray_ZEROS(1, &ng, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)sfs_o), res->sfs,
           (size_t)ng * sizeof(int32_t));
    memcpy(PyArray_DATA((PyArrayObject *)snf_arr), res->snf,
           (size_t)ng * sizeof(int32_t));
    npy_intp no = res->n_offsets;
    PyObject *off_o = PyArray_ZEROS(1, &no, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)off_o), res->offsets,
           (size_t)no * sizeof(int32_t));
    npy_intp noa = (npy_intp)res->groups * AC4_SF_MAX_SFB;
    PyObject *offa_o = PyArray_ZEROS(1, &noa, NPY_INT32, 0);
    npy_intp nog = res->groups;
    PyObject *nog_o = PyArray_ZEROS(1, &nog, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)offa_o), res->offsets_all,
           (size_t)noa * sizeof(int32_t));
    memcpy(PyArray_DATA((PyArrayObject *)nog_o), res->n_off_g,
           (size_t)nog * sizeof(int32_t));
    npy_intp mdim = res->groups;
    PyObject *max_o = PyArray_ZEROS(1, &mdim, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)max_o), res->max_sfb,
           (size_t)mdim * sizeof(int32_t));
    npy_intp fdim = AC4_FR_SIZE;
    PyObject *fr_o = PyArray_ZEROS(1, &fdim, NPY_INT32, 0);
    memcpy(PyArray_DATA((PyArrayObject *)fr_o), res->framing,
           sizeof(res->framing));
    return Py_BuildValue(
        "{s:N,s:N,s:N,s:N,s:N,s:N,s:N,s:N,s:N,s:N,s:i,s:i,s:i,s:n}",
        "lines", lines_o, "sects", sects_o, "sfb_cb", cb_o, "sfs", sfs_o,
        "snf", snf_arr, "offsets", off_o, "offsets_all", offa_o,
        "n_off_g", nog_o, "max_sfb", max_o, "framing", fr_o,
        "groups", res->groups, "has_snf", res->has_snf, "ref", res->ref,
        "bitpos", newpos);
}

static PyObject *ac4_framing_parse(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    int bit_offset = 0;
    int b_lfe = 0;
    if (!PyArg_ParseTuple(args, "y*|ii", &buf, &bit_offset, &b_lfe)) {
        return NULL;
    }
    bitreader r;
    br_init(&r, (const uint8_t *)buf.buf, (size_t)buf.len);
    r.pos = (size_t)bit_offset;
    ac4_framing fr;
    int frc = b_lfe ? ac4_parse_framing_lfe(&r, &fr)
                    : ac4_parse_framing(&r, &fr);
    Py_ssize_t newpos = (Py_ssize_t)r.pos;
    PyBuffer_Release(&buf);
    if (frc != 0) {
        PyErr_SetString(PyExc_ValueError, "malformed ASF framing");
        return NULL;
    }
    int32_t packed[AC4_FR_SIZE];
    ac4_framing_pack(&fr, packed);
    npy_intp dim = AC4_FR_SIZE;
    PyObject *arr = PyArray_SimpleNew(1, &dim, NPY_INT32);
    memcpy(PyArray_DATA((PyArrayObject *)arr), packed, sizeof(packed));
    return Py_BuildValue("(Nn)", arr, newpos);
}

static PyObject *ac4_sf(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    PyObject *rows_o, *lens_o, *sp_o, *sf_o, *snf_o, *framing_o;
    int bit_offset = 0;
    if (!PyArg_ParseTuple(args, "y*OOOOOO|i", &buf, &rows_o, &lens_o, &sp_o,
                          &sf_o, &snf_o, &framing_o, &bit_offset)) {
        return NULL;
    }
    PyArrayObject *framing = (PyArrayObject *)PyArray_FROM_OTF(
        framing_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (framing == NULL) {
        PyBuffer_Release(&buf);
        return NULL;
    }
    PyArrayObject *rows = (PyArrayObject *)PyArray_FROM_OTF(
        rows_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *lens = (PyArrayObject *)PyArray_FROM_OTF(
        lens_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (rows == NULL || lens == NULL) {
        Py_XDECREF(rows);
        Py_XDECREF(lens);
        PyBuffer_Release(&buf);
        return NULL;
    }
    PyArrayObject *sp[8];
    PyObject *sp_seq[8];
    if (!PySequence_Check(sp_o) || PySequence_Size(sp_o) != 8) {
        PyErr_SetString(PyExc_ValueError, "spectral tables must be an 8-list");
        Py_DECREF(rows);
        Py_DECREF(lens);
        PyBuffer_Release(&buf);
        return NULL;
    }
    for (int i = 0; i < 8; i++) {
        sp_seq[i] = PySequence_GetItem(sp_o, i);
        sp[i] = (PyArrayObject *)PyArray_FROM_OTF(sp_seq[i], NPY_INT32,
                                                 NPY_ARRAY_IN_ARRAY);
    }
    PyArrayObject *sf[4], *snf[4];
    PyObject *sf_seq[4], *snf_seq[4];
    for (int i = 0; i < 4; i++) {
        sf_seq[i] = PySequence_GetItem(sf_o, i);
        snf_seq[i] = PySequence_GetItem(snf_o, i);
        sf[i] = (PyArrayObject *)PyArray_FROM_OTF(sf_seq[i], NPY_INT32,
                                                  NPY_ARRAY_IN_ARRAY);
        snf[i] = (PyArrayObject *)PyArray_FROM_OTF(snf_seq[i], NPY_INT32,
                                                   NPY_ARRAY_IN_ARRAY);
    }
    int bad = 0;
    for (int i = 0; i < 8; i++) {
        if (sp[i] == NULL) {
            bad = 1;
        }
    }
    for (int i = 0; i < 4; i++) {
        if (sf[i] == NULL || snf[i] == NULL) {
            bad = 1;
        }
    }
    if (bad) {
        for (int i = 0; i < 8; i++) {
            Py_XDECREF(sp[i]);
            Py_XDECREF(sp_seq[i]);
        }
        for (int i = 0; i < 4; i++) {
            Py_XDECREF(sf[i]);
            Py_XDECREF(snf[i]);
            Py_XDECREF(sf_seq[i]);
            Py_XDECREF(snf_seq[i]);
        }
        Py_DECREF(rows);
        Py_DECREF(lens);
        PyBuffer_Release(&buf);
        return NULL;
    }
    bitreader r;
    br_init(&r, (const uint8_t *)buf.buf, (size_t)buf.len);
    r.pos = (size_t)bit_offset;
    ac4_sf_result *res = (ac4_sf_result *)calloc(1, sizeof(ac4_sf_result));
    if (res == NULL) {
        PyErr_NoMemory();
        bad = 1;
    }
    int sf_root = PyArray_SIZE(sf[3]) ? ((int32_t *)PyArray_DATA(sf[3]))[0] : 0;
    int snf_root = PyArray_SIZE(snf[3]) ? ((int32_t *)PyArray_DATA(snf[3]))[0] : 0;
    int rc = -1;
    if (!bad) {
        Py_BEGIN_ALLOW_THREADS
        rc = ac4_sf_impl(
            &r, (const int32_t *)PyArray_DATA(rows), (const int32_t *)PyArray_DATA(lens),
            (int)PyArray_SIZE(lens),
            (const int32_t *)PyArray_DATA(sp[0]), (const int32_t *)PyArray_DATA(sp[1]),
            (const int32_t *)PyArray_DATA(sp[2]), (const int32_t *)PyArray_DATA(sp[3]),
            (const int32_t *)PyArray_DATA(sp[4]), (const int32_t *)PyArray_DATA(sp[5]),
            (const int32_t *)PyArray_DATA(sp[6]), (const int32_t *)PyArray_DATA(sp[7]),
            (const int32_t *)PyArray_DATA(sf[0]), (const int32_t *)PyArray_DATA(sf[1]),
            (const int32_t *)PyArray_DATA(sf[2]), sf_root,
            (const int32_t *)PyArray_DATA(snf[0]), (const int32_t *)PyArray_DATA(snf[1]),
            (const int32_t *)PyArray_DATA(snf[2]), snf_root,
            (const int32_t *)PyArray_DATA(framing), res);
        Py_END_ALLOW_THREADS
    }
    Py_ssize_t newpos = (Py_ssize_t)r.pos;
    for (int i = 0; i < 8; i++) {
        Py_XDECREF(sp[i]);
        Py_XDECREF(sp_seq[i]);
    }
    for (int i = 0; i < 4; i++) {
        Py_XDECREF(sf[i]);
        Py_XDECREF(snf[i]);
        Py_XDECREF(sf_seq[i]);
        Py_XDECREF(snf_seq[i]);
    }
    Py_DECREF(rows);
    Py_DECREF(lens);
    Py_DECREF(framing);
    PyBuffer_Release(&buf);
    if (bad || rc != 0) {
        free(res);
        PyErr_SetString(PyExc_ValueError, "malformed ASF sf_data");
        return NULL;
    }

    PyObject *out = ac4_sf_to_dict(res, newpos);
    free(res);
    return out;
}

static PyObject *ac4_ungroup(PyObject *self, PyObject *args)
{
    PyObject *lines_o, *rows_o, *lens_o, *off_o, *msfb_o, *fr_o;
    int n_full;
    if (!PyArg_ParseTuple(args, "OOOOOOi", &lines_o, &rows_o, &lens_o, &off_o,
                          &msfb_o, &fr_o, &n_full)) {
        return NULL;
    }
    PyArrayObject *lines = (PyArrayObject *)PyArray_FROM_OTF(
        lines_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *rows = (PyArrayObject *)PyArray_FROM_OTF(
        rows_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *lens = (PyArrayObject *)PyArray_FROM_OTF(
        lens_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *off = (PyArrayObject *)PyArray_FROM_OTF(
        off_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *msfb = (PyArrayObject *)PyArray_FROM_OTF(
        msfb_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *fr = (PyArrayObject *)PyArray_FROM_OTF(
        fr_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (lines == NULL || rows == NULL || lens == NULL || off == NULL ||
        msfb == NULL || fr == NULL) {
        Py_XDECREF(lines); Py_XDECREF(rows); Py_XDECREF(lens);
        Py_XDECREF(off); Py_XDECREF(msfb); Py_XDECREF(fr);
        return NULL;
    }
    int nwin = ((int32_t *)PyArray_DATA(fr))[5];
    npy_intp dims[2] = {nwin, n_full};
    PyObject *out_o = PyArray_ZEROS(2, dims, NPY_FLOAT32, 0);
    if (out_o == NULL) {
        Py_DECREF(lines); Py_DECREF(rows); Py_DECREF(lens);
        Py_DECREF(off); Py_DECREF(msfb); Py_DECREF(fr);
        return NULL;
    }
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_ungroup_impl(
        (const int32_t *)PyArray_DATA(lines), (int)PyArray_SIZE(lines),
        (const int32_t *)PyArray_DATA(rows), (const int32_t *)PyArray_DATA(lens),
        (int)PyArray_SIZE(lens), (const int32_t *)PyArray_DATA(off),
        (const int32_t *)PyArray_DATA(msfb),
        (const int32_t *)PyArray_DATA(fr), n_full,
        (float *)PyArray_DATA((PyArrayObject *)out_o));
    Py_END_ALLOW_THREADS
    Py_DECREF(lines); Py_DECREF(rows); Py_DECREF(lens);
    Py_DECREF(off); Py_DECREF(msfb); Py_DECREF(fr);
    if (rc != 0) {
        Py_DECREF(out_o);
        PyErr_SetString(PyExc_ValueError, "malformed input for ungroup");
        return NULL;
    }
    return out_o;
}

static PyObject *ac4_synthesise(PyObject *self, PyObject *args)
{
    PyObject *spec_o, *len_o, *overlap_o;
    int n_full, n_prev, mode;
    if (!PyArg_ParseTuple(args, "OOiiOi", &spec_o, &len_o, &n_full, &n_prev,
                          &overlap_o, &mode)) {
        return NULL;
    }
    PyArrayObject *spec = (PyArrayObject *)PyArray_FROM_OTF(
        spec_o, NPY_FLOAT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *lengths = (PyArrayObject *)PyArray_FROM_OTF(
        len_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *ov = (PyArrayObject *)PyArray_FROM_OTF(
        overlap_o, NPY_FLOAT64, NPY_ARRAY_INOUT_ARRAY2);
    if (spec == NULL || lengths == NULL || ov == NULL) {
        Py_XDECREF(spec);
        Py_XDECREF(lengths);
        Py_XDECREF(ov);
        return NULL;
    }
    int nblocks = (int)PyArray_SIZE(lengths);
    if (PyArray_NDIM(spec) != 2 || (int)PyArray_DIM(spec, 0) != nblocks ||
        (int)PyArray_DIM(spec, 1) != n_full ||
        (int)PyArray_SIZE(ov) != n_full) {
        Py_DECREF(spec);
        Py_DECREF(lengths);
        PyArray_ResolveWritebackIfCopy(ov);
        Py_DECREF(ov);
        PyErr_SetString(PyExc_ValueError, "synthesise shape mismatch");
        return NULL;
    }
    int total = 0;
    for (int i = 0; i < nblocks; i++) {
        total += ((int32_t *)PyArray_DATA(lengths))[i];
    }
    npy_intp dim = total > 0 ? total : 0;
    PyObject *out_o = PyArray_ZEROS(1, &dim, NPY_FLOAT64, 0);
    if (out_o == NULL) {
        Py_DECREF(spec);
        Py_DECREF(lengths);
        PyArray_ResolveWritebackIfCopy(ov);
        Py_DECREF(ov);
        return NULL;
    }
    int np = n_prev;
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_synthesise_impl(
        (const float *)PyArray_DATA(spec),
        (const int *)PyArray_DATA(lengths), nblocks, n_full,
        (double *)PyArray_DATA(ov), &np, mode,
        (double *)PyArray_DATA((PyArrayObject *)out_o));
    Py_END_ALLOW_THREADS
    Py_DECREF(spec);
    Py_DECREF(lengths);
    PyArray_ResolveWritebackIfCopy(ov);
    Py_DECREF(ov);
    if (rc < 0) {
        Py_DECREF(out_o);
        PyErr_SetString(PyExc_ValueError, "malformed filterbank input");
        return NULL;
    }
    return Py_BuildValue("(Ni)", out_o, np);
}

static PyObject *ac4_kbd(PyObject *self, PyObject *args)
{
    int n;
    double alpha;
    if (!PyArg_ParseTuple(args, "id", &n, &alpha)) {
        return NULL;
    }
    if (n <= 0) {
        PyErr_SetString(PyExc_ValueError, "window length must be positive");
        return NULL;
    }
    npy_intp dim = (npy_intp)2 * n;
    PyObject *out_o = PyArray_ZEROS(1, &dim, NPY_FLOAT64, 0);
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_kbd_window(n, alpha, (double *)PyArray_DATA((PyArrayObject *)out_o));
    Py_END_ALLOW_THREADS
    if (rc != 0) {
        Py_DECREF(out_o);
        PyErr_SetString(PyExc_ValueError, "degenerate KBD window");
        return NULL;
    }
    return out_o;
}

static PyObject *ac4_imdct(PyObject *self, PyObject *args)
{
    PyObject *x_o, *w_o;
    if (!PyArg_ParseTuple(args, "OO", &x_o, &w_o)) {
        return NULL;
    }
    PyArrayObject *x = (PyArrayObject *)PyArray_FROM_OTF(
        x_o, NPY_FLOAT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *w = (PyArrayObject *)PyArray_FROM_OTF(
        w_o, NPY_FLOAT64, NPY_ARRAY_IN_ARRAY);
    if (x == NULL || w == NULL) {
        Py_XDECREF(x);
        Py_XDECREF(w);
        return NULL;
    }
    int n = (int)PyArray_SIZE(x);
    if ((int)PyArray_SIZE(w) != 2 * n) {
        Py_DECREF(x);
        Py_DECREF(w);
        PyErr_SetString(PyExc_ValueError, "window must be twice the block");
        return NULL;
    }
    npy_intp dim = (npy_intp)2 * n;
    PyObject *out_o = PyArray_ZEROS(1, &dim, NPY_FLOAT64, 0);
    if (out_o == NULL) {
        Py_DECREF(x);
        Py_DECREF(w);
        return NULL;
    }
    Py_BEGIN_ALLOW_THREADS
    ac4_imdct_direct((const float *)PyArray_DATA(x), n,
                     (const double *)PyArray_DATA(w),
                     (double *)PyArray_DATA((PyArrayObject *)out_o));
    Py_END_ALLOW_THREADS
    Py_DECREF(x);
    Py_DECREF(w);
    return out_o;
}

static PyObject *ac4_dequant(PyObject *self, PyObject *args)
{
    PyObject *lines_o, *off_o, *nog_o, *sfs_o, *msfb_o;
    int out_stride;
    if (!PyArg_ParseTuple(args, "OOOOOi", &lines_o, &off_o, &nog_o,
                          &sfs_o, &msfb_o, &out_stride)) {
        return NULL;
    }
    PyArrayObject *lines = (PyArrayObject *)PyArray_FROM_OTF(
        lines_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *off = (PyArrayObject *)PyArray_FROM_OTF(
        off_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *nog = (PyArrayObject *)PyArray_FROM_OTF(
        nog_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *sfs = (PyArrayObject *)PyArray_FROM_OTF(
        sfs_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *msfb = (PyArrayObject *)PyArray_FROM_OTF(
        msfb_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (lines == NULL || off == NULL || nog == NULL || sfs == NULL ||
        msfb == NULL) {
        Py_XDECREF(lines); Py_XDECREF(off); Py_XDECREF(nog);
        Py_XDECREF(sfs); Py_XDECREF(msfb);
        return NULL;
    }
    int groups = (int)PyArray_SIZE(msfb);
    npy_intp dims[2] = {groups, out_stride};
    PyObject *out_o = PyArray_ZEROS(2, dims, NPY_FLOAT32, 0);
    if (out_o == NULL) {
        Py_DECREF(lines); Py_DECREF(off); Py_DECREF(nog);
        Py_DECREF(sfs); Py_DECREF(msfb);
        return NULL;
    }
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_dequant_impl(
        (const int32_t *)PyArray_DATA(lines),
        (int)PyArray_SIZE(lines), groups,
        (const int32_t *)PyArray_DATA(off), (const int32_t *)PyArray_DATA(nog),
        (const int32_t *)PyArray_DATA(sfs),
        (const int32_t *)PyArray_DATA(msfb),
        (float *)PyArray_DATA((PyArrayObject *)out_o), out_stride);
    Py_END_ALLOW_THREADS
    Py_DECREF(lines); Py_DECREF(off); Py_DECREF(nog);
    Py_DECREF(sfs); Py_DECREF(msfb);
    if (rc != 0) {
        Py_DECREF(out_o);
        PyErr_SetString(PyExc_ValueError, "malformed sf result for dequant");
        return NULL;
    }
    return out_o;
}

static PyObject *ac4_qmf_analyse(PyObject *self, PyObject *args)
{
    PyObject *pcm_o, *filt_o, *qwin_o;
    if (!PyArg_ParseTuple(args, "OOO", &pcm_o, &filt_o, &qwin_o)) {
        return NULL;
    }
    PyArrayObject *pcm = (PyArrayObject *)PyArray_FROM_OTF(
        pcm_o, NPY_FLOAT64, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *filt = (PyArrayObject *)PyArray_FROM_OTF(
        filt_o, NPY_FLOAT64, NPY_ARRAY_INOUT_ARRAY2);
    PyArrayObject *qwin = (PyArrayObject *)PyArray_FROM_OTF(
        qwin_o, NPY_FLOAT64, NPY_ARRAY_IN_ARRAY);
    if (pcm == NULL || filt == NULL || qwin == NULL) {
        Py_XDECREF(pcm); Py_XDECREF(filt); Py_XDECREF(qwin);
        return NULL;
    }
    if (PyArray_SIZE(qwin) != AC4_QMF_WIN_COEF ||
        PyArray_SIZE(filt) != AC4_QMF_FILT_LEN) {
        Py_DECREF(pcm); Py_DECREF(filt); Py_DECREF(qwin);
        PyErr_SetString(PyExc_ValueError, "QMF: bad window/filter size");
        return NULL;
    }
    int nts = (int)(PyArray_SIZE(pcm) / AC4_QMF_SUBBANDS);
    npy_intp dims[2] = {AC4_QMF_SUBBANDS, nts};
    PyObject *out_o = PyArray_SimpleNew(2, dims, NPY_COMPLEX128);
    if (out_o == NULL) {
        Py_DECREF(pcm); Py_DECREF(filt); Py_DECREF(qwin);
        return NULL;
    }
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_qmf_analyse_impl(
        (const double *)PyArray_DATA(pcm), nts,
        (const double *)PyArray_DATA(qwin),
        (double *)PyArray_DATA(filt),
        (double _Complex *)PyArray_DATA((PyArrayObject *)out_o));
    Py_END_ALLOW_THREADS
    PyArray_ResolveWritebackIfCopy(filt);
    if (rc != 0) {
        Py_DECREF(pcm); Py_DECREF(filt); Py_DECREF(qwin);
        Py_DECREF(out_o);
        PyErr_NoMemory();
        return NULL;
    }
    Py_DECREF(pcm); Py_DECREF(filt); Py_DECREF(qwin);
    return out_o;
}

static PyObject *ac4_qmf_synthesise(PyObject *self, PyObject *args)
{
    PyObject *q_o, *filt_o, *qwin_o;
    if (!PyArg_ParseTuple(args, "OOO", &q_o, &filt_o, &qwin_o)) {
        return NULL;
    }
    PyArrayObject *q = (PyArrayObject *)PyArray_FROM_OTF(
        q_o, NPY_COMPLEX128, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *filt = (PyArrayObject *)PyArray_FROM_OTF(
        filt_o, NPY_FLOAT64, NPY_ARRAY_INOUT_ARRAY2);
    PyArrayObject *qwin = (PyArrayObject *)PyArray_FROM_OTF(
        qwin_o, NPY_FLOAT64, NPY_ARRAY_IN_ARRAY);
    if (q == NULL || filt == NULL || qwin == NULL) {
        Py_XDECREF(q); Py_XDECREF(filt); Py_XDECREF(qwin);
        return NULL;
    }
    if (PyArray_NDIM(q) != 2 ||
        PyArray_DIM(q, 0) != AC4_QMF_SUBBANDS ||
        PyArray_SIZE(qwin) != AC4_QMF_WIN_COEF ||
        PyArray_SIZE(filt) != AC4_QMF_SYN_LEN) {
        Py_DECREF(q); Py_DECREF(filt); Py_DECREF(qwin);
        PyErr_SetString(PyExc_ValueError, "QMF: bad matrix/window/filter size");
        return NULL;
    }
    int nts = (int)PyArray_DIM(q, 1);
    npy_intp dim = (npy_intp)nts * AC4_QMF_SUBBANDS;
    PyObject *out_o = PyArray_SimpleNew(1, &dim, NPY_FLOAT64);
    if (out_o == NULL) {
        Py_DECREF(q); Py_DECREF(filt); Py_DECREF(qwin);
        return NULL;
    }
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_qmf_synthesise_impl(
        (const double _Complex *)PyArray_DATA(q), nts,
        (const double *)PyArray_DATA(qwin),
        (double *)PyArray_DATA(filt),
        (double *)PyArray_DATA((PyArrayObject *)out_o));
    Py_END_ALLOW_THREADS
    PyArray_ResolveWritebackIfCopy(filt);
    if (rc != 0) {
        Py_DECREF(q); Py_DECREF(filt); Py_DECREF(qwin);
        Py_DECREF(out_o);
        PyErr_NoMemory();
        return NULL;
    }
    Py_DECREF(q); Py_DECREF(filt); Py_DECREF(qwin);
    return out_o;
}

static PyObject *ac4_element(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    PyObject *rows_o, *lens_o, *sp_o, *sf_o, *snf_o;
    int b_iframe = 0;
    int is_pair = 0;
    if (!PyArg_ParseTuple(args, "y*OOOOO|ii", &buf, &rows_o, &lens_o, &sp_o,
                          &sf_o, &snf_o, &b_iframe, &is_pair)) {
        return NULL;
    }
    PyArrayObject *rows = (PyArrayObject *)PyArray_FROM_OTF(
        rows_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *lens = (PyArrayObject *)PyArray_FROM_OTF(
        lens_o, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (rows == NULL || lens == NULL) {
        Py_XDECREF(rows);
        Py_XDECREF(lens);
        PyBuffer_Release(&buf);
        return NULL;
    }
    if (!PySequence_Check(sp_o) || PySequence_Size(sp_o) != 8 ||
        !PySequence_Check(sf_o) || PySequence_Size(sf_o) != 4 ||
        !PySequence_Check(snf_o) || PySequence_Size(snf_o) != 4) {
        PyErr_SetString(PyExc_ValueError, "bad codebook table lists");
        Py_DECREF(rows);
        Py_DECREF(lens);
        PyBuffer_Release(&buf);
        return NULL;
    }
    PyArrayObject *sp[8], *sf[4], *snf[4];
    PyObject *sp_seq[8], *sf_seq[4], *snf_seq[4];
    int bad = 0;
    for (int i = 0; i < 8; i++) {
        sp_seq[i] = PySequence_GetItem(sp_o, i);
        sp[i] = (PyArrayObject *)PyArray_FROM_OTF(sp_seq[i], NPY_INT32,
                                                 NPY_ARRAY_IN_ARRAY);
        if (sp[i] == NULL) {
            bad = 1;
        }
    }
    for (int i = 0; i < 4; i++) {
        sf_seq[i] = PySequence_GetItem(sf_o, i);
        snf_seq[i] = PySequence_GetItem(snf_o, i);
        sf[i] = (PyArrayObject *)PyArray_FROM_OTF(sf_seq[i], NPY_INT32,
                                                  NPY_ARRAY_IN_ARRAY);
        snf[i] = (PyArrayObject *)PyArray_FROM_OTF(snf_seq[i], NPY_INT32,
                                                   NPY_ARRAY_IN_ARRAY);
        if (sf[i] == NULL || snf[i] == NULL) {
            bad = 1;
        }
    }
    if (bad) {
        for (int i = 0; i < 8; i++) {
            Py_XDECREF(sp[i]); Py_XDECREF(sp_seq[i]);
        }
        for (int i = 0; i < 4; i++) {
            Py_XDECREF(sf[i]); Py_XDECREF(snf[i]);
            Py_XDECREF(sf_seq[i]); Py_XDECREF(snf_seq[i]);
        }
        Py_DECREF(rows);
        Py_DECREF(lens);
        PyBuffer_Release(&buf);
        return NULL;
    }
    int sf_root = PyArray_SIZE(sf[3]) ? ((int32_t *)PyArray_DATA(sf[3]))[0] : 0;
    int snf_root = PyArray_SIZE(snf[3]) ? ((int32_t *)PyArray_DATA(snf[3]))[0] : 0;
    bitreader r;
    br_init(&r, (const uint8_t *)buf.buf, (size_t)buf.len);
    ac4_element_result *er = (ac4_element_result *)calloc(
        1, sizeof(ac4_element_result));
    int rc = -1;
    if (er == NULL) {
        PyErr_NoMemory();
        bad = 1;
    }
    if (!bad) {
        Py_BEGIN_ALLOW_THREADS
        if (is_pair) {
            rc = ac4_pair_impl(
                &r, b_iframe,
                (const int32_t *)PyArray_DATA(rows), (const int32_t *)PyArray_DATA(lens),
                (int)PyArray_SIZE(lens),
                (const int32_t *)PyArray_DATA(sp[0]), (const int32_t *)PyArray_DATA(sp[1]),
                (const int32_t *)PyArray_DATA(sp[2]), (const int32_t *)PyArray_DATA(sp[3]),
                (const int32_t *)PyArray_DATA(sp[4]), (const int32_t *)PyArray_DATA(sp[5]),
                (const int32_t *)PyArray_DATA(sp[6]), (const int32_t *)PyArray_DATA(sp[7]),
                (const int32_t *)PyArray_DATA(sf[0]), (const int32_t *)PyArray_DATA(sf[1]),
                (const int32_t *)PyArray_DATA(sf[2]), sf_root,
                (const int32_t *)PyArray_DATA(snf[0]), (const int32_t *)PyArray_DATA(snf[1]),
                (const int32_t *)PyArray_DATA(snf[2]), snf_root,
                er);
        } else {
            rc = ac4_element_impl(
                &r, b_iframe,
                (const int32_t *)PyArray_DATA(rows), (const int32_t *)PyArray_DATA(lens),
                (int)PyArray_SIZE(lens),
                (const int32_t *)PyArray_DATA(sp[0]), (const int32_t *)PyArray_DATA(sp[1]),
                (const int32_t *)PyArray_DATA(sp[2]), (const int32_t *)PyArray_DATA(sp[3]),
                (const int32_t *)PyArray_DATA(sp[4]), (const int32_t *)PyArray_DATA(sp[5]),
                (const int32_t *)PyArray_DATA(sp[6]), (const int32_t *)PyArray_DATA(sp[7]),
                (const int32_t *)PyArray_DATA(sf[0]), (const int32_t *)PyArray_DATA(sf[1]),
                (const int32_t *)PyArray_DATA(sf[2]), sf_root,
                (const int32_t *)PyArray_DATA(snf[0]), (const int32_t *)PyArray_DATA(snf[1]),
                (const int32_t *)PyArray_DATA(snf[2]), snf_root,
                er);
        }
        Py_END_ALLOW_THREADS
    }
    for (int i = 0; i < 8; i++) {
        Py_XDECREF(sp[i]); Py_XDECREF(sp_seq[i]);
    }
    for (int i = 0; i < 4; i++) {
        Py_XDECREF(sf[i]); Py_XDECREF(snf[i]);
        Py_XDECREF(sf_seq[i]); Py_XDECREF(snf_seq[i]);
    }
    Py_DECREF(rows);
    Py_DECREF(lens);
    PyBuffer_Release(&buf);
    if (bad || rc != 0) {
        free(er);
        PyErr_SetString(PyExc_ValueError, "unsupported or malformed AC-4 element");
        return NULL;
    }
    PyObject *chan = PyDict_New();
    static const char *names[AC4_MAX_CHANNELS] = {
        "lfe", "L", "R", "Ls", "Rs", "C"};
    for (int c = 0; c < AC4_MAX_CHANNELS; c++) {
        if (!er->present[c]) {
            continue;
        }
        PyObject *d = ac4_sf_to_dict(&er->sf[c], er->sf[c].n_lines);
        PyDict_SetItemString(chan, names[c], d);
        Py_DECREF(d);
    }
    PyObject *aspx = Py_None;
    Py_INCREF(Py_None);
    if (er->has_aspx) {
        aspx = Py_BuildValue(
            "{s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i}",
            "quant_mode_env", er->aspx_quant_mode_env,
            "start_freq", er->aspx_start_freq,
            "stop_freq", er->aspx_stop_freq,
            "master_freq_scale", er->aspx_master_freq_scale,
            "interpolation", er->aspx_interpolation,
            "preflat", er->aspx_preflat,
            "limiter", er->aspx_limiter,
            "noise_sbg", er->aspx_noise_sbg,
            "num_env_bits_fixfix", er->aspx_num_env_bits_fixfix,
            "freq_res_mode", er->aspx_freq_res_mode);
    }
    long bitpos = er->bitpos;
    int codec_mode = er->codec_mode;
    int coding_config = er->coding_config;
    int is_pair_r = er->is_pair;
    int stereo_sap = er->stereo_sap;
    int stereo_sap_sr = er->stereo_sap_sr;
    free(er);
    return Py_BuildValue(
        "{s:i,s:i,s:i,s:i,s:i,s:O,s:O,s:l}",
        "codec_mode", codec_mode, "coding_config", coding_config,
        "is_pair", is_pair_r,
        "stereo_sap", stereo_sap, "stereo_sap_sr", stereo_sap_sr,
        "channels", chan, "aspx", aspx, "bitpos", bitpos);
}

static PyObject *ac4_huff_decode(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    PyObject *lens_obj, *words_obj;
    int bit_offset = 0;
    if (!PyArg_ParseTuple(args, "y*OO|i", &buf, &lens_obj, &words_obj,
                          &bit_offset)) {
        return NULL;
    }
    PyArrayObject *lens = (PyArrayObject *)PyArray_FROM_OTF(
        lens_obj, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    PyArrayObject *words = (PyArrayObject *)PyArray_FROM_OTF(
        words_obj, NPY_INT32, NPY_ARRAY_IN_ARRAY);
    if (lens == NULL || words == NULL) {
        Py_XDECREF(lens);
        Py_XDECREF(words);
        PyBuffer_Release(&buf);
        return NULL;
    }
    int n = (int)PyArray_SIZE(lens);
    if ((int)PyArray_SIZE(words) != n) {
        PyErr_SetString(PyExc_ValueError, "lens/words length mismatch");
        Py_DECREF(lens);
        Py_DECREF(words);
        PyBuffer_Release(&buf);
        return NULL;
    }
    bitreader r;
    br_init(&r, (const uint8_t *)buf.buf, (size_t)buf.len);
    r.pos = (size_t)bit_offset;
    int symbol = -1;
    Py_ssize_t consumed = 0;
    Py_BEGIN_ALLOW_THREADS
    symbol = huff_decode(&r, (const int32_t *)PyArray_DATA(lens),
                         (const int32_t *)PyArray_DATA(words), n);
    consumed = (Py_ssize_t)(r.pos - (size_t)bit_offset);
    Py_END_ALLOW_THREADS
    Py_DECREF(lens);
    Py_DECREF(words);
    PyBuffer_Release(&buf);
    if (symbol < 0) {
        PyErr_SetString(PyExc_ValueError,
                        "no codeword matched within max length");
        return NULL;
    }
    return Py_BuildValue("(in)", symbol, consumed);
}



static PyObject *ac4_parse_toc(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    if (!PyArg_ParseTuple(args, "y*", &buf)) {
        return NULL;
    }
    ac4_toc toc;
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_parse_toc_impl((const uint8_t *)buf.buf, (size_t)buf.len, &toc);
    Py_END_ALLOW_THREADS
    PyBuffer_Release(&buf);

    if (rc == 1) {
        PyErr_SetString(PyExc_ValueError,
                        "bitstream_version <= 1 is not handled by this kernel");
        return NULL;
    }
    if (rc != 0) {
        PyErr_SetString(PyExc_ValueError, "malformed AC-4 TOC");
        return NULL;
    }
    npy_intp dim = toc.n_substreams;
    PyObject *sizes = PyArray_SimpleNew(1, &dim, NPY_INT32);
    if (sizes == NULL) {
        return NULL;
    }
    int32_t *sp = (int32_t *)PyArray_DATA((PyArrayObject *)sizes);
    for (int i = 0; i < toc.n_substreams; i++) {
        sp[i] = toc.substream_sizes[i];
    }
    npy_intp cdim = toc.n_channel_modes;
    PyObject *cmodes = PyArray_SimpleNew(1, &cdim, NPY_INT32);
    if (cmodes == NULL) {
        Py_DECREF(sizes);
        return NULL;
    }
    int32_t *cp = (int32_t *)PyArray_DATA((PyArrayObject *)cmodes);
    for (int i = 0; i < toc.n_channel_modes; i++) {
        cp[i] = toc.channel_modes[i];
    }
    return Py_BuildValue(
        "{s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:i,s:N,s:N}",
        "bitstream_version", toc.bitstream_version,
        "sequence_counter", toc.sequence_counter,
        "fs_index", toc.fs_index,
        "frame_rate_index", toc.frame_rate_index,
        "b_iframe_global", toc.b_iframe_global,
        "n_presentations", toc.n_presentations,
        "payload_base", toc.payload_base,
        "total_n_substream_groups", toc.total_n_substream_groups,
        "toc_bytes", toc.toc_bytes,
        "n_substreams", toc.n_substreams,
        "substream_sizes", sizes,
        "channel_modes", cmodes);
}

static PyObject *ac4_substream(PyObject *self, PyObject *args)
{
    Py_buffer buf;
    int index;
    if (!PyArg_ParseTuple(args, "y*i", &buf, &index)) {
        return NULL;
    }
    size_t offset = 0;
    size_t size = 0;
    int rc;
    Py_BEGIN_ALLOW_THREADS
    rc = ac4_substream_span((const uint8_t *)buf.buf, (size_t)buf.len, index,
                            &offset, &size);
    Py_END_ALLOW_THREADS
    if (rc != 0) {
        PyBuffer_Release(&buf);
        PyErr_SetString(PyExc_ValueError, "malformed AC-4 frame");
        return NULL;
    }
    PyObject *out = PyBytes_FromStringAndSize(
        (const char *)buf.buf + offset, (Py_ssize_t)size);
    PyBuffer_Release(&buf);
    return out;
}

static PyMethodDef ac4_methods[] = {
    {"parse_toc", ac4_parse_toc, METH_VARARGS,
     "Parse ac4_toc() from a raw AC-4 frame; return a dict of TOC fields."},
    {"substream", ac4_substream, METH_VARARGS,
     "Return substream ``index``'s bytes from a raw AC-4 frame."},
    {"huff_decode", ac4_huff_decode, METH_VARARGS,
     "Decode one Huffman codeword; return (symbol, bits_consumed)."},
    {"spectral", ac4_spectral, METH_VARARGS,
     "Decode one window group's ASF spectral data; return (lines, bitpos)."},
    {"framing", ac4_framing_parse, METH_VARARGS,
     "Parse sf_info()/sf_info_lfe(); return (packed int32 array, bitpos)."},
    {"sf", ac4_sf, METH_VARARGS,
     "Decode one channel's ASF sf_data() (sections, spectral, scale factors, "
     "noise fill) from a parsed framing; return a dict."},
    {"element", ac4_element, METH_VARARGS,
     "Decode a 5.X channel element's ASF core; return a dict of channels."},
    {"dequant", ac4_dequant, METH_VARARGS,
     "Reconstruct and scale quantized lines (clause 5.1.3.2); return float32."},
    {"kbd", ac4_kbd, METH_VARARGS,
     "Kaiser-Bessel derived window (clause 5.5.3); return 2N float64."},
    {"ungroup", ac4_ungroup, METH_VARARGS,
     "Pseudocode 25 ungrouping; return (nwin, n_full) float32 spectra."},
    {"synthesise", ac4_synthesise, METH_VARARGS,
     "Filterbank steps 5-6 (Pseudocode 63-64); return (pcm, n_prev)."},
    {"imdct", ac4_imdct, METH_VARARGS,
     "Inverse MDCT of N coefficients with a 2N window (5.5.2); return 2N."},
    {"qmf_analyse", ac4_qmf_analyse, METH_VARARGS,
     "64-band complex QMF analysis (Pseudocode 65); update the filter state."},
    {"qmf_synthesise", ac4_qmf_synthesise, METH_VARARGS,
     "64-band complex QMF synthesis (Pseudocode 66); update the filter state."},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef ac4_module = {
    PyModuleDef_HEAD_INIT,
    "_ac4",
    "AC-4 (ETSI TS 103 190) decoder kernel.  Foundation: ac4_toc().",
    -1,
    ac4_methods
};

PyMODINIT_FUNC PyInit__ac4(void)
{
    import_array();
    return PyModule_Create(&ac4_module);
}
