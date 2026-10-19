#include "rsp_internal.h"

#define MSI001_F_REF 24000000u
#define MSI001_DIV_PRE_N 4u

struct msi001_band {
    uint32_t rf;
    uint8_t mode;
    uint8_t div_lo;
};

static const struct msi001_band msi001_bands[] = {
    {50000000u, 0xe1, 16},   /* AM_MODE2 */
    {108000000u, 0x42, 32},  /* VHF_MODE */
    {330000000u, 0x44, 16},  /* B3_MODE */
    {960000000u, 0x48, 4},   /* B45_MODE */
    {0xffffffffu, 0x50, 2},  /* BL_MODE */
};

struct msi001_bandwidth {
    uint32_t hz;
    uint8_t code;
};

static const struct msi001_bandwidth msi001_bandwidths[] = {
    {200000u, 0x00},
    {300000u, 0x01},
    {600000u, 0x02},
    {1536000u, 0x03},
    {5000000u, 0x04},
    {6000000u, 0x05},
    {7000000u, 0x06},
    {8000000u, 0x07},
};

static uint32_t gcd_u32(uint32_t a, uint32_t b)
{
    while (b) {
        uint32_t t = a % b;
        a = b;
        b = t;
    }
    return a;
}

void msi001_tuner_words(double rf, double bandwidth, int lna, int mixer,
                        int if_gain, uint32_t words[7])
{
    uint64_t f_rf = (uint64_t)(rf + 0.5);
    uint64_t f_vco, f_vco_step;
    uint32_t div_lo = 2, mode = 0x50;
    uint32_t div_n, k, k_thresh, k_frac, uitmp;
    uint8_t filter_mode = 0x03; /* zero IF */
    uint8_t bw_code = 0x07;
    int32_t gr;

    for (size_t i = 0; i < sizeof(msi001_bands) / sizeof(msi001_bands[0]); i++) {
        if (f_rf <= msi001_bands[i].rf) {
            mode = msi001_bands[i].mode;
            div_lo = msi001_bands[i].div_lo;
            break;
        }
    }

    for (size_t i = 0; i < sizeof(msi001_bandwidths) /
                               sizeof(msi001_bandwidths[0]); i++) {
        if (bandwidth <= msi001_bandwidths[i].hz) {
            bw_code = msi001_bandwidths[i].code;
            break;
        }
    }

    f_vco = f_rf * div_lo;
    div_n = (uint32_t)(f_vco / (MSI001_DIV_PRE_N * MSI001_F_REF));
    k = (uint32_t)(f_vco % (MSI001_DIV_PRE_N * MSI001_F_REF));
    f_vco_step = div_lo;
    if (f_vco_step == 0)
        f_vco_step = 1;
    k_thresh = (uint32_t)((MSI001_DIV_PRE_N * MSI001_F_REF) / f_vco_step);
    k_frac = (uint32_t)(((uint64_t)k * k_thresh) /
                        (MSI001_DIV_PRE_N * MSI001_F_REF));

    uitmp = gcd_u32(k_thresh, k_frac);
    if (uitmp) {
        k_thresh /= uitmp;
        k_frac /= uitmp;
    }

    uitmp = (k_thresh + 4094u) / 4095u;
    if (uitmp > 1u) {
        k_thresh = (k_thresh + uitmp / 2u) / uitmp;
        k_frac = (k_frac + uitmp / 2u) / uitmp;
    }

    gr = 59 - if_gain;
    if (gr < 0) gr = 0;
    if (gr > 59) gr = 59;

    words[0] = 0x00000eu;
    words[1] = 0x000003u;
    words[2] = mode << 4;
    words[2] |= (uint32_t)filter_mode << 12;
    words[2] |= (uint32_t)bw_code << 14;
    words[2] |= 0x02u << 17;
    words[3] = 5u;
    words[3] |= k_thresh << 4;
    words[3] |= 1u << 19;
    words[3] |= 1u << 21;
    words[4] = 2u;
    words[4] |= k_frac << 4;
    words[4] |= div_n << 16;
    words[5] = 1u;
    words[5] |= (uint32_t)gr << 4;
    words[5] |= (uint32_t)(1 - mixer) << 12;
    words[5] |= (uint32_t)(1 - lna) << 13;
    words[5] |= 4u << 14;
    words[6] = MSI001_REG6_RSP1B;
}

static int msi001_write(rsp_dev *dev, uint32_t word)
{
    uint32_t data = 0x09u | ((word & 0xffu) << 8) |
                    (((word >> 8) & 0xffu) << 16) |
                    (((word >> 16) & 0xffu) << 24);
    return rsp_ctrl(dev, RSP_CMD_WREG, data) < 0 ? -1 : 0;
}

int msi001_set_tuner(rsp_dev *dev, double rf, double bandwidth, int lna,
                     int mixer, int if_gain)
{
    uint32_t words[7];

    msi001_tuner_words(rf, bandwidth, lna, mixer, if_gain, words);
    for (size_t i = 0; i < 7; i++) {
        if (msi001_write(dev, words[i]) < 0)
            return -1;
    }
    return 0;
}
