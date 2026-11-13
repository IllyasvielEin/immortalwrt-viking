/* Execute the actual PCS functions against a cached-clock/fault model. */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <sys/resource.h>

#define __iomem
#define BIT(n) (1UL << (n))
#define GENMASK(h, l) ((~0UL << (l)) & (~0UL >> (63 - (h))))
#define FIELD_PREP(mask, val) (((unsigned long)(val) << __builtin_ctzl(mask)) & (mask))
#define FIELD_GET(mask, val) (((val) & (mask)) >> __builtin_ctzl(mask))
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x, value) ((x) = (value))
#define container_of(p, type, member) ((type *)((char *)(p) - offsetof(type, member)))
#define ERR_PTR(e) ((void *)(intptr_t)(e))
#define GFP_KERNEL 0
#define PCS_RX_CLK 0
#define PCS_TX_CLK 1
#define PHYLINK_PCS_NEG_INBAND_ENABLED 1
#define dev_err(dev, ...) ((void)(dev), discard_log(__VA_ARGS__))
#define dev_warn(dev, ...) ((void)(dev), discard_log(__VA_ARGS__))
#define msleep(...) ((void)0)
#define fsleep(...) ((void)0)

typedef enum {
    PHY_INTERFACE_MODE_NA, PHY_INTERFACE_MODE_SGMII,
    PHY_INTERFACE_MODE_QSGMII, PHY_INTERFACE_MODE_PSGMII,
    PHY_INTERFACE_MODE_1000BASEX, PHY_INTERFACE_MODE_2500BASEX,
    PHY_INTERFACE_MODE_USXGMII, PHY_INTERFACE_MODE_10G_QXGMII,
    PHY_INTERFACE_MODE_10GBASER,
} phy_interface_t;
struct device { void *of_node; };
struct phylink_pcs { int unused; };
struct reset_control { bool asserted; };
struct regmap { unsigned int regs[0x200000]; };
struct clk_hw;
struct clk_rate_request { unsigned long rate; };
struct clk_ops {
    int (*determine_rate)(struct clk_hw *, struct clk_rate_request *);
    unsigned long (*recalc_rate)(struct clk_hw *, unsigned long);
};
struct clk_init_data { const struct clk_ops *ops; const char *name; };
struct clk {
    unsigned long cached_rate;
    int references;
    struct clk_hw *hw;
    const struct clk_ops *ops;
};
struct clk_hw { struct clk *clk; const struct clk_init_data *init; };
struct of_phandle_args { unsigned int args[1]; };

#include "prepared_types.inc"

static struct regmap registers;
static struct device device;
static struct reset_control reset;
static struct clk roots[2], ports[PCS_MAX_MII_NRS][2];
static struct ipq_pcs controller;
static struct ipq_pcs_mii channels[PCS_MAX_MII_NRS];
static int operations, fail_at, roots_registered, mode_writes;
static bool fail_calibration;
static void discard_log(const char *format, ...) { (void)format; }

static int operation(void)
{
    return ++operations == fail_at ? -EIO : 0;
}
static int regmap_update_bits(struct regmap *map, unsigned int reg,
                             unsigned int mask, unsigned int value)
{
    if (operation()) return -EIO;
    assert(reg < 0x200000);
    map->regs[reg] = (map->regs[reg] & ~mask) | (value & mask);
    if (reg == PCS_MODE_CTRL) mode_writes++;
    return 0;
}
static int regmap_set_bits(struct regmap *map, unsigned int reg, unsigned int bits)
{ return regmap_update_bits(map, reg, bits, bits); }
static int regmap_clear_bits(struct regmap *map, unsigned int reg, unsigned int bits)
{ return regmap_update_bits(map, reg, bits, 0); }
static int regmap_write(struct regmap *map, unsigned int reg, unsigned int value)
{ return regmap_update_bits(map, reg, ~0U, value); }
#define regmap_read_poll_timeout(map, reg, value, condition, delay, limit) \
    ((value) = (reg) == PCS_CALIBRATION ? PCS_CALIBRATION_DONE : XPCS_KR_LINK_STS, \
     ((reg) == PCS_CALIBRATION && fail_calibration) ? -ETIMEDOUT : 0)
static int reset_control_assert(struct reset_control *r)
{ if (!r) return 0; if (operation()) return -EIO; r->asserted = true; return 0; }
static int reset_control_deassert(struct reset_control *r)
{ if (!r) return 0; if (operation()) return -EIO; r->asserted = false; return 0; }
static bool of_property_read_bool(void *node, const char *name)
{ (void)node; (void)name; return true; }
static int clk_prepare_enable(struct clk *clk)
{ if (operation()) return -EIO; clk->references++; return 0; }
static void clk_disable_unprepare(struct clk *clk)
{ assert(clk->references > 0); clk->references--; }

/* CCF retains provider recalc_rate() results and can return early when the
 * requested rate is already cached. It does not read PCS interface state
 * again merely because the driver later assigns that field. */
static int clk_set_rate(struct clk *clk, unsigned long rate)
{
    struct clk_rate_request req = { .rate = rate };
    if (clk->cached_rate == rate) return 0;
    if (operation()) return -EIO;
    if (clk->ops) {
        int ret = clk->ops->determine_rate(clk->hw, &req);
        if (ret) return ret;
        clk->cached_rate = clk->ops->recalc_rate(clk->hw, 0);
    } else {
        clk->cached_rate = rate;
    }
    return 0;
}
static const char *dev_name(struct device *dev) { (void)dev; return "test-pcs"; }
static char *devm_kasprintf(struct device *dev, int flags, const char *fmt, ...)
{ (void)dev; (void)flags; (void)fmt; return "test-clock"; }
static int devm_clk_hw_register(struct device *dev, struct clk_hw *hw)
{
    (void)dev;
    assert(roots_registered < 2);
    struct clk *clk = &roots[roots_registered++];
    clk->hw = hw;
    clk->ops = hw->init->ops;
    hw->clk = clk;
    clk->cached_rate = clk->ops->recalc_rate(hw, 0);
    return 0;
}
static int devm_of_clk_add_hw_provider(struct device *dev,
    struct clk_hw *(*get)(struct of_phandle_args *, void *), void *data)
{ (void)dev; (void)get; (void)data; return 0; }

#include "prepared_functions.inc"

static void setup(void)
{
    memset(&registers, 0, sizeof(registers));
    memset(&controller, 0, sizeof(controller));
    memset(roots, 0, sizeof(roots));
    memset(ports, 0, sizeof(ports));
    memset(channels, 0, sizeof(channels));
    operations = fail_at = roots_registered = mode_writes = 0;
    fail_calibration = false;
    controller.dev = &device;
    controller.regmap = &registers;
    controller.xpcs_rstc = controller.sys_rstc = controller.soft_rstc = &reset;
    assert(!ipq_pcs_clk_register(&controller));
    for (int i = 0; i < PCS_MAX_MII_NRS; i++) {
        channels[i].qpcs = &controller;
        channels[i].index = i;
        channels[i].rx_clk = &ports[i][0];
        channels[i].tx_clk = &ports[i][1];
        controller.qpcs_mii[i] = &channels[i];
        assert(!ipq_pcs_enable(&channels[i].pcs));
    }
    operations = 0;
}
static void rates(unsigned long expected)
{
    assert(roots[0].cached_rate == expected);
    assert(roots[1].cached_rate == expected);
    /* Consumers must be able to derive the 125 MHz 1G target from the
     * cached parent: 312.5 MHz / 2.5, rather than the erroneous / 1. */
    if (expected == 312500000)
        assert(roots[0].cached_rate * 2 / 5 == 125000000);
}
static void references_balanced(void)
{
    for (int i = 0; i < PCS_MAX_MII_NRS; i++) {
        assert(ports[i][0].references == ports[i][1].references);
        assert(ports[i][0].references >= 0 && ports[i][0].references <= 1);
    }
}
static void cold_clock(void)
{
    setup(); rates(125000000);
    assert(!ipq_pcs_config_usxgmii(&controller, 0, PHY_INTERFACE_MODE_10G_QXGMII));
    rates(312500000);
    assert(controller.interface == PHY_INTERFACE_MODE_10G_QXGMII);
    int writes = mode_writes;
    registers.regs[XPCS_MII_CTRL] = XPCS_SPEED_1000;
    assert(!ipq_pcs_config_usxgmii(&controller, 0, PHY_INTERFACE_MODE_10G_QXGMII));
    assert(mode_writes == writes);
    assert((registers.regs[XPCS_MII_CTRL] & XPCS_SPEED_MASK) == XPCS_SPEED_1000);
}
static void transitions(void)
{
    static const phy_interface_t modes[] = {
        PHY_INTERFACE_MODE_10G_QXGMII, PHY_INTERFACE_MODE_SGMII,
        PHY_INTERFACE_MODE_2500BASEX, PHY_INTERFACE_MODE_1000BASEX,
        PHY_INTERFACE_MODE_USXGMII, PHY_INTERFACE_MODE_10GBASER,
    };
    setup();
    for (int repeat = 0; repeat < 4; repeat++)
        for (size_t i = 0; i < sizeof(modes) / sizeof(modes[0]); i++) {
            assert(!ipq_pcs_config_mode(&controller, modes[i]));
            rates(modes[i] == PHY_INTERFACE_MODE_SGMII ||
                  modes[i] == PHY_INTERFACE_MODE_1000BASEX ? 125000000 : 312500000);
            references_balanced();
        }
}
static void failed_mode_retry(void)
{
    setup();
    assert(!ipq_pcs_config_2500basex(&controller));
    fail_calibration = true;
    assert(ipq_pcs_config_mode(&controller, PHY_INTERFACE_MODE_SGMII) == -ETIMEDOUT);
    assert(controller.interface == PHY_INTERFACE_MODE_NA);
    int writes = mode_writes;
    fail_calibration = false;
    assert(!ipq_pcs_config_2500basex(&controller));
    assert(mode_writes == writes + 1);
    rates(312500000);
}
static void failure_sweep(bool usx)
{
    phy_interface_t mode = usx ? PHY_INTERFACE_MODE_USXGMII : PHY_INTERFACE_MODE_10G_QXGMII;
    setup();
    assert(!ipq_pcs_config_usxgmii(&controller, 0, mode));
    int count = operations;
    for (int fail = 1; fail <= count; fail++) {
        setup(); fail_at = fail;
        assert(ipq_pcs_config_usxgmii(&controller, 0, mode) == -EIO);
        references_balanced();
        assert(!channels[0].usxgmii_initialized);
        fail_at = 0;
        assert(!ipq_pcs_config_usxgmii(&controller, 0, mode));
        rates(312500000);
        assert(channels[0].usxgmii_initialized);
        assert(registers.regs[XPCS_DIG_CTRL] & XPCS_USXG_EN);
        if (!usx)
            assert((registers.regs[XPCS_KR_CTRL] & XPCS_USXG_MODE_MASK) == XPCS_10G_QXGMII_MODE);
        references_balanced();
    }
    printf("fault sites: %d\n", count);
}
static void enable_failure(void)
{
    setup();
    ipq_pcs_disable(&channels[0].pcs);
    operations = 0; fail_at = 2;
    assert(ipq_pcs_enable(&channels[0].pcs) == -EIO);
    references_balanced();
    assert(!ports[0][0].references);
    /* phylink can still call disable after an enable failure. */
    ipq_pcs_disable(&channels[0].pcs);
    fail_at = 0;
    assert(!ipq_pcs_enable(&channels[0].pcs));
    assert(!ipq_pcs_enable(&channels[0].pcs));
    references_balanced();
}
int main(int argc, char **argv)
{
    struct rlimit no_core = { 0, 0 };
    assert(!setrlimit(RLIMIT_CORE, &no_core));
    assert(argc == 2);
    if (!strcmp(argv[1], "cold")) cold_clock();
    else if (!strcmp(argv[1], "transitions")) transitions();
    else if (!strcmp(argv[1], "retry")) failed_mode_retry();
    else if (!strcmp(argv[1], "qx-faults")) failure_sweep(false);
    else if (!strcmp(argv[1], "usx-faults")) failure_sweep(true);
    else if (!strcmp(argv[1], "enable-failure")) enable_failure();
    else return 2;
    return 0;
}
