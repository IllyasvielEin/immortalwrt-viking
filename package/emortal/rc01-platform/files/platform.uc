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

function ppe_counts(value) {
	let fields = value == null ? [] : split(value, /\s+/);
	if (length(fields) == 1 && number(fields[0]) != null)
		return { total: number(fields[0]) };
	if (length(fields) != 3 || number(fields[0]) == null || number(fields[1]) == null)
		return null;
	let usage = split(fields[2], '/');
	let total = number(usage[0]);
	if (total == null || length(usage) > 2) return null;
	let result = { ipv4: number(fields[0]), ipv6: number(fields[1]), total };
	if (length(usage) == 2) {
		let capacity = number(usage[1]);
		if (capacity == null || capacity == 0 || total > capacity) return null;
		result.capacity = capacity;
	}
	return result;
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
			offset: numeric(path + '/offset'),
			size: numeric(path + '/size'), erase_size: numeric(path + '/erasesize'),
			page_size: numeric(path + '/writesize'),
			bad_blocks: numeric(path + '/bad_blocks'),
			uncorrectable_ecc: numeric(path + '/ecc_failures')
		});
	}
	return length(found) == 1 ? found[0] : null;
}

function environment_map(root, env, contract) {
	let entries = [];
	for (let line in split(readfile(root + '/etc/fw_env.config') || '', '\n')) {
		line = trim(split(line, '#')[0]);
		if (length(line)) push(entries, split(line, /\s+/));
	}
	let fields = length(entries) == 1 ? entries[0] : [];
	return env != null && contract != null && length(fields) == 5 && fields[0] == env.device &&
		number(fields[1]) == 0 && number(fields[2]) == contract.environment?.bytes &&
		number(fields[3]) == contract.environment?.erase_bytes && number(fields[4]) == contract.environment?.range_blocks;
}

function layout_contract(root) {
	try {
		let value = json(readfile(root + '/usr/share/rc01/layout.json') || 'null');
		return value?.schema == 1 && value?.layout_id == 'rc01-identity-preserved-r1' ? value : null;
	} catch (error) { return null; }
}

function layout_matches(root, contract) {
	if (type(contract?.partitions) != 'object' || length(keys(contract.partitions)) != 26) return false;
	let devices = filter(glob(root + '/sys/class/mtd/mtd[0-9]*') || [], path => match(split(path, '/')[-1], /^mtd[0-9]+$/));
	if (length(devices) != 26) return false;
	for (let key, wanted in contract.partitions) {
		let actual = partition(root, lc(wanted.name));
		if (actual == null || actual.offset !== wanted.offset || actual.size !== wanted.bytes ||
			actual.erase_size !== contract.nand?.erase_bytes || actual.page_size !== contract.nand?.page_bytes) return false;
	}
	return true;
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
	let license = partition(root, '0:license');
	let bdata = partition(root, 'bdata');
	let contract = layout_contract(root);
	let ubi = root + '/sys/class/ubi/ubi0';
	let ppe_flows = ppe_counts(text(root + '/sys/kernel/debug/ppe/active_flows'));
	let overlay = statvfs(root + '/overlay');
	return {
		schema: 1, board, supported: board == 'xiaomi,be10000',
		kernel: text(root + '/proc/sys/kernel/osrelease'),
		physical_acceptance: false,
		temperatures, fans, ports, processes, radios,
		storage: { rootfs, environment: env, license, bdata,
			layout_id: contract?.layout_id, layout_matches: layout_matches(root, contract),
			bad_block_reserve: contract?.nand?.bad_block_reserve_pebs,
			environment_map_valid: environment_map(root, env, contract),
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
	check('layout', storage.layout_matches === true ? 'ok' : 'error', 'Requires the identity-preserved NAND layout');
	let identities = [ storage.license, storage.bdata ];
	check('identity-storage', !length(filter(identities, part => part == null || part.bad_blocks !== 0 ||
		part.uncorrectable_ecc !== 0)) ? 'ok' : 'error', 'Identity partitions require readable ECC and no bad blocks');
	let bad = storage.rootfs?.bad_blocks;
	check('nand-reserve', bad == null || storage.bad_block_reserve == null || bad > storage.bad_block_reserve ? 'error' : 'ok',
		'Rootfs bad blocks must fit the reserved capacity');
	check('environment-map', storage.environment_map_valid ? 'ok' : 'error', 'Requires the 64 KiB environment map; CRC is checked separately during upgrade');
	check('ubi', storage.ubi_root_matches && join(' ', storage.volumes) == 'kernel rootfs rootfs_data' ? 'ok' : 'error',
		'Requires kernel/rootfs/rootfs_data at UBI0 IDs 0/1/2');
	let ecc = storage.rootfs?.uncorrectable_ecc;
	check('nand-ecc', ecc === 0 ? 'ok' : 'error', 'NAND uncorrectable ECC counter must be readable and zero');
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
