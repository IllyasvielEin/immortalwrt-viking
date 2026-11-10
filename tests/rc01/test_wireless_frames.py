"""Host probes of prepared 915 native TX gating and 917 RX prefix handling.

These do not exercise DMA, firmware, the remaining RX QoS/crypto rebuild, or
end-to-end delivery. Core frame predicates and transformations come verbatim
from the prepared source, rather than being reimplemented as test predicates.
"""
from pathlib import Path
import os
import re
import shutil
import subprocess
import tempfile
import unittest

WIRELESS = os.environ.get("RC01_WIRELESS_SOURCE")
HERE = Path(__file__).resolve().parent


def function(source, name):
    match = re.search(r"^(?:static (?:inline )?)?(?:unsigned int __attribute_const__|bool|int|static int)\s*\n?" + name + r"\([^)]*\)\n\{", source, re.M)
    if match is None:
        raise AssertionError(f"missing prepared function {name}")
    start = match.start()
    # Include a standalone 'static int' prefix above a multi-line name.
    if source[max(0, start - 11):start] == "static int\n":
        start -= 11
    end = source.index("\n}", match.end()) + 2
    return source[start:end] + "\n"


@unittest.skipUnless(WIRELESS and shutil.which("cc"), "set RC01_WIRELESS_SOURCE to prepared tree; host cc required")
class WirelessFramePrefixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="rc01-frame-prefixes-")
        cls.addClassCleanup(cls.temporary.cleanup)
        work = Path(cls.temporary.name)
        tree = Path(WIRELESS)
        ieee = (tree / "include/linux/ieee80211.h").read_text()
        ieee += "\n" + (tree / "include/linux/ieee80211-ht.h").read_text()
        generated = []
        for name in ("IEEE80211_FCTL_FROMDS", "IEEE80211_FCTL_TODS", "IEEE80211_FCTL_ORDER",
                     "IEEE80211_FCTL_FTYPE", "IEEE80211_FCTL_STYPE", "IEEE80211_FTYPE_MGMT",
                     "IEEE80211_FTYPE_CTL", "IEEE80211_FTYPE_DATA", "IEEE80211_FTYPE_EXT",
                     "IEEE80211_STYPE_QOS_DATA", "IEEE80211_STYPE_NULLFUNC", "IEEE80211_QOS_CTL_LEN",
                     "IEEE80211_HT_CTL_LEN"):
            generated.append(re.search(rf"^#define {name}\s+[^\n]+", ieee, re.M)[0])
        for name in ("ieee80211_has_a4", "ieee80211_has_order", "ieee80211_is_data", "ieee80211_is_ext",
                     "ieee80211_is_mgmt", "ieee80211_is_ctl", "ieee80211_is_data_qos", "ieee80211_is_nullfunc"):
            generated.append(function(ieee, name))
        generated.append(function((tree / "net/wireless/util.c").read_text(), "ieee80211_hdrlen"))
        (work / "prepared-ieee.h").write_text("\n".join(generated))
        driver = tree / "drivers/net/wireless/ath/ath12k"
        rx = (driver / "dp_rx.c").read_text()
        start = rx.index("static void ath12k_dp_rx_h_undecap_nwifi(")
        prefix_start = rx.index("\t/* pull decapped header */", start)
        prefix_end = rx.index("\t/*  Rebuild qos header */", prefix_start)
        (work / "prepared-rx-prefix.h").write_text(rx[prefix_start:prefix_end])
        tx = (driver / "wifi7/dp_tx.c").read_text()
        (work / "prepared-eapol.h").write_text(function(tx, "ath12k_wifi7_dp_tx_encap_eapol"))
        start = tx.index("\tcase HAL_TCL_ENCAP_TYPE_NATIVE_WIFI:", tx.index("int ath12k_wifi7_dp_tx("))
        end = tx.index("\tcase HAL_TCL_ENCAP_TYPE_RAW:", start)
        (work / "prepared-native-case.h").write_text(tx[start:end])
        cls.binary = work / "frame-probe"
        result = subprocess.run([shutil.which("cc"), "-std=gnu11", "-Werror", "-I", str(work),
                                 str(HERE / "frames_harness.c"), "-o", str(cls.binary)],
                                capture_output=True, text=True)
        if result.returncode:
            raise AssertionError(result.stderr)

    def scenario(self, name):
        result = subprocess.run([str(self.binary), name], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rx_padding_positive_and_format_length_snap_negatives(self):
        self.scenario("rx-prefix")

    def test_tx_encrypted_eapol_uses_real_raw_conversion_once(self):
        self.scenario("tx-encrypted-eapol")

    def test_tx_native_data_no_key_and_null_frames_keep_their_branches(self):
        self.scenario("tx-other-frames")

    def test_tx_expansion_failure_propagates_without_publishing_extension(self):
        self.scenario("tx-allocation-failure")
