// SPDX-License-Identifier: GPL-2.0-only
// Read-only status, with an alternate filesystem root for offline fixtures.
// Do not read bootloader contents, proxy profiles, command lines or logs.
import { readfile, glob, realpath, stat, statvfs } from 'fs';

function text(path) {
	let value = readfile(path);
	return value == null ? null : trim(value);
}

function number(value) {
	return type(value) == 'string' && match(value, /^(0[xX][0-9a-fA-F]+|0|[1-9][0-9]*)$/)
		? int(value, 0) : null;
}

function numeric(path) {
	return number(text(path));
}

function enabled(value) {
	return value == '1' || value == 'true' || value == 'yes' || value == 'on';
}

function executable(path) {
	let file = stat(path);
	return file?.type == 'file' && (file.mode & 73) != 0;
}

function partition(root, wanted) {
	let found = [];
	for (let path in glob(root + '/sys/class/mtd/mtd[0-9]*') || []) {
		if (!match(split(path, '/')[-1], /^mtd[0-9]+$/)) continue;
		if (lc(text(path + '/name') || '') != wanted) continue;
		push(found, {
			device: '/dev/' + split(path, '/')[-1],
			size: numeric(path + '/size'), erase_size: numeric(path + '/erasesize'),
			page_size: numeric(path + '/writesize'),
			bad_blocks: numeric(path + '/bad_blocks'),
			uncorrectable_ecc: numeric(path + '/ecc_failures')
		});
	}
	return length(found) == 1 ? found[0] : null;
}

function environment_map(root, env) {
	let entries = [];
	for (let line in split(readfile(root + '/etc/fw_env.config') || '', '\n')) {
		line = trim(split(line, '#')[0]);
		if (length(line)) push(entries, split(line, /\s+/));
	}
	let fields = length(entries) == 1 ? entries[0] : [];
	return env != null && length(fields) == 5 && fields[0] == env.device &&
		number(fields[1]) == 0 && number(fields[2]) == 65536 &&
		number(fields[3]) == 131072 && number(fields[4]) == 2;
}

export function collect(root, uci) {
	root = root || '';
	let read = (c, s, k) => uci?.get(c, s, k);
	let board = text(root + '/tmp/sysinfo/board_name');
	let temperatures = [], fans = [], ports = [], processes = [], radios = [];
	for (let path in glob(root + '/sys/class/thermal/thermal_zone*') || []) {
		let milli = numeric(path + '/temp');
		push(temperatures, { name: text(path + '/type'),
			celsius: milli == null || milli > 150000 ? null : milli / 1000.0 });
	}
	for (let path in glob(root + '/sys/class/hwmon/hwmon*') || []) {
		if (text(path + '/name') != 'emc2305') continue;
		if (!match(realpath(path + '/device') || '', /\/[0-9]+-002f$/)) continue;
		push(fans, { hwmon: split(path, '/')[-1], rpm: numeric(path + '/fan1_input'),
			pwm: numeric(path + '/pwm1'), fault: numeric(path + '/fan1_fault'),
			fault_available: stat(path + '/fan1_fault') != null });
	}
	for (let name in [ 'lan1', 'lan2', 'lan3', 'lan4', 'sfp', 'wan' ]) {
		let path = root + '/sys/class/net/' + name;
		push(ports, { name, present: stat(path) != null,
			state: text(path + '/operstate'), carrier: numeric(path + '/carrier'),
			speed_mbps: numeric(path + '/speed'), duplex: text(path + '/duplex') });
	}
	for (let path in glob(root + '/proc/[0-9]*/comm') || []) {
		let name = text(path);
		if (index([ 'mihomo', 'daed', 'dae', 'tailscaled', 'rc01-fan-guard' ], name) < 0) continue;
		let rss = match(readfile(replace(path, /comm$/, 'status')) || '', /(^|\n)VmRSS:\s+(\d+) kB/);
		push(processes, { name, pid: int(split(path, '/')[-2]), rss_kib: rss ? int(rss[2]) : null });
	}
	uci?.foreach('wireless', 'wifi-device', s => push(radios, { name: s['.name'], disabled: enabled(s.disabled) }));
	let defaults = [];
	uci?.foreach('firewall', 'defaults', s => {
		push(defaults, { available: true, software: enabled(s.flow_offloading), hardware: enabled(s.flow_offloading_hw) });
	});
	let firewall = length(defaults) == 1 ? defaults[0] : { available: false, software: null, hardware: null };
	let rootfs = partition(root, 'rootfs');
	let env = partition(root, '0:appsblenv');
	let ubi = root + '/sys/class/ubi/ubi0';
	let active = text(root + '/sys/kernel/debug/ppe/active_flows');
	let counts = active == null ? [] : split(active, /\s+/);
	let ppe_flows = null;
	if ((length(counts) == 1 || length(counts) == 3) &&
		!length(filter(counts, v => number(v) == null)))
		ppe_flows = length(counts) == 3 ? { ipv4: number(counts[0]), ipv6: number(counts[1]), total: number(counts[2]) }
			: { total: number(counts[0]) };
	let overlay = statvfs(root + '/overlay');
	return {
		schema: 1, board, supported: board == 'xiaomi,be10000',
		kernel: text(root + '/proc/sys/kernel/osrelease'),
		physical_acceptance: false,
		temperatures, fans, ports, processes, radios,
		storage: { rootfs, environment: env, environment_map_valid: environment_map(root, env),
			environment_crc_checked: false,
			ubi_root_matches: rootfs != null && '/dev/mtd' + numeric(ubi + '/mtd_num') == rootfs.device,
			volumes: map([ 0, 1, 2 ], id => text(root + '/sys/class/ubi/ubi0_' + id + '/name')),
			overlay_available_bytes: overlay ? overlay.bavail * overlay.frsize : null },
		ppe: { present: stat(root + '/sys/module/qcom_ppe') != null, active_flows: ppe_flows,
			direct_switch_status_available: stat(root + '/sys/kernel/debug/ppe/direct_switch') != null },
		proxy: { btf: stat(root + '/sys/kernel/btf/vmlinux') != null,
			nikki_installed: executable(root + '/usr/libexec/mihomo'),
			daed_installed: executable(root + '/usr/bin/daed'),
			dae_installed: executable(root + '/usr/bin/dae'),
			nikki_enabled: enabled(read('nikki', 'config', 'enabled')),
			daed_enabled: enabled(read('daed', 'config', 'enabled')),
			dae_enabled: enabled(read('dae', 'config', 'enabled')),
			automatic_network: read('router-tool', 'main', 'auto_network') != '0', offload: firewall }
	};
}

export function assess(state, target) {
	let checks = [];
	function check(code, level, message) { push(checks, { code, level, message }); }
	check('board', state.supported ? 'ok' : 'error', 'Requires Xiaomi RC01 / BE10000');
	let storage = state.storage;
	let geometry = storage.rootfs?.size == 125829120 && storage.rootfs?.erase_size == 131072 &&
		storage.rootfs?.page_size == 2048 && storage.environment?.size == 524288 &&
		storage.environment?.erase_size == 131072;
	check('layout', geometry ? 'ok' : 'error', 'Requires the verified 120 MiB NAND layout');
	check('environment-map', storage.environment_map_valid ? 'ok' : 'error', 'Requires the 64 KiB environment map; CRC is checked separately during upgrade');
	check('ubi', storage.ubi_root_matches && join(' ', storage.volumes) == 'kernel rootfs rootfs_data' ? 'ok' : 'error',
		'Requires kernel/rootfs/rootfs_data at UBI0 IDs 0/1/2');
	let ecc = storage.rootfs?.uncorrectable_ecc;
	check('nand-ecc', ecc == null ? 'warn' : ecc > 0 ? 'error' : 'ok', 'NAND uncorrectable ECC counter');
	let fan = length(state.fans) == 1 ? state.fans[0] : null;
	check('fan', fan != null && fan.rpm > 0 && fan.pwm != null && fan.pwm >= 77 && fan.pwm <= 255 &&
		(!fan.fault_available || fan.fault === 0) ? 'ok' : 'error', 'Requires one healthy EMC2301 at address 0x2f');
	let sensors = filter(state.temperatures, s => index([ 'nss-top-thermal', 'nss_top_thermal' ], s.name) >= 0);
	let temperature = length(sensors) == 1 ? sensors[0].celsius : null;
	check('temperature', temperature == null || temperature < 1 || temperature >= 75 ? 'error' : temperature >= 60 ? 'warn' : 'ok',
		'Requires a valid NSS temperature below the 75 C guard threshold');
	check('ports', !length(filter(state.ports, p => !p.present)) ? 'ok' : 'error', 'Requires lan1..lan4, sfp and wan net devices; cables may be disconnected');
	check('ppe', state.ppe.present ? 'ok' : 'warn', 'Native PPE module presence; zero or unavailable counters do not prove a fault');
	let active = name => length(filter(state.processes, p => p.name == name)) > 0;
	let proxies = [ 'nikki', 'dae', 'daed' ];
	let running = { nikki: active('mihomo'), dae: active('dae'), daed: active('daed') };
	check('proxy-conflict', length(filter(proxies, name => running[name] || state.proxy[name + '_enabled'])) > 1 ? 'error' : 'ok',
		'Run only one transparent proxy at a time');
	if (index(proxies, target) >= 0) {
		check('proxy-installed', state.proxy[target + '_installed'] ? 'ok' : 'error', 'Requested proxy core is installed');
		check('other-proxy', length(filter(proxies, name => name != target && (running[name] || state.proxy[name + '_enabled']))) ? 'error' : 'ok',
			'Stop and disable the other proxy before switching');
		check('network-owner', state.proxy.automatic_network ? 'warn' : 'ok', 'Bypass networking should remain manually managed');
		let offload = state.proxy.offload;
		check('offload-baseline', !offload.available || offload.software || offload.hardware ? 'warn' : 'ok',
			'Validate the proxy with flow offload disabled before testing acceleration coexistence');
		if (target != 'nikki') check('btf', state.proxy.btf ? 'ok' : 'error', 'dae/daed requires runtime kernel BTF');
		check('traffic-acceptance', 'info', 'Read-only preflight does not test DNS, routing, TCP/UDP, IPv6 or leak protection');
	}
	return { target, passed: !length(filter(checks, c => c.level == 'error')), checks };
}

export function snapshot(uci) {
	let state = collect('', uci);
	state.preflight = map([ 'hardware', 'nikki', 'dae', 'daed' ], target => assess(state, target));
	return state;
}
