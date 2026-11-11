// SPDX-License-Identifier: GPL-2.0-only
// Read-only status, with an alternate filesystem root for offline fixtures.
// Do not read bootloader contents, proxy profiles, command lines or logs.
import { readfile, glob, realpath, stat, statvfs, open, lsdir } from 'fs';

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

// Monotonic seconds since boot, used for sample ages. Wall-clock time can
// step (NTP) and therefore never decides freshness.
function uptime(root) {
	let fields = split(text(root + '/proc/uptime') || '', /\s+/);
	return match(fields[0] || '', /^[0-9]+(\.[0-9]+)?$/) ? +fields[0] : null;
}

// Read at most `limit` bytes. Distinguishes absent sources from sources that
// exist but cannot be read, and flags output longer than the bound.
function bounded(path, limit) {
	if (stat(path) == null)
		return { status: 'missing' };
	let handle = open(path, 'r');
	if (!handle)
		return { status: 'unreadable' };
	let data = handle.read(limit + 1);
	handle.close();
	if (data == null)
		return { status: 'unreadable' };
	if (length(data) > limit)
		return { status: 'truncated', data: substr(data, 0, limit) };
	return { status: 'ok', data };
}

// Kernel counters are kept as decimal strings: 64-bit values exceed the
// exact integer range of JSON consumers, and conversion must not round.
const COUNTER = /^-?(0|[1-9][0-9]*)$/;

// Parse "group key:value key:value group key:value" lines. A bare word opens
// a group; values must be decimal counters, ratios or simple identifiers.
function token_groups(line) {
	let groups = [], current = null;
	for (let token in split(trim(line), /\s+/)) {
		let pair = match(token, /^([a-z0-9_]+):(.+)$/);
		if (pair) {
			if (current == null || exists(current.values, pair[1])) return null;
			let value = pair[2];
			if (!match(value, COUNTER) && !match(value, /^[0-9]+\/[0-9]+$/) && !match(value, /^[a-z][a-z0-9_]*$/))
				return null;
			current.values[pair[1]] = value;
		}
		else if (match(token, /^[a-z][a-z0-9_]*$/)) {
			current = { name: token, values: {} };
			push(groups, current);
		}
		else return null;
	}
	return length(groups) ? groups : null;
}

// Every value must be a counter, except the named identifier or ratio keys.
function counters_only(values, identifiers, ratios) {
	for (let key, value in values) {
		if (index(identifiers || [], key) >= 0 ? !match(value, /^[a-z][a-z0-9_]*$/) :
			index(ratios || [], key) >= 0 ? !match(value, /^[0-9]+\/[0-9]+$/) : !match(value, COUNTER))
			return false;
	}
	return true;
}

function source_record(source, read) {
	return { source, status: read.status };
}

function ppe_resources(path) {
	let read = bounded(path, 8192), result = source_record('debugfs:ppe/resource_stats', read);
	if (read.status != 'ok') return result;
	let failures = {}, groups = {}, ignored = 0;
	for (let line in split(read.data, '\n')) {
		if (!length(trim(line))) continue;
		let parsed = token_groups(line);
		if (parsed == null) { result.status = 'malformed'; return result; }
		for (let group in parsed) {
			if (!counters_only(group.values, group.name == 'failure' ? [ 'stage' ] : [], group.name == 'flow' ? [ 'active' ] : [])) {
				result.status = 'malformed';
				return result;
			}
			if (group.name == 'failure') {
				let stage = group.values.stage;
				if (stage == null || exists(failures, stage)) { result.status = 'malformed'; return result; }
				delete group.values.stage;
				failures[stage] = group.values;
			}
			else if (index([ 'host', 'flow', 'nexthop' ], group.name) >= 0 && !exists(groups, group.name))
				groups[group.name] = group.values;
			else
				ignored++;
		}
	}
	if (!length(groups)) { result.status = 'malformed'; return result; }
	let flow = groups.flow;
	if (flow?.active != null) {
		let pair = split(flow.active, '/');
		if (length(pair) != 2) { result.status = 'malformed'; return result; }
		flow.ipv4_active = pair[0];
		flow.ipv6_active = pair[1];
		delete flow.active;
	}
	return { ...result, host: groups.host, flow, nexthop: groups.nexthop, failures, ignored_groups: ignored };
}

function ppe_direct_switch(path) {
	let read = bounded(path, 8192), result = source_record('debugfs:ppe/direct_switch', read);
	if (read.status != 'ok') return result;
	let nodes = [];
	for (let line in split(read.data, '\n')) {
		if (!length(trim(line))) continue;
		let unregistered = match(line, /^node:([0-9]+) unregistered$/);
		if (unregistered) { push(nodes, { node: int(unregistered[1]), registered: false }); continue; }
		let parsed = token_groups('node ' + line);
		let values = length(parsed) == 1 ? parsed[0].values : null;
		if (values == null || !counters_only(values) || !match(values.node || '', /^[0-9]+$/) ||
			!match(values.running || '', /^[01]$/) || !match(values.faulted || '', /^[01]$/)) {
			result.status = 'malformed';
			return result;
		}
		let node = { node: int(values.node), registered: true,
			running: values.running == '1', faulted: values.faulted == '1', counters: {} };
		for (let key, value in values)
			if (index([ 'node', 'running', 'faulted' ], key) < 0) node.counters[key] = value;
		push(nodes, node);
	}
	if (!length(nodes)) result.status = 'malformed';
	else result.nodes = nodes;
	return result;
}

// The flows view prints named header lines (counters, topology, reject
// stages, rule refreshes) followed by one line per installed flow, which
// starts with an address. Only the header is parsed; the bound caps cost.
const PPE_FLOW_HEADER_GROUPS = [ 'topology', 'reject', 'rule' ];

function ppe_flow_events(path) {
	let read = bounded(path, 4096), result = source_record('debugfs:ppe/flows', read);
	if (read.status != 'ok' && read.status != 'truncated') return result;
	let lines = split(read.data, '\n'), groups = {}, ended = false;
	// A truncated read ends in a partial line, which is never trusted.
	if (read.status == 'truncated') pop(lines);
	else if (lines[-1] == '') pop(lines);
	if (!length(lines)) { result.status = read.status == 'truncated' ? 'truncated' : 'malformed'; return result; }
	let events = token_groups('events ' + lines[0]);
	if (length(events) != 1 || events[0].values.bound == null || !counters_only(events[0].values)) {
		result.status = 'malformed';
		return result;
	}
	for (let i = 1; i < length(lines); i++) {
		if (!match(lines[i], /^[a-z]/)) { ended = true; break; }
		let parsed = token_groups(lines[i]);
		if (length(parsed) != 1 || exists(groups, parsed[0].name) || !counters_only(parsed[0].values)) {
			result.status = 'malformed';
			return result;
		}
		groups[parsed[0].name] = parsed[0].values;
	}
	if (!ended && read.status == 'truncated') { result.status = 'truncated'; return result; }
	if (groups.reject == null) { result.status = 'malformed'; return result; }
	result.status = 'ok';
	result.events = events[0].values;
	result.rejects = groups.reject;
	result.topology = groups.topology;
	result.rule = groups.rule;
	result.ignored_groups = length(filter(keys(groups), k => index(PPE_FLOW_HEADER_GROUPS, k) < 0));
	return result;
}

function ppe_reasons(path) {
	let read = bounded(path, 131072), result = source_record('debugfs:ppe/reason_counters', read);
	if (read.status != 'ok') return result;
	let rows = [], failed = 0, total = 0;
	for (let line in split(read.data, '\n')) {
		if (!length(trim(line))) continue;
		let row = match(line, /^(cpu|drop) code:([0-9]+)( port:(-?[0-9]+) index:([0-9]+))? read:(-?[0-9]+) packets:([0-9]+) bytes:([0-9]+) name:(.{1,64})$/);
		if (!row || (row[1] == 'drop') != (row[3] != null)) { result.status = 'malformed'; return result; }
		total++;
		if (row[6] != '0') { failed++; continue; }
		if (row[7] == '0') continue;
		push(rows, { kind: row[1], code: int(row[2]), port: row[4] == null ? null : int(row[4]),
			packets: row[7], bytes: row[8], name: row[9] });
	}
	// Keep the most active rows; compare decimal strings without rounding.
	sort(rows, (a, b) => length(b.packets) - length(a.packets) || (b.packets > a.packets) - (b.packets < a.packets));
	result.rows = slice(rows, 0, 32);
	result.nonzero_rows = length(rows);
	result.listed_rows = total;
	result.read_failures = failed;
	return result;
}

// Flows and reason counters walk the flow table or hardware counter rows,
// so they are sampled at most once per interval and returned with their age.
const PPE_DETAIL_INTERVAL = 60;

function ppe_state(root, now, cache) {
	let base = root + '/sys/kernel/debug/ppe';
	let present = stat(root + '/sys/module/qcom_ppe') != null;
	let debugfs = length(lsdir(root + '/sys/kernel/debug') || []) > 0;
	let flows = bounded(base + '/active_flows', 256);
	let state = {
		present,
		debugfs_mounted: debugfs,
		sampled_at: now,
		active_flows: flows.status == 'ok' ? ppe_counts(trim(flows.data)) : null,
		active_flows_source: { source: 'debugfs:ppe/active_flows',
			status: flows.status != 'ok' ? flows.status : ppe_counts(trim(flows.data)) == null ? 'malformed' : 'ok' },
		resources: ppe_resources(base + '/resource_stats'),
		direct_switch: ppe_direct_switch(base + '/direct_switch'),
		direct_switch_status_available: stat(base + '/direct_switch') != null
	};
	let detail = cache?.ppe_detail;
	if (detail == null || now == null || detail.sampled_at == null || now - detail.sampled_at >= PPE_DETAIL_INTERVAL ||
		now < detail.sampled_at) {
		detail = { sampled_at: now, flows: ppe_flow_events(base + '/flows'), reasons: ppe_reasons(base + '/reason_counters') };
		if (cache != null && now != null) cache.ppe_detail = detail;
	}
	state.detail = { ...detail, interval: PPE_DETAIL_INTERVAL,
		age: now == null || detail.sampled_at == null ? null : now - detail.sampled_at };
	return state;
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

export function collect(root, uci, cache) {
	root = root || '';
	let now = uptime(root);
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
	let overlay = statvfs(root + '/overlay');
	return {
		schema: 1, board, supported: board == 'xiaomi,be10000',
		kernel: text(root + '/proc/sys/kernel/osrelease'),
		sampled_at: { uptime: now, unix: time() },
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
		ppe: ppe_state(root, now, cache),
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

export function snapshot(uci, cache) {
	let state = collect('', uci, cache);
	state.preflight = map([ 'hardware', 'nikki', 'dae', 'daed' ], target => assess(state, target));
	return state;
}
