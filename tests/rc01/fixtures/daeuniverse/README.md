Pristine upstream files of daeuniverse/openwrt-packages at commit
`e104a4930c5fc4f625f3355257626495685424f5` (the pin in `config/rc01/feeds.conf`),
used as preimages for the honk controlled patch and the first-boot contract
tests. They are not built or installed; keep them byte-identical to upstream
and update them together with the feed pin.

- `honk/Makefile`: `git show e104a49:honk/Makefile`
- `honk/files/honk.uci-defaults`: installed upstream as `/etc/uci-defaults/90-honk-autostart`

Both are GPL-3.0 licensed by their authors.
