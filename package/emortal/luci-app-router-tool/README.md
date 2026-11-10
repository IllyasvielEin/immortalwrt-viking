# Router Tool wireless defaults

## Manual network management

Set `router-tool.main.auto_network='0'`, or disable **Automatic routed presets**
in Network → Interfaces, to retain manual LAN/WAN, DHCP/RA and firewall-zone
configuration. Platform offload/steering defaults still initialize once, and
later explicit acceleration choices remain authoritative. Wireless defaults
are independent. The default is `1`, preserving existing routed presets.

This option does not convert a network to a bypass router or restore a previous
snapshot. Set the address, services and port roles explicitly. Relay AP mode
remains an explicit, separate mode switch.

Build systems may set the optional wireless defaults in `/etc/config/router-tool`:

```uci
config settings 'main'

config wireless 'defaults'
	option ssid ''
	option key ''
```

When both values are non-empty, `ssid` produces names such as
`Home-2.4G` and `Home-5.2G`, with the same key on every band. If either value is
empty, Router Tool uses the system hostname, vendor, and band in the SSID and
`12345678` as the key. Hostname case is preserved and the vendor's first letter
is capitalized. The suffix follows the radio's capabilities (`2.4G`, `5.2G`,
`5.8G`, or `6G`). Names are shortened to 32 bytes without splitting UTF-8
characters. A custom key must contain 8 to 63 printable ASCII characters,
supporting both WPA2 and WPA3-SAE.

These settings are initialization defaults. Only unmarked, untouched APs with
`OWRT` / `12345678` / `psk2+ccmp` are initialized. Existing custom credentials,
STA/mesh/WDS/MLO profiles, and radios with `defaults_done=1` are preserved.
Subsequent LuCI edits and upgrades that retain configuration do not reapply the
build defaults. Do not clear the markers to enforce new credentials on an
already configured router; edit its Wireless form instead.

For eligible APs, 2.4 GHz starts on channel 1 with 20 MHz width; 5 and 6 GHz use
automatic channel selection. Low 5 GHz and 6 GHz request up to 160 MHz, while
high 5 GHz requests up to 80 MHz, always within the reported radio capabilities.
On a PHY with child radios, the selected child's default channel determines
whether the 5 GHz radio uses the high-band defaults. Missing child or band
capabilities leave initialization pending. Automatic selection still follows
the regulatory and DFS/CAC requirements. These defaults preserve both radio
and AP disabled flags; the user enables Wi-Fi explicitly.

OpenWRT-CI passes `WRT_SSID` and `WRT_WORD` through this section. It must leave
the wifi-scripts generator's untouched-AP values intact, so custom build
credentials do not suppress channel, bandwidth, country, and security defaults.
Keep this configuration private when using a private Wi-Fi password.

## Configuration migration

`/etc/uci-defaults/90-router-tool-migrate` owns historical settings and snapshot
normalization and the initial generated ULA cleanup. It removes only the unchanged, owned `wan6`/`local` prefix
filter, including its saved routed value when AP mode is active. Custom filters
remain intact. It also normalizes older routed snapshots before regular service
reloads and LuCI saves can use them.

Regular runtime paths do not repair old snapshot formats. They still record new
sections, restore AP settings, preserve manual edits, and resume interrupted
configuration transactions. These operations are part of normal mode switching.
