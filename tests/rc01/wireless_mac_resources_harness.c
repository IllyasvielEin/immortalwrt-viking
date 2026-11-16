#include <assert.h>
#include <stdbool.h>
#include <stdlib.h>
#include <errno.h>
typedef unsigned u32;
#define NL80211_BAND_2GHZ 0
#define NL80211_BAND_5GHZ 1
#define NL80211_BAND_6GHZ 2
#define ATH12K_DEFAULT_NOISE_FLOOR -95
#define SET_IEEE80211_DEV(...) ((void)0)
struct ieee80211_supported_band {void *channels;};
struct ieee80211_hw {int unused;};
struct ath12k_base {int unused;};
struct ath12k_pdev_cap {int supported_bands;};
struct ath12k_pdev {struct ath12k_pdev_cap cap;};
struct ath12k {struct ath12k_base *ab;struct ath12k_pdev *pdev;
 int txmgmt_idr,txmgmt_empty_waitq,txmgmt_idr_lock,max_num_stations,max_num_peers;
 struct {struct ieee80211_supported_band sbands[3];} mac;
 struct {int min_nf_dbm,temp_offset,noise_floor;} rssi_info;};
struct ath12k_hw {struct ieee80211_hw *hw;bool registered;struct ath12k radio[2];};
#define for_each_ar(ah,ar,i) for((i)=0;(i)<2 && ((ar)=&(ah)->radio[i],1);(i)++)
static int all_closed,channels,fail_channels,unregisters;
static void channel_free(void *p) {assert(all_closed);if(p)channels--;free(p);}
#define kfree channel_free
#define idr_for_each(idr,cb,ar) assert(all_closed && *(idr)==1)
#define idr_destroy(idr) do {assert(*(idr)==1);*(idr)=0;} while(0)
#define ath12k_pdev_caps_update(...) ((void)0)
static int ath12k_mac_setup_channels_rates(struct ath12k *ar,int bands,struct ieee80211_supported_band *out[]) {
 (void)bands;(void)out;for(int i=0;i<3;i++){ar->mac.sbands[i].channels=malloc(1);channels++;}return fail_channels?-ENOMEM:0;
}
#define ath12k_mac_setup_ht_vht_cap(...) ((void)0)
#define ath12k_mac_setup_sband_iftype_data(...) ((void)0)
#define ath12k_core_get_max_station_per_radio(...) 10
#define ath12k_core_get_max_peers_per_radio(...) 20
#define ath12k_thermal_init_configs(...) ((void)0)
#define ath12k_debugfs_unregister(...) assert(all_closed)
#define ath12k_fw_stats_reset(...) assert(all_closed)
static void ieee80211_unregister_hw(struct ieee80211_hw *hw) {(void)hw;assert(all_closed);unregisters++;}
static void ath12k_mac_cleanup_iface_combinations(struct ath12k_hw *ah) {(void)ah;assert(all_closed);}
#include "mac-resources-functions.inc"
int main(void) {
 struct ieee80211_hw hw={};struct ath12k_hw ah={.hw=&hw};struct ath12k_base ab={};struct ath12k_pdev pdev={};u32 ht;
 for(int i=0;i<2;i++){ah.radio[i].ab=&ab;ah.radio[i].pdev=&pdev;ah.radio[i].txmgmt_idr=1;}
 assert(!ath12k_mac_setup_register(&ah.radio[0],&ht,NULL));fail_channels=1;
 assert(ath12k_mac_setup_register(&ah.radio[1],&ht,NULL)==-ENOMEM);
 assert(channels==6 && !unregisters);
 all_closed=1;ath12k_mac_hw_unregister(&ah);assert(!channels && !unregisters);
 for(int i=0;i<2;i++)ah.radio[i].txmgmt_idr=1;
 fail_channels=0;all_closed=0;
 for(int i=0;i<2;i++)assert(!ath12k_mac_setup_register(&ah.radio[i],&ht,NULL));
 ah.registered=true;all_closed=1;ath12k_mac_hw_unregister(&ah);
 assert(!channels && unregisters==1 && !ah.registered);
}
