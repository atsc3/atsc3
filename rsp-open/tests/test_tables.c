#include <stdint.h>
#include <stdio.h>

#include "rsp_internal.h"

static int failures;

static void check32(const char *what, uint32_t got, uint32_t want)
{
    if (got != want) {
        fprintf(stderr, "FAIL %s: got 0x%08x want 0x%08x\n", what, got, want);
        failures++;
    }
}

int main(void)
{
    uint32_t reg3, reg4, reg7;
    uint32_t words[7];
    static const uint32_t want_words[7] = {
        0x00000eu, 0x000003u, 0x0005f480u, 0x00280185u,
        0x001800b2u, 0x00012281u, 0x003ffff6u,
    };

    msi2500_adc_words(10e6, &reg3, &reg4, &reg7);
    check32("adc reg3", reg3, 0x01da0703u);
    check32("adc reg4", reg4, 0x00000004u);
    check32("adc reg7", reg7, 0x000c9407u);

    msi001_tuner_words(587e6, 8e6, 0, 1, 19, words);
    for (int i = 0; i < 7; i++) {
        char label[32];
        snprintf(label, sizeof(label), "tuner word %d", i);
        check32(label, words[i], want_words[i]);
    }

    check32("vendor init length", (uint32_t)rsp1b_init_vendor_len, 22);
    check32("vendor init[0] value", rsp1b_init_vendor[0].value, 0x8008);
    check32("vendor init[0] index", rsp1b_init_vendor[0].index, 0x00ee);
    check32("vendor init[5] value", rsp1b_init_vendor[5].value, 0x8509);
    check32("vendor init[5] index", rsp1b_init_vendor[5].index, 0x28bb);
    check32("vendor init[7] value", rsp1b_init_vendor[7].value, 0xf609);
    check32("vendor init[7] index", rsp1b_init_vendor[7].index, 0x2003);
    check32("vendor init[9] req", rsp1b_init_vendor[9].req, 0x49);
    check32("vendor init[9] value", rsp1b_init_vendor[9].value, 0x12df);
    check32("vendor init[17] value", rsp1b_init_vendor[17].value, 0x8109);
    check32("vendor init[17] index", rsp1b_init_vendor[17].index, 0x0102);
    check32("vendor init[21] value", rsp1b_init_vendor[21].value, 0x0703);
    check32("vendor init[21] index", rsp1b_init_vendor[21].index, 0x01da);

    check32("vendor close length", (uint32_t)rsp1b_close_vendor_len, 10);
    check32("vendor close[0] req", rsp1b_close_vendor[0].req, 0x45);
    check32("vendor close[8] value", rsp1b_close_vendor[8].value, 0x0003);
    check32("vendor close[8] index", rsp1b_close_vendor[8].index, 0x0913);

    uint16_t v, i;
    rsp1b_gain_word(40, &v, &i);
    check32("gain 40 value", v, 0x8109);
    check32("gain 40 index", i, 0x0102);
    rsp1b_gain_word(44, &v, &i);
    check32("gain 44 value", v, 0xc109);
    check32("gain 44 index", i, 0x0102);
    rsp1b_gain_word(45, &v, &i);
    check32("gain 45 value", v, 0xd109);
    check32("gain 45 index", i, 0x0102);
    rsp1b_gain_word(48, &v, &i);
    check32("gain 48 value", v, 0x0109);
    check32("gain 48 index", i, 0x0103);

    if (failures) {
        fprintf(stderr, "%d table check(s) failed\n", failures);
        return 1;
    }
    printf("table checks passed (RSP1B 587 MHz / 10 MS/s / 8 MHz)\n");
    return 0;
}
