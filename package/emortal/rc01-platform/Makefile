# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=rc01-platform
PKG_VERSION:=1
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only

include $(INCLUDE_DIR)/package.mk

define Package/rc01-platform
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=RC01 fan supervision, status and upgrade preflight
  DEPENDS:=@TARGET_qualcommbe_ipq95xx +kmod-hwmon-emc2305 +kmod-thermal +uboot-envtools +fdt-utils +ucode +ucode-mod-fs +ucode-mod-uci
  PKGARCH:=all
endef

define Package/rc01-platform/description
 Hardware support for the verified Xiaomi RC01 / BE10000 with Du v2 and
 the merged 120 MiB rootfs layout. Does not rewrite bootloader environment.
endef

define Build/Compile
endef

define Package/rc01-platform/install
	$(INSTALL_DIR) $(1)/etc/init.d $(1)/usr/libexec $(1)/lib/upgrade $(1)/usr/bin $(1)/usr/share/ucode/rc01
	$(INSTALL_BIN) ./files/rc01-fan.init $(1)/etc/init.d/rc01-fan
	$(INSTALL_BIN) ./files/rc01-fan-guard $(1)/usr/libexec/rc01-fan-guard
	$(INSTALL_DATA) ./files/rc01.sh $(1)/lib/upgrade/rc01.sh
	$(INSTALL_DATA) ./files/platform.uc $(1)/usr/share/ucode/rc01/platform.uc
	$(INSTALL_BIN) ./files/rc01-check $(1)/usr/bin/rc01-check
endef

$(eval $(call BuildPackage,rc01-platform))
