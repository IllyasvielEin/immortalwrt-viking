/* Limited host probes: real RX prefix and native TX case, stubbed skb I/O. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

typedef uint8_t u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint16_t __le16;
#ifndef __attribute_const__
#define __attribute_const__
#endif
#if __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#define cpu_to_le16(x) ((u16)(x))
#define cpu_to_be16(x) __builtin_bswap16((u16)(x))
#else
#define cpu_to_le16(x) __builtin_bswap16((u16)(x))
#define cpu_to_be16(x) ((u16)(x))
#endif
#define ALIGN(x, a) (((x) + (a) - 1) & ~((a) - 1))
#define GFP_ATOMIC 0
#define ETH_P_PAE 0x888e
#define IEEE80211_OFFLOAD_ENCAP_ENABLED 1
#define ATH12K_SKB_CIPHER_SET 2
#define ATH12K_HW_QCN9274_HW10 10
#define ATH12K_HW_QCN9274_HW20 20
#define HAL_TCL_DATA_CMD_INFO2_TO_FW 1
#define u32_encode_bits(value, mask) ((u32)(value) * (mask))

enum hal_encrypt_type { HAL_ENCRYPT_TYPE_OPEN, HAL_ENCRYPT_TYPE_CCMP, HAL_ENCRYPT_TYPE_GCMP };
enum { HAL_TCL_ENCAP_TYPE_NATIVE_WIFI, HAL_TCL_ENCAP_TYPE_RAW, HAL_TCL_ENCAP_TYPE_ETHERNET };
struct ieee80211_hdr { __le16 frame_control; u8 rest[34]; };
struct sk_buff { u8 storage[512]; u8 *data; size_t len; u16 protocol; };
struct hal_tx_info { unsigned int flags0; int encap_type; enum hal_encrypt_type encrypt_type; };
struct ath12k_skb_cb { unsigned int flags; enum hal_encrypt_type cipher; };
struct ath12k_base { int hw_rev; };
struct ath12k_dp { struct ath12k_base *ab; };
struct ath12k_pdev_dp { struct ath12k_dp *dp; };
struct vif { unsigned int offload_flags; };
struct ahvif { struct vif *vif; };

#include "prepared-ieee.h"

static u16 get_unaligned_le16(const u8 *p) { return p[0] | ((u16)p[1] << 8); }
static void *skb_pull(struct sk_buff *skb, size_t n)
{
	if (n > skb->len) return NULL;
	skb->data += n; skb->len -= n; return skb->data;
}
static void *skb_push(struct sk_buff *skb, size_t n)
{
	assert((size_t)(skb->data - skb->storage) >= n);
	skb->data -= n; skb->len += n; return skb->data;
}
static void *skb_put(struct sk_buff *skb, size_t n)
{
	assert((size_t)(skb->data - skb->storage) + skb->len + n <= sizeof(skb->storage));
	void *tail = skb->data + skb->len; skb->len += n; return tail;
}
static int expansion_calls, native_calls, fail_expand;
static int pskb_expand_head(struct sk_buff *skb, int head, int tail, int flags)
{
	(void)flags; expansion_calls++;
	if (fail_expand) return -ENOMEM;
	assert((size_t)(skb->data - skb->storage) >= (size_t)head);
	assert((size_t)(skb->data - skb->storage) + skb->len + tail <= sizeof(skb->storage));
	return 0;
}
static enum hal_encrypt_type ath12k_dp_tx_get_encrypt_type(enum hal_encrypt_type cipher) { return cipher; }
static u8 ath12k_dp_tx_crypto_iv_len(enum hal_encrypt_type cipher) { assert(cipher != HAL_ENCRYPT_TYPE_OPEN); return 8; }
static u8 ath12k_dp_tx_crypto_icv_len(enum hal_encrypt_type cipher) { return cipher == HAL_ENCRYPT_TYPE_CCMP ? 8 : 16; }
static void ath12k_dp_tx_encap_nwifi(struct sk_buff *skb) { (void)skb; native_calls++; }

#include "prepared-eapol.h"

static void rx_prefix(struct ath12k_pdev_dp *dp_pdev, struct sk_buff *msdu)
{
	struct ieee80211_hdr *hdr;
	size_t hdr_len;
#include "prepared-rx-prefix.h"
}
struct tx_result { struct hal_tx_info ti; bool extension, different, null_frame; };
static int native_branch(struct sk_buff *skb, struct ath12k_skb_cb *skb_cb,
			unsigned int offload, bool *done, struct tx_result *out)
{
	struct ieee80211_hdr *hdr = (void *)skb->data;
	struct vif vif = { offload };
	struct ahvif context = { &vif }, *ahvif = &context;
	struct hal_tx_info ti = { .encap_type = HAL_TCL_ENCAP_TYPE_NATIVE_WIFI };
	bool is_null_frame = false, is_diff_encap = false, msdu_ext_desc = false;
	bool eapol_encap_done = *done;
	int ret = 0;
	switch (ti.encap_type) {
#include "prepared-native-case.h"
	default: assert(!"native branch probe called with wrong encap");
	}
fail_remove_tx_buf:
	*done = eapol_encap_done;
	*out = (struct tx_result){ ti, msdu_ext_desc, is_diff_encap, is_null_frame };
	return ret;
}
static void frame(struct sk_buff *skb, __le16 control, size_t len)
{
	memset(skb, 0, sizeof(*skb)); skb->data = skb->storage + 64; skb->len = len;
	((struct ieee80211_hdr *)skb->data)->frame_control = control;
}

static void rx_cases(void)
{
	struct ath12k_base ab = { ATH12K_HW_QCN9274_HW10 };
	struct ath12k_dp dp = { &ab }; struct ath12k_pdev_dp pdev = { &dp };
	const u8 snap[] = { 0xaa, 0xaa, 0x03, 0x00, 0x00, 0x00, 0x08, 0x06 };
	__le16 a4 = cpu_to_le16(IEEE80211_FTYPE_DATA | IEEE80211_FCTL_TODS | IEEE80211_FCTL_FROMDS);
	struct sk_buff skb;
	frame(&skb, a4, 40); memcpy(skb.data + 32, snap, sizeof(snap));
	u8 *start = skb.data; rx_prefix(&pdev, &skb);
	assert(skb.data == start + 32 && skb.len == 8 && !memcmp(skb.data, snap, 8));
	/* Already unpadded frames must keep the complete SNAP header. */
	frame(&skb, a4, 38); memcpy(skb.data + 30, snap, sizeof(snap));
	start = skb.data; rx_prefix(&pdev, &skb);
	assert(skb.data == start + 30 && skb.len == 8 && !memcmp(skb.data, snap, 8));
	/* Truncated SNAP, a different chip, bad padding and bad SNAP all use
	 * the original hdrlen pull. No later QoS/crypto rebuild is included.
	 */
	for (int which = 0; which < 5; which++) {
		frame(&skb, a4, which == 0 ? 37 : 40);
		memcpy(skb.data + 32, snap, which == 0 ? 5 : 8);
		ab.hw_rev = which == 1 ? ATH12K_HW_QCN9274_HW20 : ATH12K_HW_QCN9274_HW10;
		if (which == 2) skb.data[30] = 1;
		if (which == 3) skb.data[34] = 0x04;
		if (which == 4) skb.data[36] = 1;
		start = skb.data; rx_prefix(&pdev, &skb);
		assert(skb.data == start + 30);
	}
	ab.hw_rev = ATH12K_HW_QCN9274_HW10;
	frame(&skb, cpu_to_le16(IEEE80211_FTYPE_DATA), 40);
	memcpy(skb.data + 32, snap, 8); start = skb.data; rx_prefix(&pdev, &skb);
	assert(skb.data == start + 24); /* Not four-address. */
	frame(&skb, a4 | cpu_to_le16(IEEE80211_STYPE_QOS_DATA), 40);
	memcpy(skb.data + 32, snap, 8); start = skb.data; rx_prefix(&pdev, &skb);
	assert(skb.data == start + 32); /* Already aligned hdrlen. */
}

static void tx_eapol(void)
{
	for (int cipher = HAL_ENCRYPT_TYPE_CCMP; cipher <= HAL_ENCRYPT_TYPE_GCMP; cipher++) {
		struct sk_buff skb; frame(&skb, cpu_to_le16(IEEE80211_FTYPE_DATA), 40);
		skb.protocol = cpu_to_be16(ETH_P_PAE);
		memset(skb.data + 24, 0x5a, 16);
		u8 original[40]; memcpy(original, skb.data, 40);
		struct ath12k_skb_cb cb = { ATH12K_SKB_CIPHER_SET, cipher };
		bool done = false; struct tx_result out;
		int before = expansion_calls;
		assert(!native_branch(&skb, &cb, 0, &done, &out));
		assert(done && expansion_calls == before + 1 && out.extension && !native_calls);
		assert(out.ti.encap_type == HAL_TCL_ENCAP_TYPE_RAW && out.ti.encrypt_type == cipher);
		assert(out.ti.flags0 & HAL_TCL_DATA_CMD_INFO2_TO_FW);
		assert(!memcmp(skb.data, original, 24));
		for (int j = 0; j < 8; j++) assert(!skb.data[24 + j]);
		assert(!memcmp(skb.data + 32, original + 24, 16));
		size_t icv = cipher == HAL_ENCRYPT_TYPE_CCMP ? 8 : 16;
		assert(skb.len == 40 + 8 + icv);
		for (size_t j = 0; j < icv; j++) assert(!skb.data[48 + j]);
		size_t len = skb.len;
		assert(!native_branch(&skb, &cb, 0, &done, &out));
		assert(expansion_calls == before + 1 && skb.len == len && out.extension);
		assert(out.ti.encap_type == HAL_TCL_ENCAP_TYPE_RAW && out.ti.encrypt_type == cipher);
	}
}

static void tx_other(void)
{
	for (int encrypted = 0; encrypted < 2; encrypted++)
	for (int eapol = 0; eapol < 2; eapol++)
	for (int null_frame = 0; null_frame < 2; null_frame++)
	for (int offload = 0; offload < 2; offload++) {
		if (encrypted && eapol) continue; /* Covered by the conversion probe. */
		struct sk_buff skb;
		frame(&skb, cpu_to_le16(IEEE80211_FTYPE_DATA | (null_frame ? IEEE80211_STYPE_NULLFUNC : 0)), 40);
		skb.protocol = cpu_to_be16(eapol ? ETH_P_PAE : 0x0800);
		struct ath12k_skb_cb cb = { encrypted ? ATH12K_SKB_CIPHER_SET : 0, HAL_ENCRYPT_TYPE_CCMP };
		struct tx_result out; bool done = false;
		int previous_native = native_calls;
		assert(!native_branch(&skb, &cb, offload ? IEEE80211_OFFLOAD_ENCAP_ENABLED : 0, &done, &out));
		assert(!expansion_calls && !done && skb.len == 40);
		assert(out.extension == (bool)offload);
		assert(native_calls == previous_native + (offload ? 0 : 1));
		assert(out.different == (bool)(offload && (eapol || null_frame)));
		assert(out.ti.encap_type == HAL_TCL_ENCAP_TYPE_NATIVE_WIFI);
	}
}
static void tx_failure(void)
{
	struct sk_buff skb; frame(&skb, cpu_to_le16(IEEE80211_FTYPE_DATA), 40);
	skb.protocol = cpu_to_be16(ETH_P_PAE);
	struct ath12k_skb_cb cb = { ATH12K_SKB_CIPHER_SET, HAL_ENCRYPT_TYPE_CCMP };
	struct tx_result out; bool done = false; fail_expand = 1;
	assert(native_branch(&skb, &cb, 0, &done, &out) == -ENOMEM);
	assert(expansion_calls == 1 && !native_calls && !done && !out.extension && skb.len == 40);
}
int main(int argc, char **argv)
{
	assert(argc == 2);
	if (!strcmp(argv[1], "rx-prefix")) rx_cases();
	else if (!strcmp(argv[1], "tx-encrypted-eapol")) tx_eapol();
	else if (!strcmp(argv[1], "tx-other-frames")) tx_other();
	else if (!strcmp(argv[1], "tx-allocation-failure")) tx_failure();
	else assert(!"unknown scenario");
	return 0;
}
