# SPDX-License-Identifier: GPL-2.0-only
# Resolve the same compiler selections as the firmware build, without compiling.
TOPDIR:=$(CURDIR)
include $(TOPDIR)/rules.mk
ifneq ($(CONFIG_USE_LLVM_HOST)$(CONFIG_USE_LLVM_PREBUILT)$(CONFIG_USE_LLVM_BUILD),)
include $(INCLUDE_DIR)/bpf.mk
endif
.PHONY: rc01-ci-toolchain
rc01-ci-toolchain:
	@printf '%s\n' 'host_cc=$(HOSTCC_NOCACHE)' "target_cc=$$(command -v '$(TARGET_CC_NOCACHE)' || true)" 'bpf_clang=$(CLANG)' 'bpf_llvm_strip=$(LLVM_STRIP)'
