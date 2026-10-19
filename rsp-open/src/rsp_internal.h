#ifndef RSP_INTERNAL_H
#define RSP_INTERNAL_H

#include <stdint.h>

#include "rsp.h"

#define RSP_VID 0x1df7
#define RSP_PID_RSP1B 0x3050

#define RSP_CMD_WREG 0x41
#define RSP_CMD_START 0x43
#define RSP_CMD_STOP 0x45

#define RSP_EP_IN 0x81
#define RSP_ISO_FRAMES 8
#define RSP_ISO_FRAME_BYTES (3 * 1024)
#define RSP_NURB 8
#define RSP_CONTROL_TIMEOUT_MS 2000

#define RSP_RING_SAMPLES (1u << 22)
#define RSP_PRIME_SECONDS 0.5
#define RSP_READ_CHUNK 262144

#define MSI2500_REG7_CS8 0x000c9407u
#define MSI001_REG6_RSP1B 0x003ffff6u

struct rsp1b_ctrl {
    uint8_t req;
    uint16_t value;
    uint16_t index;
};

extern const struct rsp1b_ctrl rsp1b_init_vendor[];
extern const size_t rsp1b_init_vendor_len;
extern const struct rsp1b_ctrl rsp1b_close_vendor[];
extern const size_t rsp1b_close_vendor_len;

void rsp1b_gain_word(int ifgr, uint16_t *value, uint16_t *index);

int rsp_ctrl(rsp_dev *dev, uint8_t req, uint32_t data);

void msi2500_adc_words(double fs, uint32_t *reg3, uint32_t *reg4,
                       uint32_t *reg7);
int msi2500_set_adc(rsp_dev *dev, double fs);

void msi001_tuner_words(double rf, double bandwidth, int lna, int mixer,
                        int if_gain, uint32_t words[7]);
int msi001_set_tuner(rsp_dev *dev, double rf, double bandwidth, int lna,
                     int mixer, int if_gain);

#endif
