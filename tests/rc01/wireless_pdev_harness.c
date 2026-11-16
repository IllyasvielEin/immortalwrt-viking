#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdio.h>
#include <errno.h>
#define IS_REACHABLE(x) 1
#define IS_ERR(p) ((uintptr_t)(p)>(uintptr_t)-4096)
#define PTR_ERR(p) ((int)(intptr_t)(p))
#define ERR_PTR(n) ((void *)(intptr_t)(n))
#define ath12k_err(...) ((void)0)
#define scnprintf snprintf
struct mutex { int held; };
static void mutex_lock(struct mutex *m) {assert(!m->held);m->held=1;}
static void mutex_unlock(struct mutex *m) {assert(m->held);m->held=0;}
static struct mutex *guard_take(struct mutex *m) {mutex_lock(m);return m;}
static void guard_drop(struct mutex **m) {mutex_unlock(*m);}
#define guard(t) struct mutex *__attribute__((cleanup(guard_drop))) guard_var=guard_take
struct device { int kobj; };
struct thermal_cooling_device { struct device device; };
struct wiphy { struct device dev; };
struct ieee80211_hw { struct wiphy *wiphy; };
struct ath12k_hw { struct ieee80211_hw *hw; };
struct ath12k_base;
struct ath12k { struct ath12k_base *ab; struct ath12k_hw *ah; unsigned hw_link_id;
 struct { struct thermal_cooling_device *cdev; struct device *hwmon_dev; } thermal; };
struct ath12k_base { struct { struct ath12k *ar; } pdevs[3]; int num_radios;
 bool pdev_live; struct mutex core_lock; };
static int step, fail_at, cdevs, hwmons, links, dp_live, irq_live, fail_dp, rfkill_error;
static const int ath12k_thermal_ops,ath12k_hwmon_groups;
static struct thermal_cooling_device *thermal_cooling_device_register(const char *name,struct ath12k *ar,const int *ops) {
 (void)name;(void)ar;(void)ops;if(++step==fail_at)return ERR_PTR(-ENOMEM);
 cdevs++;return calloc(1,sizeof(struct thermal_cooling_device));
}
static void thermal_cooling_device_unregister(struct thermal_cooling_device *d) {assert(d && cdevs>0);cdevs--;free(d);}
static int sysfs_create_link(int *a,int *b,const char *name) {(void)a;(void)b;(void)name;if(++step==fail_at)return -ENOMEM;links++;return 0;}
static void sysfs_remove_link(int *a,const char *name) {(void)a;(void)name;assert(links>0);links--;}
static struct device *hwmon_device_register_with_groups(struct device *dev,const char *name,struct ath12k *ar,const int groups) {
 (void)dev;(void)name;(void)ar;(void)groups;if(++step==fail_at)return ERR_PTR(-ENOMEM);hwmons++;return calloc(1,sizeof(struct device));
}
static void hwmon_device_unregister(struct device *d) {assert(d && hwmons>0);hwmons--;free(d);}
static int ath12k_dp_pdev_alloc(struct ath12k_base *ab) {(void)ab;if(fail_dp)return -ENOMEM;assert(!dp_live);dp_live=1;return 0;}
static void ath12k_dp_pdev_free(struct ath12k_base *ab) {(void)ab;assert(dp_live);dp_live=0;}
#define ath12k_debugfs_pdev_create(...) ((void)0)
static void ath12k_hif_irq_enable(struct ath12k_base *ab) {(void)ab;assert(!irq_live);irq_live=1;}
static void ath12k_hif_irq_disable(struct ath12k_base *ab) {(void)ab;irq_live=0;}
static int ath12k_core_rfkill_config(struct ath12k_base *ab) {(void)ab;return rfkill_error;}
#include "pdev-functions.inc"
int main(void) {
 struct ath12k_base ab={.num_radios=3};struct ath12k radios[3]={};
 struct wiphy wiphy={};struct ieee80211_hw hw={.wiphy=&wiphy};struct ath12k_hw ah={.hw=&hw};
 for(int i=0;i<3;i++) {ab.pdevs[i].ar=&radios[i];radios[i].ab=&ab;radios[i].ah=&ah;radios[i].hw_link_id=i;}
 for(int fail=1;fail<=9;fail++) {
  step=0;fail_at=fail;assert(ath12k_core_device_setup(&ab)==-ENOMEM);
  assert(!ab.core_lock.held && !ab.pdev_live && !dp_live && !cdevs && !hwmons && !links && !irq_live);
  ath12k_core_pdev_destroy(&ab);
 }
 fail_at=0;fail_dp=1;assert(ath12k_core_device_setup(&ab)==-ENOMEM);assert(!dp_live);fail_dp=0;
 rfkill_error=-EIO;assert(ath12k_core_device_setup(&ab)==-EIO);
 assert(!ab.pdev_live && !dp_live && !cdevs && !hwmons && !links && !irq_live);
 rfkill_error=-EOPNOTSUPP;assert(!ath12k_core_device_setup(&ab));
 assert(ab.pdev_live && dp_live && cdevs==3 && hwmons==3 && links==3 && irq_live);
 ath12k_core_device_cleanup(&ab);ath12k_core_pdev_destroy(&ab);
 assert(!ab.pdev_live && !dp_live && !cdevs && !hwmons && !links && !irq_live);
}
