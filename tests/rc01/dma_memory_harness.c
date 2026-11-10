/* Host fault injection for verbatim prepared QMI ownership functions. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <errno.h>

typedef uint32_t u32;
typedef uint8_t u8;
typedef uint64_t dma_addr_t;
typedef uint64_t phys_addr_t;
#define __iomem
#define GFP_KERNEL 0
#define __GFP_NOWARN 0
#define U32_MAX UINT32_MAX
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define ATH12K_FLAG_FIXED_MEM_REGION 0
#define ATH12K_BUS_PCI 1
#define ATH12K_BUS_AHB 2
#define fallthrough __attribute__((fallthrough))
#define ath12k_warn(ab, ...) ((void)(ab))
#define ath12k_info(ab, ...) ((void)(ab))
#define ath12k_err(ab, ...) ((void)(ab))
#define WARN_ON_ONCE(x) (!!(x))
#define lockdep_assert_held(p) assert(*(p) == 1)

#include "prepared-types.h"

struct reserved_mem { phys_addr_t base; size_t size; };
struct device_node {
	bool declared[4];
	struct reserved_mem *regions[4];
	u32 host_offset;
};
struct device { struct device_node *of_node; int refs; };
struct ath12k_base;
struct ath12k_hw_group {
	int mutex;
	unsigned int num_devices, num_started;
	struct ath12k_base *ab[4];
	struct ath12k_mlo_memory mlo_mem;
};
struct ath12k_base {
	struct ath12k_hw_group *ag;
	struct device *dev;
	struct ath12k_qmi qmi;
	unsigned long dev_flags;
	struct { int bus; } hif;
};

static void mutex_lock(int *mutex) { assert(!*mutex); *mutex = 1; }
static void mutex_unlock(int *mutex) { assert(*mutex == 1); *mutex = 0; }
static bool test_bit(int bit, unsigned long *flags) { return (*flags & (1UL << bit)) != 0; }
static struct device *get_device(struct device *dev) { assert(dev->refs > 0); dev->refs++; return dev; }
static void put_device(struct device *dev) { if (dev) { assert(dev->refs > 1); dev->refs--; } }

static int region_index(const char *name)
{
	static const char *const names[] = { "host-ddr-mem", "m3-dump", "q6-caldb", "mlo-global-mem" };
	for (int i = 0; i < 4; i++) if (!strcmp(name, names[i])) return i;
	return -1;
}
static int of_property_match_string(struct device_node *node, const char *property, const char *name)
{
	assert(!strcmp(property, "memory-region-names"));
	int i = region_index(name);
	return node && i >= 0 && node->declared[i] ? i : -EINVAL;
}
static int of_property_read_u32(struct device_node *node, const char *property, u32 *out)
{
	assert(!strcmp(property, "qcom,host-ddr-offset"));
	if (!node) return -EINVAL;
	*out = node->host_offset;
	return 0;
}
static struct reserved_mem *ath12k_core_get_rmem_by_name(struct ath12k_base *ab, const char *name)
{
	int i = region_index(name);
	return ab->dev->of_node && i >= 0 && ab->dev->of_node->declared[i] ? ab->dev->of_node->regions[i] : NULL;
}

/* Registry detects a wrong API/device/length, duplicate free or leaked owner. */
static struct allocation { void *ptr; size_t size; struct device *dev; dma_addr_t paddr; int kind; } allocations[256];
static int alloc_count, dma_count, map_count, dma_frees, unmaps;
static int fail_dma_after = -1, fail_map_after = -1;
static void *new_allocation(size_t size, struct device *dev, dma_addr_t paddr, int kind)
{
	assert(size && size < 1024 * 1024);
	void *ptr = malloc(size);
	assert(ptr && alloc_count < 256);
	if (kind == 0) paddr = (uintptr_t)ptr;
	allocations[alloc_count++] = (struct allocation){ ptr, size, dev, paddr, kind };
	return ptr;
}
static void *dma_alloc_coherent(struct device *dev, size_t size, dma_addr_t *paddr, int flags)
{
	(void)flags;
	if (fail_dma_after == 0) return NULL;
	if (fail_dma_after > 0) fail_dma_after--;
	void *ptr = new_allocation(size, dev, 0, 0);
	*paddr = (uintptr_t)ptr;
	dma_count++;
	return ptr;
}
static void dma_free_coherent(struct device *dev, size_t size, void *ptr, dma_addr_t paddr)
{
	for (int i = 0; i < alloc_count; i++) if (allocations[i].ptr == ptr) {
		assert(allocations[i].kind == 0 && allocations[i].dev == dev);
		assert(allocations[i].size == size && allocations[i].paddr == paddr);
		free(ptr); allocations[i].ptr = NULL; dma_frees++; return;
	}
	assert(!"unowned or repeated dma_free_coherent");
}
static void *ioremap(phys_addr_t paddr, size_t size)
{
	if (fail_map_after == 0) return NULL;
	if (fail_map_after > 0) fail_map_after--;
	map_count++;
	return new_allocation(size, NULL, paddr, 1);
}
static void iounmap(void *ptr)
{
	for (int i = 0; i < alloc_count; i++) if (allocations[i].ptr == ptr) {
		assert(allocations[i].kind == 1);
		free(ptr); allocations[i].ptr = NULL; unmaps++; return;
	}
	assert(!"unowned or repeated iounmap");
}
static void memset_io(void *ptr, int value, size_t size) { memset(ptr, value, size); }
static int alive(int kind)
{
	int n = 0;
	for (int i = 0; i < alloc_count; i++) if (allocations[i].ptr && allocations[i].kind == kind) n++;
	return n;
}

#include "prepared-functions.h"

static void setup(struct ath12k_base *ab, struct ath12k_hw_group *ag, struct device *dev, struct device_node *node)
{
	memset(ab, 0, sizeof(*ab));
	memset(node, 0, sizeof(*node));
	*dev = (struct device){ .of_node = node, .refs = 1 };
	ab->dev = dev; ab->ag = ag; ab->hif.bus = ATH12K_BUS_PCI;
	ag->ab[ag->num_devices++] = ab;
}
static void request(struct ath12k_base *ab, unsigned int idx, u32 type, u32 size)
{
	/* The real QMI callback overwrites request fields, not owner metadata. */
	ab->qmi.target_mem[idx].type = type;
	ab->qmi.target_mem[idx].size = size;
}
static void cleanup(struct ath12k_base *ab)
{
	mutex_lock(&ab->ag->mutex);
	ath12k_qmi_free_target_mem_chunk(ab);
	mutex_unlock(&ab->ag->mutex);
}
#define FIXTURE \
	struct ath12k_hw_group ag = {}; \
	struct ath12k_base ab; struct device dev; struct device_node node; \
	setup(&ab, &ag, &dev, &node)

static void coherent(void)
{
	FIXTURE;
	ab.qmi.mem_seg_count = 2;
	request(&ab, 0, HOST_DDR_REGION_TYPE, 64);
	request(&ab, 1, CALDB_MEM_REGION_TYPE, 32);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	void *first = ab.qmi.target_mem[0].v.addr;
	assert(alive(0) == 2 && dma_count == 2);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(first == ab.qmi.target_mem[0].v.addr && dma_count == 2);
	request(&ab, 0, M3_DUMP_REGION_TYPE, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(dma_count == 3 && dma_frees == 1);
	ab.qmi.mem_seg_count = 1;
	request(&ab, 0, M3_DUMP_REGION_TYPE, 16);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(alive(0) == 1 && dma_count == 4 && dma_frees == 3);
	assert(!ab.qmi.target_mem[1].v.addr && !ab.qmi.target_mem[1].paddr);
	request(&ab, 0, 0xf0, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(!alive(0) && !ab.qmi.target_mem[0].paddr);
	cleanup(&ab); cleanup(&ab);
	assert(!alive(0) && dev.refs == 1);
}

static void allocation_failure(void)
{
	FIXTURE;
	ab.qmi.mem_seg_count = 2;
	request(&ab, 0, HOST_DDR_REGION_TYPE, 64);
	request(&ab, 1, M3_DUMP_REGION_TYPE, 64);
	fail_dma_after = 1;
	assert(ath12k_qmi_alloc_target_mem_chunk(&ab) == -ENOMEM);
	assert(!alive(0) && !ag.mlo_mem.init_done);
	fail_dma_after = 0;
	ab.qmi.mem_seg_count = 1;
	request(&ab, 0, HOST_DDR_REGION_TYPE, ATH12K_QMI_MAX_CHUNK_SIZE + 1);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(ab.qmi.target_mem_delayed && !alive(0));
	fail_dma_after = -1;
	request(&ab, 0, HOST_DDR_REGION_TYPE, 32);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(!ab.qmi.target_mem_delayed);
	cleanup(&ab); cleanup(&ab);
	request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64);
	fail_dma_after = 0;
	assert(ath12k_qmi_alloc_target_mem_chunk(&ab) == -ENOMEM);
	assert(!alive(0) && dev.refs == 1 && !ag.mlo_mem.num_users);
	fail_dma_after = -1;
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	cleanup(&ab);
}

static void fixed(void)
{
	FIXTURE;
	struct reserved_mem host = { 0x100000, 4096 }, caldb = { 0x200000, 4096 };
	node.declared[0] = true; node.regions[0] = &host;
	ab.hif.bus = ATH12K_BUS_AHB;
	ab.dev_flags = 1;
	ab.qmi.mem_seg_count = 2;
	request(&ab, 0, HOST_DDR_REGION_TYPE, 64);
	request(&ab, 1, CALDB_MEM_REGION_TYPE, 32);
	assert(!ath12k_qmi_assign_target_mem_chunk(&ab));
	assert(alive(0) == 1 && alive(1) == 1);
	node.declared[2] = true; node.regions[2] = &caldb;
	assert(!ath12k_qmi_assign_target_mem_chunk(&ab));
	assert(!alive(0) && alive(1) == 2 && dma_frees == 1);
	node.declared[2] = false;
	assert(!ath12k_qmi_assign_target_mem_chunk(&ab));
	assert(alive(0) == 1 && alive(1) == 1 && unmaps == 1);
	request(&ab, 0, HOST_DDR_REGION_TYPE, 128);
	fail_map_after = 0;
	assert(ath12k_qmi_assign_target_mem_chunk(&ab) == -EIO);
	assert(!alive(0) && !alive(1));
	fail_map_after = -1;
	assert(!ath12k_qmi_assign_target_mem_chunk(&ab));
	cleanup(&ab); cleanup(&ab);
	/* Dynamic owner followed by an unknown fixed-PCI request must free it. */
	ab.dev_flags = 0; ab.hif.bus = ATH12K_BUS_PCI;
	ab.qmi.mem_seg_count = 1;
	request(&ab, 0, HOST_DDR_REGION_TYPE, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	ab.dev_flags = 1;
	request(&ab, 0, 0xf0, 32);
	assert(!ath12k_qmi_assign_target_mem_chunk(&ab));
	assert(!alive(0)); cleanup(&ab);
}

static void dt_errors(void)
{
	FIXTURE;
	struct reserved_mem host = { 0x100000, 4096 };
	node.declared[0] = true; node.regions[0] = &host;
	ab.dev_flags = 1; ab.hif.bus = ATH12K_BUS_AHB;
	ab.qmi.mem_seg_count = 1;
	request(&ab, 0, CALDB_MEM_REGION_TYPE, 32);
	node.declared[2] = true; /* Declared but unresolvable is not dynamic. */
	assert(ath12k_qmi_assign_target_mem_chunk(&ab) == -ENODEV);
	assert(!alive(0) && !alive(1));
	request(&ab, 0, HOST_DDR_REGION_TYPE, 32);
	node.host_offset = 4090;
	assert(ath12k_qmi_assign_target_mem_chunk(&ab) == -EINVAL);
	node.host_offset = 4080;
	request(&ab, 0, HOST_DDR_REGION_TYPE, 16);
	assert(!ath12k_qmi_assign_target_mem_chunk(&ab));
	assert(ab.qmi.target_mem[0].paddr == host.base + 4080);
	cleanup(&ab);
	node.host_offset = 4097;
	assert(ath12k_qmi_assign_target_mem_chunk(&ab) == -EINVAL);
	cleanup(&ab);
}

static void pre_start_partner(void)
{
	FIXTURE;
	struct ath12k_base partner; struct device partner_dev; struct device_node partner_node;
	setup(&partner, &ag, &partner_dev, &partner_node);
	ab.qmi.mem_seg_count = 1; request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	void *pool = ab.qmi.target_mem[0].v.addr;
	assert(!ag.num_started && dev.refs == 2);
	partner.qmi.mem_seg_count = 2;
	request(&partner, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64);
	request(&partner, 1, HOST_DDR_REGION_TYPE, 32);
	fail_dma_after = 0;
	assert(ath12k_qmi_alloc_target_mem_chunk(&partner) == -ENOMEM);
	assert(alive(0) == 1 && ag.mlo_mem.chunk[0].v.addr == pool && dev.refs == 2);
	assert(ag.mlo_mem.num_users == 1 && !partner.qmi.mlo_mem_ref);
	fail_dma_after = -1;
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	assert(ag.mlo_mem.num_users == 2 && dma_count == 2);
	partner.qmi.mem_seg_count = 1;
	request(&partner, 0, HOST_DDR_REGION_TYPE, 32);
	assert(ath12k_qmi_alloc_target_mem_chunk(&partner) == -EINVAL);
	assert(alive(0) == 1 && ag.mlo_mem.chunk[0].v.addr == pool && dev.refs == 2);
	request(&partner, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	cleanup(&ab); cleanup(&ab);
	assert(alive(0) == 1 && ag.mlo_mem.num_users == 1 && dev.refs == 2);
	cleanup(&partner); cleanup(&partner);
	assert(!alive(0) && dev.refs == 1 && partner_dev.refs == 1);
}

static void shared_pool(void)
{
	FIXTURE;
	struct ath12k_base partner; struct device partner_dev; struct device_node partner_node;
	setup(&partner, &ag, &partner_dev, &partner_node);
	ab.qmi.mem_seg_count = 2;
	request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, 24);
	request(&ab, 1, MLO_GLOBAL_MEM_REGION_TYPE, 40);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	void *pool = ab.qmi.target_mem[0].v.addr;
	assert((u8 *)ab.qmi.target_mem[1].v.addr == (u8 *)pool + 24);
	struct target_mem_chunk invalid_view = {};
	mutex_lock(&ag.mutex);
	assert(ath12k_qmi_assign_mlo_mem_chunk(&ab, &invalid_view, 63, 2) == -EINVAL);
	assert(ath12k_qmi_assign_mlo_mem_chunk(&ab, &invalid_view, SIZE_MAX, 1) == -EINVAL);
	mutex_unlock(&ag.mutex);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	assert(dma_count == 1 && ag.mlo_mem.num_users == 1 && dev.refs == 2);
	partner.qmi.mem_seg_count = 1; request(&partner, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	assert(partner.qmi.target_mem[0].v.addr == pool && ag.mlo_mem.num_users == 2);
	request(&partner, 0, MLO_GLOBAL_MEM_REGION_TYPE, 96);
	assert(ath12k_qmi_alloc_target_mem_chunk(&partner) == -EINVAL);
	assert(alive(0) == 1 && ag.mlo_mem.num_users == 1);
	/* Once the other borrower leaves, a different complete size is safe. */
	cleanup(&ab);
	assert(!alive(0) && dev.refs == 1);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	assert(ag.mlo_mem.mlo_mem_size == 96 && partner_dev.refs == 2);
	request(&partner, 0, MLO_GLOBAL_MEM_REGION_TYPE, 48);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	assert(ag.mlo_mem.mlo_mem_size == 48 && partner_dev.refs == 2 && alive(0) == 1);
	partner.qmi.mem_seg_count = 1; request(&partner, 0, HOST_DDR_REGION_TYPE, 32);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	assert(ag.mlo_mem.num_users == 0 && partner_dev.refs == 1 && alive(0) == 1);
	cleanup(&partner);
	/* A started core remains an additional lifetime barrier. */
	request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64); ab.qmi.mem_seg_count = 1;
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	ag.num_started = 1; cleanup(&ab);
	assert(alive(0) == 1 && ag.mlo_mem.num_users == 0 && dev.refs == 2);
	ag.num_started = 0; cleanup(&ab);
	assert(!alive(0) && dev.refs == 1);
}

static void fixed_mlo(void)
{
	FIXTURE;
	struct reserved_mem mlo = { 0x200000, 128 };
	struct ath12k_base partner; struct device partner_dev; struct device_node partner_node;
	setup(&partner, &ag, &partner_dev, &partner_node);
	node.declared[3] = true; node.regions[3] = &mlo;
	ab.qmi.mem_seg_count = 2;
	request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, 16);
	request(&ab, 1, MLO_GLOBAL_MEM_REGION_TYPE, 48);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	partner.qmi.mem_seg_count = 1; request(&partner, 0, MLO_GLOBAL_MEM_REGION_TYPE, 64);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&partner));
	assert(!dma_count && map_count == 1 && ag.mlo_mem.fixed_mem);
	assert(ab.qmi.target_mem[1].paddr == mlo.base + 16);
	cleanup(&ab); assert(alive(1) == 1);
	cleanup(&partner); cleanup(&partner);
	assert(!alive(1) && unmaps == 1 && !dma_frees && dev.refs == 1);
}

static void request_bounds(void)
{
	FIXTURE;
	ab.qmi.mem_seg_count = 2;
	request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, U32_MAX);
	request(&ab, 1, MLO_GLOBAL_MEM_REGION_TYPE, 1);
	assert(ath12k_qmi_alloc_target_mem_chunk(&ab) == -EINVAL);
	assert(!dma_count && !ag.mlo_mem.num_users);
	ab.qmi.mem_seg_count = 1;
	request(&ab, 0, MLO_GLOBAL_MEM_REGION_TYPE, (u32)INT_MAX + 1U);
	assert(ath12k_qmi_alloc_target_mem_chunk(&ab) == -EINVAL);
	request(&ab, 0, AFC_REGION_TYPE, AFC_MEM_SIZE + 1);
	assert(ath12k_qmi_alloc_target_mem_chunk(&ab) == -EINVAL);
	request(&ab, 0, AFC_REGION_TYPE, AFC_MEM_SIZE);
	assert(!ath12k_qmi_alloc_target_mem_chunk(&ab));
	cleanup(&ab); cleanup(&ab);
}

int main(int argc, char **argv)
{
	assert(argc == 2);
	if (!strcmp(argv[1], "coherent")) coherent();
	else if (!strcmp(argv[1], "allocation-failure")) allocation_failure();
	else if (!strcmp(argv[1], "fixed")) fixed();
	else if (!strcmp(argv[1], "dt-errors")) dt_errors();
	else if (!strcmp(argv[1], "pre-start-partner")) pre_start_partner();
	else if (!strcmp(argv[1], "shared-pool")) shared_pool();
	else if (!strcmp(argv[1], "fixed-mlo")) fixed_mlo();
	else if (!strcmp(argv[1], "request-bounds")) request_bounds();
	else assert(!"unknown scenario");
	assert(!alive(0) && !alive(1));
	return 0;
}
