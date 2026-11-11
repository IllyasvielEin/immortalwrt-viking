# SPDX-License-Identifier: GPL-2.0-or-later
#
# Effects and settings adapted from be10000-fan-rgb by zh8416,
# https://github.com/zh8416/immortalwrt commit
# 7c3057979a9e1767fed5e12401ae7d1948223f16 (GPL-2.0-or-later).
# Only the RGB part is used; the fan daemon is not.
include $(TOPDIR)/rules.mk

PKG_NAME:=rc01-rgb
PKG_VERSION:=1
PKG_RELEASE:=2
PKG_LICENSE:=GPL-2.0-or-later

include $(INCLUDE_DIR)/package.mk

define Package/rc01-rgb
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=RC01 RGB light bar service
  DEPENDS:=@TARGET_qualcommbe_ipq95xx +ucode +ucode-mod-fs +ucode-mod-uci
  PKGARCH:=all
endef

define Package/rc01-rgb/description
 Colour, brightness, effects and on/off/night schedule for the Xiaomi RC01
 (BE10000) top RGB bar (pwm-leds red/green/blue:ambient). Disabled by
 default. Does not control the fan or any other LED.
endef

define Package/rc01-rgb/conffiles
/etc/config/rc01_rgb
endef

define Build/Compile
endef

define Package/rc01-rgb/install
	$(INSTALL_DIR) $(1)/etc/config $(1)/etc/init.d $(1)/usr/libexec $(1)/usr/share/ucode/rc01
	$(INSTALL_CONF) ./files/rc01_rgb.config $(1)/etc/config/rc01_rgb
	$(INSTALL_BIN) ./files/rc01-rgb.init $(1)/etc/init.d/rc01-rgb
	$(INSTALL_BIN) ./files/rc01-rgb $(1)/usr/libexec/rc01-rgb
	$(INSTALL_DATA) ./files/rgb.uc $(1)/usr/share/ucode/rc01/rgb.uc
endef

$(eval $(call BuildPackage,rc01-rgb))
