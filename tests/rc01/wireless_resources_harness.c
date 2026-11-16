#include <assert.h>
#include <stdbool.h>
#include <errno.h>
#include <stdlib.h>
#define ARRAY_SIZE(a) (sizeof(a)/sizeof((a)[0]))
#define ath12k_warn(...) ((void)0)
#define WARN_ON(x) (x)
#define spin_lock_irqsave(lock,flags) do { flags=0; assert(++*(lock)==1); } while(0)
#define spin_unlock_irqrestore(lock,flags) do { (void)flags; assert(--*(lock)==0); } while(0)
struct napi_struct { int unused; };
struct ath12k_base;
struct ath12k_ext_irq_grp { struct ath12k_base *ab; int irq_lock;
 bool irq_enabled, irq_stopped, napi_enabled; unsigned num_irq, irqs[3]; struct napi_struct napi; };
#define ATH12K_EXT_IRQ_GRP_NUM_MAX 2
struct hal_srng { bool initialized; };
struct ath12k_ce_ring { bool initialized; unsigned hal_ring_id, nentries, write_index, sw_index; };
struct ath12k_ce_pipe { struct ath12k_ce_ring *src_ring,*dest_ring,*status_ring; unsigned rx_buf_needed; };
struct params { int ce_count; };
struct ath12k_hal { struct hal_srng srng_list[9]; int *srng_config; void *dev;
 struct { int (*create_srng_config)(struct ath12k_hal *); } *ops; };
struct ath12k_base { struct ath12k_ext_irq_grp ext_irq_grp[2]; int irq_num[6];
 struct { struct ath12k_ce_pipe ce_pipe[3]; } ce;
 struct { struct { int shadow_reg_v3,shadow_reg_v3_len; } ce_cfg; } qmi;
 struct params *hw_params; struct ath12k_hal hal; bool dp_live; void *dev; };
static int depths[6]={1,1,1,1,1,1};
static void enable_irq(int n) { assert(depths[n]==1); depths[n]--; }
static void disable_irq_nosync(int n) { assert(depths[n]==0); depths[n]++; }
static void napi_disable(struct napi_struct *n) { (void)n; }
static int init_n, fail_n;
enum { HAL_CE_SRC, HAL_CE_DST, HAL_CE_DST_STATUS };
static int ath12k_ce_init_ring(struct ath12k_base *ab, struct ath12k_ce_ring *ring, int ce, int type) {
 init_n++; if(init_n==fail_n) return -ENOMEM;
 ring->hal_ring_id=ce*3+type; ring->initialized=true;
 ab->hal.srng_list[ring->hal_ring_id].initialized=true; return 0;
}
#define ath12k_ce_get_shadow_config(...) ((void)0)
static int config_alloc, rdp_alloc, wrp_alloc, keys, hal_fail;
static int create_config(struct ath12k_hal *h) { h->srng_config=malloc(sizeof(int)); config_alloc++; return 0; }
static int ath12k_hal_alloc_cont_rdp(struct ath12k_hal *h) { (void)h; if(hal_fail==1) return -ENOMEM; rdp_alloc++; return 0; }
static int ath12k_hal_alloc_cont_wrp(struct ath12k_hal *h) { (void)h; if(hal_fail==2) return -ENOMEM; wrp_alloc++; return 0; }
#define ath12k_hal_free_cont_rdp(h) (--rdp_alloc)
#define ath12k_hal_free_cont_wrp(h) (--wrp_alloc)
#define ath12k_hal_register_srng_lock_keys(h) (++keys)
#define ath12k_hal_unregister_srng_lock_keys(h) assert(--keys==0)
static void config_free(void *p) { if(p) config_alloc--; free(p); }
#define kfree config_free
#include "resources-functions.inc"
int main(void) {
 struct params params={.ce_count=3}; struct ath12k_base ab={.hw_params=&params};
 struct ath12k_ce_ring rings[9]={};
 for(int i=0;i<3;i++) { ab.ce.ce_pipe[i].src_ring=&rings[3*i];
 ab.ce.ce_pipe[i].dest_ring=&rings[3*i+1]; ab.ce.ce_pipe[i].status_ring=&rings[3*i+2]; }
 for(int fail=1;fail<=9;fail++) {
  init_n=0;fail_n=fail;assert(ath12k_ce_init_pipes(&ab)==-ENOMEM);
  for(int i=0;i<9;i++) assert(!rings[i].initialized && !ab.hal.srng_list[i].initialized);
 }
 fail_n=0; assert(!ath12k_ce_init_pipes(&ab)); ath12k_ce_deinit_pipes(&ab); ath12k_ce_deinit_pipes(&ab);
 for(int i=0;i<2;i++) { struct ath12k_ext_irq_grp *g=&ab.ext_irq_grp[i];
  g->ab=&ab;g->num_irq=1;g->irqs[0]=i;ab.irq_num[i]=i;g->irq_stopped=false;
  ath12k_ahb_ext_grp_enable(g);ath12k_ahb_ext_grp_enable(g);assert(depths[i]==0);
  ath12k_ahb_ext_grp_disable(g);ath12k_ahb_ext_grp_disable(g);assert(depths[i]==1);
  ath12k_ahb_ext_grp_enable(g);g->napi_enabled=true;
 }
 __ath12k_ahb_ext_irq_disable(&ab);
 for(int i=0;i<2;i++) { ath12k_ahb_ext_grp_enable(&ab.ext_irq_grp[i]);assert(depths[i]==1); }
 __ath12k_ahb_ext_irq_disable(&ab);
 typeof(*ab.hal.ops) ops={.create_srng_config=create_config};ab.hal.ops=&ops;
 for(int i=1;i<=2;i++) { hal_fail=i; assert(ath12k_hal_srng_init(&ab)==-ENOMEM);
  assert(!config_alloc && !rdp_alloc && !wrp_alloc && !keys && !ab.hal.srng_config);
  ath12k_hal_srng_deinit(&ab);
 }
 hal_fail=0; assert(!ath12k_hal_srng_init(&ab));ab.dp_live=true;
 ath12k_hal_srng_deinit(&ab);assert(config_alloc==1 && rdp_alloc==1 && wrp_alloc==1 && keys==1);
 assert(ath12k_hal_srng_init(&ab)==-EBUSY);
 ab.dp_live=false;ath12k_hal_srng_deinit(&ab);ath12k_hal_srng_deinit(&ab);
 assert(!config_alloc && !rdp_alloc && !wrp_alloc && !keys);
}
