#include "rsp_internal.h"

#define MSI2500_F_REF 24000000u
#define MSI2500_DIV_PRE_N 2u
#define MSI2500_DIV_LO_OUT 12u
#define MSI2500_F_VCO_MIN 202000000u

static int wreg(rsp_dev *dev, uint32_t data)
{
    return rsp_ctrl(dev, RSP_CMD_WREG, data) < 0 ? -1 : 0;
}

void msi2500_adc_words(double fs, uint32_t *reg3, uint32_t *reg4,
                       uint32_t *reg7)
{
    uint64_t f_sr = (uint64_t)(fs + 0.5);
    uint64_t f_vco = 0;
    uint32_t div_n, k, k_cw, div_out;

    *reg7 = MSI2500_REG7_CS8;
    *reg3 = 0x01000303u;
    *reg4 = 0x00000004u;

    if (f_sr < 6000000u)
        *reg3 |= 0x1u << 20;
    else if (f_sr < 7000000u)
        *reg3 |= 0x5u << 20;
    else if (f_sr < 8500000u)
        *reg3 |= 0x9u << 20;
    else
        *reg3 |= 0xdu << 20;

    for (div_out = 4; div_out < 16; div_out += 2) {
        f_vco = f_sr * div_out * MSI2500_DIV_LO_OUT;
        if (f_vco >= MSI2500_F_VCO_MIN)
            break;
    }

    div_n = (uint32_t)(f_vco / (MSI2500_DIV_PRE_N * MSI2500_F_REF));
    k = (uint32_t)(f_vco % (MSI2500_DIV_PRE_N * MSI2500_F_REF));
    k_cw = (uint32_t)(((uint64_t)k * 0x200000u) /
                      (MSI2500_DIV_PRE_N * MSI2500_F_REF));

    *reg3 |= div_n << 16;
    *reg3 |= (div_out / 2 - 1) << 10;
    *reg3 |= ((k_cw >> 20) & 1u) << 15;
    *reg4 |= (k_cw & 0xfffffu) << 8;
}

int msi2500_set_adc(rsp_dev *dev, double fs)
{
    uint32_t reg3, reg4, reg7;

    msi2500_adc_words(fs, &reg3, &reg4, &reg7);

    if (wreg(dev, 0x00ee8008u) < 0) return -1;
    if (wreg(dev, 0x00000c05u) < 0) return -1;
    if (wreg(dev, 0x00020000u) < 0) return -1;
    if (wreg(dev, 0x00480102u) < 0) return -1;
    if (wreg(dev, 0x00f38008u) < 0) return -1;
    if (wreg(dev, reg7) < 0) return -1;
    if (wreg(dev, reg4) < 0) return -1;
    if (wreg(dev, reg3) < 0) return -1;
    return 0;
}
