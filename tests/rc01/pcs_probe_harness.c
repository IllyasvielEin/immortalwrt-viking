/* Reset acquisition model for the actual extracted PCS probe. */
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define EPROBE_DEFER 517
#define ENOTSUPP 524
#define GFP_KERNEL 0
#define ERR_PTR(err) ((void *)(intptr_t)(err))
#define PTR_ERR(ptr) ((int)(intptr_t)(ptr))
#define IS_ERR(ptr) ((uintptr_t)(ptr) >= (uintptr_t)-4095)
#define IS_ERR_OR_NULL(ptr) (!(ptr) || IS_ERR(ptr))
#define CHECK(expr) do { if (!(expr)) { \
    fprintf(stderr, "behavior assertion: %s\n", #expr); exit(1); \
} } while (0)

struct device { int unused; };
struct platform_device { struct device dev; void *data; };
struct clk { int unused; };
struct reset_control { int unused; };
struct ipq_pcs {
    struct device *dev;
    void *base, *regmap;
    struct reset_control *xpcs_rstc, *sys_rstc, *soft_rstc;
};
static struct ipq_pcs controller;
static struct reset_control resets[3];
static struct clk clock_handle;
static int ipq_pcs_regmap_cfg;
static int xpcs_error, optional_error, optional_slot;
static int triple, registered, miis, acquisitions;

static void *devm_kzalloc(struct device *dev, size_t size, int flags)
{ CHECK(size == sizeof(controller)); return &controller; }
static void *devm_platform_ioremap_resource(struct platform_device *pdev, int index)
{ CHECK(index == 0); return &controller; }
static void *devm_regmap_init(struct device *dev, void *bus, void *data, const void *cfg)
{ return &controller; }
static struct clk *devm_clk_get_enabled(struct device *dev, const char *id)
{ return &clock_handle; }
static int dev_err_probe(struct device *dev, int error, const char *message)
{ return error; }

/* NULL id selects reset index zero, even when that reset is named. A named
 * request cannot find an unnamed legacy descriptor. Optional missing resets
 * return NULL; mandatory missing resets and disabled framework return errors. */
static struct reset_control *get_reset(const char *id, int optional)
{
    int slot = !id || !strcmp(id, "xpcs") ? 0 : !strcmp(id, "sys") ? 1 : 2;
    CHECK(acquisitions++ == slot);
    if (!slot) {
        if (xpcs_error)
            return optional && (xpcs_error == -ENOENT || xpcs_error == -ENOTSUPP)
                ? NULL : ERR_PTR(xpcs_error);
        if (id && !triple) return optional ? NULL : ERR_PTR(-ENOENT);
    } else {
        if (slot == optional_slot) return ERR_PTR(optional_error);
        if (!triple) return optional ? NULL : ERR_PTR(-ENOENT);
    }
    return &resets[slot];
}
static struct reset_control *devm_reset_control_get_exclusive(struct device *dev, const char *id)
{ return get_reset(id, 0); }
static struct reset_control *devm_reset_control_get_optional(struct device *dev, const char *id)
{ return get_reset(id, 1); }
static struct reset_control *devm_reset_control_get_optional_exclusive(struct device *dev, const char *id)
{ return get_reset(id, 1); }
static int ipq_pcs_clk_register(struct ipq_pcs *pcs)
{
    CHECK(pcs->xpcs_rstc == &resets[0]);
    CHECK(pcs->sys_rstc == (triple ? &resets[1] : NULL));
    CHECK(pcs->soft_rstc == (triple ? &resets[2] : NULL));
    CHECK(acquisitions == 3);
    registered++;
    return 0;
}
static int ipq_pcs_create_miis(struct ipq_pcs *pcs)
{ CHECK(registered == 1); miis++; return 0; }
static void platform_set_drvdata(struct platform_device *pdev, void *data)
{ CHECK(miis == 1); pdev->data = data; }

#include "probe.inc"

int main(int argc, char **argv)
{
    struct platform_device pdev = {0};
    int expected = 0;
    CHECK(argc == 2);
    if (!strcmp(argv[1], "missing")) expected = xpcs_error = -ENOENT;
    else if (!strcmp(argv[1], "disabled")) expected = xpcs_error = -ENOTSUPP;
    else if (!strcmp(argv[1], "defer")) expected = xpcs_error = -EPROBE_DEFER;
    else if (!strcmp(argv[1], "io")) expected = xpcs_error = -EIO;
    else if (!strcmp(argv[1], "legacy")) { }
    else if (!strcmp(argv[1], "triple")) triple = 1;
    else if (!strcmp(argv[1], "sys-error")) {
        optional_slot = 1; expected = optional_error = -EPROBE_DEFER;
    } else if (!strcmp(argv[1], "soft-error")) {
        optional_slot = 2; expected = optional_error = -EIO;
    } else return 2;
    CHECK(ipq9574_pcs_probe(&pdev) == expected);
    CHECK(registered == !expected);
    CHECK(miis == !expected);
    CHECK(pdev.data == (expected ? NULL : &controller));
    return 0;
}
