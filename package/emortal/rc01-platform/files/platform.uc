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

function ppe_state(root, now) {
	let base = root + '/sys/kernel/debug/ppe';
	let present = stat(root + '/sys/module/qcom_ppe') != null;
	let debugfs = length(lsdir(root + '/sys/kernel/debug') || []) > 0;
	let flows = bounded(base + '/active_flows', 256);
	let state = {
		present,
		debugfs_accessible: debugfs,
		sampled_at: now,
		active_flows: flows.status == 'ok' ? ppe_counts(trim(flows.data)) : null,
		active_flows_source: { source: 'debugfs:ppe/active_flows',
			status: flows.status != 'ok' ? flows.status : ppe_counts(trim(flows.data)) == null ? 'malformed' : 'ok' },
		resources: ppe_resources(base + '/resource_stats'),
		direct_switch: ppe_direct_switch(base + '/direct_switch'),
		direct_switch_status_available: stat(base + '/direct_switch') != null
	};
	// These single_open producers scan complete hardware tables before a
	// userspace read limit takes effect. Ordinary status calls never open them.
	state.detail = {
		flows: { source: 'debugfs:ppe/flows', status: 'not-collected' },
		reasons: { source: 'debugfs:ppe/reason_counters', status: 'not-collected' }
	};
	return state;
}

// Fan readings follow the guard's own validity rules: a valid NSS sensor
// between 1 and 130 C, PWM at or above the 77 floor, nonzero tachometer and
// a zero fault flag when exported. Anything else is reported, never "ok".
const FAN_MIN_PWM = 77;

function fan_health(fan) {
	if (fan.rpm == null || fan.pwm == null) return 'unreadable';
	if (fan.fault_available && fan.fault == null) return 'unreadable';
	if (fan.fault_available && fan.fault != 0) return 'fault';
	if (fan.rpm == 0) return 'stopped';
	if (fan.pwm < FAN_MIN_PWM || fan.pwm > 255) return 'pwm-out-of-range';
	return fan.fault_available ? 'ok' : 'ok-no-fault-flag';
}

function nss_temperature(temperatures) {
	let sensors = filter(temperatures, s => index([ 'nss-top-thermal', 'nss_top_thermal' ], s.name) >= 0);
	if (length(sensors) != 1) return { status: length(sensors) ? 'ambiguous' : 'missing', celsius: null };
	let value = sensors[0].celsius;
	return { status: value == null ? 'invalid' : value < 1 || value > 130 ? 'out-of-range' : 'ok', celsius: value };
}

// A short trend kept in the rpcd plugin cache. It samples only while the
// status call is polled, at most once per interval, keeps a bounded window
// and marks gaps instead of drawing through missing time.
const FAN_TREND_INTERVAL = 10;
const FAN_TREND_SAMPLES = 90;

function fan_trend(cache, now, sample) {
	let trend = cache?.fan_trend;
	if (trend == null || now == null || (length(trend) && now < trend[-1].t))
		trend = [];
	if (now != null && (!length(trend) || now - trend[-1].t >= FAN_TREND_INTERVAL))
		push(trend, { t: now, ...sample });
	let horizon = FAN_TREND_INTERVAL * FAN_TREND_SAMPLES;
	trend = filter(trend, x => now - x.t < horizon);
	if (length(trend) > FAN_TREND_SAMPLES)
		trend = slice(trend, length(trend) - FAN_TREND_SAMPLES);
	if (cache != null && now != null) cache.fan_trend = trend;
	let samples = [], previous = null;
	for (let x in trend) {
		push(samples, { ...x, age: now - x.t, gap_before: previous != null && x.t - previous > 2 * FAN_TREND_INTERVAL });
		previous = x.t;
	}
	return { interval: FAN_TREND_INTERVAL, capacity: FAN_TREND_SAMPLES, window: horizon,
		source: 'rpcd status polling', samples };
}

function enabled(value) {
	return value == '1' || value == 'true' || value == 'yes' || value == 'on';
}

// Band/primary-channel hints compare the configured band and channel with
// the frequencies the kernel reports for the matching phy. Channel numbers
// repeat across bands, so the channel is first converted to a frequency with
// its configured band (as cfg80211 does). Only the primary 20 MHz channel is
// checked: not width, country, DFS/CAC or whether the radio transmits.
// Hints only inform; nothing here writes configuration or affects startup.
function channel_frequency(band, channel) {
	if (!match(channel, /^[0-9]{1,3}$/)) return null;
	let ch = int(channel);
	if (band == '2g') return ch == 14 ? 2484 : ch >= 1 && ch <= 13 ? 2407 + 5 * ch : null;
	if (band == '5g') return ch >= 182 && ch <= 196 ? 4000 + 5 * ch : ch >= 1 && ch <= 177 ? 5000 + 5 * ch : null;
	if (band == '6g') return ch == 2 ? 5935 : ch >= 1 && ch <= 233 ? 5950 + 5 * ch : null;
	if (band == '60g') return ch >= 1 && ch <= 6 ? 56160 + 2160 * ch : null;
	return null;
}

// Same band boundaries as wifi-detect.
function frequency_band(freq) {
	return freq > 50000 ? '60g' : freq > 5900 ? '6g' : freq > 4000 ? '5g' : freq > 2000 ? '2g' : null;
}

// nl80211 GET_WIPHY dump -> [ { name, bands: { band: [ { freq, disabled, no_ir, radar } ] } } ]
function wiphy_capabilities(dump) {
	let phys = [];
	for (let phy in dump) {
		if (type(phy?.wiphy_name) != 'string') continue;
		let bands = {};
		for (let band in phy.wiphy_bands || [])
			for (let f in band?.freqs || []) {
				let name = frequency_band(f?.freq);
				if (name == null) continue;
				bands[name] ??= [];
				push(bands[name], { freq: f.freq, disabled: !!f.disabled, no_ir: !!f.no_ir, radar: !!f.radar });
			}
		push(phys, { name: phy.wiphy_name, bands });
	}
	return phys;
}

// Read-only version of wifi-scripts find_phy(): device path (with +N for
// the Nth phy below one device), then MAC address, then phy name. It never
// renames a phy.
function radio_phy(root, device, names) {
	let sys = n => root + '/sys/class/ieee80211/' + n;
	if (type(device.path) == 'string' && length(device.path)) {
		let parts = split(device.path, '+');
		let found = filter(names, n => substr(realpath(sys(n) + '/device') || '', -length(parts[0])) == parts[0]);
		found = sort(found, (a, b) => numeric(sys(a) + '/index') - numeric(sys(b) + '/index'));
		if (found[int(parts[1] || '0')] != null) return found[int(parts[1] || '0')];
	}
	if (type(device.macaddr) == 'string') {
		let found = filter(names, n => text(sys(n) + '/macaddress') == lc(device.macaddr))[0];
		if (found != null) return found;
	}
	return index(names, device.phy) >= 0 ? device.phy : null;
}

function channel_hint(root, device, phys) {
	let hint = { radio: device.name, band: device.band, channel: device.channel,
		configured_disabled: device.disabled, phy: null, frequency: null, flags: [] };
	let automatic = device.channel == null || device.channel == '' || device.channel == 'auto' || device.channel == '0';
	if (device.band == null) { hint.status = 'band-missing'; return hint; }
	if (index([ '2g', '5g', '6g', '60g' ], device.band) < 0) { hint.status = 'band-invalid'; return hint; }
	if (!automatic) {
		hint.frequency = channel_frequency(device.band, device.channel);
		if (hint.frequency == null) { hint.status = 'channel-invalid'; return hint; }
	}
	if (phys == null) { hint.status = 'capability-unavailable'; return hint; }
	hint.phy = radio_phy(root, device, map(phys, p => p.name));
	let phy = filter(phys, p => p.name == hint.phy)[0];
	if (phy == null) { hint.status = 'not-ready'; return hint; }
	let freqs = phy.bands[device.band];
	if (!length(freqs)) { hint.status = 'band-unsupported'; hint.supported_bands = sort(keys(phy.bands)); return hint; }
	if (automatic) {
		hint.status = length(filter(freqs, f => !f.disabled)) ? 'auto' : 'band-disabled';
		return hint;
	}
	let entry = filter(freqs, f => f.freq == hint.frequency)[0];
	if (entry == null) { hint.status = 'channel-unsupported'; return hint; }
	for (let flag in [ 'radar', 'no_ir' ]) if (entry[flag]) push(hint.flags, flag);
	hint.status = entry.disabled ? 'channel-disabled' : 'ok';
	return hint;
}

// Queried on each status call and never cached, so a failed query cannot
// leave earlier capabilities on display. `query` lets tests inject a dump.
function wireless_state(root, uci, now, query) {
	let devices = [];
	uci?.foreach('wireless', 'wifi-device', s => push(devices, { name: s['.name'], band: s.band, channel: s.channel,
		disabled: enabled(s.disabled), path: s.path, phy: s.phy, macaddr: s.macaddr }));
	let status = 'ok', dump = null;
	if (query == null) {
		let nl = null;
		try { nl = require('nl80211'); } catch (e) { }
		if (nl == null) status = 'module-missing';
		else dump = nl.request(nl.const.NL80211_CMD_GET_WIPHY, nl.const.NLM_F_DUMP, { split_wiphy_dump: true });
	}
	else dump = query();
	if (status == 'ok' && type(dump) != 'array') status = 'unavailable';
	let phys = status == 'ok' ? wiphy_capabilities(dump) : null;
	return { source: 'nl80211 GET_WIPHY', status, sampled_at: now,
		phys: phys == null ? null : map(phys, p => ({ name: p.name, bands: sort(keys(p.bands)) })),
		hints: map(devices, d => channel_hint(root, d, phys)) };
}

// SFP cage state as the kernel sfp driver sees it. The net device existing
// says nothing about a module: presence is the driver's moddef0 bit from its
// debugfs state file. RC01 wires no MOD_DEF0 GPIO, so the kernel infers
// presence from an I2C read of the module EEPROM; repeated read failures also
// clear it, so "not detected" cannot prove the cage is physically empty.
// No EEPROM, identity or DDM data is read here.
const SFP_DRIVER_ENTRIES = [ 'bind', 'unbind', 'uevent', 'module', 'new_id', 'remove_id' ];

function sfp_presence(root) {
	let cages = filter(lsdir(root + '/sys/bus/platform/drivers/sfp') || [], n => index(SFP_DRIVER_ENTRIES, n) < 0);
	let result = { source: 'debugfs:<cage>/state', method: 'i2c-inferred', cage: null, detected: null, module_state: null };
	if (length(cages) != 1) {
		result.status = length(cages) ? 'ambiguous' : 'driver-missing';
		return result;
	}
	result.cage = cages[0];
	result.source = 'debugfs:' + cages[0] + '/state';
	let read = bounded(root + '/sys/kernel/debug/' + cages[0] + '/state', 4096);
	result.status = read.status;
	if (read.status != 'ok') return result;
	let moddef0 = match(read.data, /(^|\n)moddef0: ([01])\n/);
	if (!moddef0) { result.status = 'malformed'; return result; }
	result.detected = moddef0[2] == '1';
	result.module_state = match(read.data, /(^|\n)Module state: ([a-z]+)\n/)?.[2];
	return result;
}

function sfp_state(root, uci, now, port, processes) {
	let presence = sfp_presence(root);
	let carrier = port.present ? (port.carrier == 0 || port.carrier == 1 ? port.carrier : null) : null;
	let status;
	if (!port.present) status = 'interface-missing';
	else if (presence.status != 'ok') status = 'unknown';
	else if (carrier == 1) status = 'link';
	else if (!presence.detected) status = 'not-detected';
	else status = carrier == null ? 'detected-link-unknown' : 'detected-no-link';
	let controller = filter(processes, p => p.name == 'rc01-sfp-led');
	return { interface: port.name, interface_present: port.present, sampled_at: now, status,
		carrier, carrier_status: !port.present ? 'interface-missing' : carrier == null ? 'unreadable' : 'ok',
		operstate: port.state, presence,
		led: { presence_mode: enabled(uci?.get('system', 'led_sfp', 'rc01_presence')),
			controller: length(controller) == 1 ? 'running' : length(controller) ? 'multiple' : 'absent' } };
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

export function collect(root, uci, cache, wiphy_query) {
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
		if (index([ 'mihomo', 'daed', 'dae', 'tailscaled', 'rc01-fan-guard', 'rc01-sfp-led' ], name) < 0) continue;
		let status = readfile(replace(path, /comm$/, 'status')) || '';
		let rss = match(status, /(^|\n)VmRSS:\s+(\d+) kB/), ppid = match(status, /(^|\n)PPid:\s+(\d+)/);
		push(processes, { name, pid: int(split(path, '/')[-2]), ppid: ppid ? int(ppid[2]) : null, rss_kib: rss ? int(rss[2]) : null });
	}
	// The shell services fork short-lived subshells that keep their name;
	// these are not separate instances.
	for (let service in [ 'rc01-fan-guard', 'rc01-sfp-led' ]) {
		let pids = map(filter(processes, p => p.name == service), p => p.pid);
		processes = filter(processes, p => p.name != service || index(pids, p.ppid) < 0);
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
	for (let fan in fans) fan.health = fan_health(fan);
	let nss = nss_temperature(temperatures);
	let fan = length(fans) == 1 ? fans[0] : null;
	let guard = filter(processes, p => p.name == 'rc01-fan-guard');
	let fan_state = {
		controller: length(fans) == 1 ? 'found' : length(fans) ? 'ambiguous' : 'missing',
		temperature: nss,
		// Process presence only: it does not show that protection is working.
		guard_process: length(guard) == 1 ? 'running' : length(guard) ? 'multiple' : 'absent',
		trend: fan_trend(cache, now, { celsius: nss.celsius, rpm: fan?.rpm, pwm: fan?.pwm, health: fan?.health })
	};
	return {
		schema: 1, board, supported: board == 'xiaomi,be10000',
		kernel: text(root + '/proc/sys/kernel/osrelease'),
		sampled_at: { uptime: now, unix: time() },
		physical_acceptance: false,
		temperatures, fans, fan: fan_state, ports, processes, radios,
		storage: { rootfs, environment: env, license, bdata,
			layout_id: contract?.layout_id, layout_matches: layout_matches(root, contract),
			bad_block_reserve: contract?.nand?.bad_block_reserve_pebs,
			environment_map_valid: environment_map(root, env, contract),
			environment_crc_checked: false,
			ubi_root_matches: rootfs != null && '/dev/mtd' + numeric(ubi + '/mtd_num') == rootfs.device,
			volumes: map([ 0, 1, 2 ], id => text(root + '/sys/class/ubi/ubi0_' + id + '/name')),
			overlay_available_bytes: overlay ? overlay.bavail * overlay.frsize : null },
		ppe: ppe_state(root, now),
		wireless: wireless_state(root, uci, now, wiphy_query),
		sfp: sfp_state(root, uci, now, filter(ports, p => p.name == 'sfp')[0], processes),
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
