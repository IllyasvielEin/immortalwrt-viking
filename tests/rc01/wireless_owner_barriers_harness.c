/* Exercise the actual permanent-stop barrier placement, without hardware. */
#include <assert.h>
#include <stdbool.h>
struct ath12k_hw_group;
struct ath12k_base {
 bool pdev_live;
 unsigned long dev_flags;
 struct ath12k_hw_group *ag;
};
struct ath12k_hw_group {
 int mutex, num_devices;
 unsigned long flags;
 struct ath12k_base *ab[2];
};
static int ath12k_hw_group_mutex = 1;
static int thermal_stops, mac_stops, pdev_stops;
#define ATH12K_GROUP_FLAG_REGISTERED 0
#define ATH12K_FLAG_REGISTERED 0
#define lockdep_assert_held(p) assert(*(p) == 1)
#define clear_bit(n,p) (*(p) &= ~(1UL << (n)))
static void mutex_lock(int *p) { assert(!*p); *p = 1; }
static void mutex_unlock(int *p) { assert(*p); *p = 0; }
static void ath12k_dp_ds_stop_group(struct ath12k_hw_group *ag) { (void)ag; }
static void ath12k_mac_mlo_teardown(struct ath12k_hw_group *ag) { assert(ag->mutex); }
static void ath12k_hif_irq_disable(struct ath12k_base *ab) { (void)ab; }
static void ath12k_thermal_unregister(struct ath12k_base *ab)
{
 assert(!ab->ag->mutex && "teardown resource lock held");
 thermal_stops++;
}
static void ath12k_mac_unregister(struct ath12k_hw_group *ag)
{
 assert(!ag->mutex && "teardown resource lock held");
 assert(thermal_stops == 2);
 mac_stops++;
}
static void ath12k_core_device_cleanup(struct ath12k_base *ab)
{
 assert(!ab->ag->mutex && mac_stops == 1);
 pdev_stops++;
}
#include "owner-barriers.inc"
int main(void)
{
 struct ath12k_hw_group ag = {.num_devices = 2, .flags = 1};
 struct ath12k_base ab[2] = {{.pdev_live = true, .ag = &ag},
                            {.pdev_live = true, .ag = &ag}};
 ag.ab[0] = &ab[0]; ag.ab[1] = &ab[1];
 ath12k_core_hw_group_stop(&ag);
 assert(!ag.mutex && thermal_stops == 2 && mac_stops == 1 && pdev_stops == 2);
}
