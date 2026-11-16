// SPDX-License-Identifier: GPL-2.0-only
'use strict';
'require view';
'require rpc';
'require poll';
'require dom';

// Only what no stock LuCI page shows: preflight, fan protection, PPE and SFP
// module state. Ports, storage, wireless and processes are on Status pages.
const callStatus = rpc.declare({ object: 'rc01', method: 'status', expect: { '': {} } });

function display(value) {
	return value == null ? _('Unknown') : String(value);
}

// Kernel counters arrive as decimal strings so 64-bit values keep every digit.
function counter(value) {
	if (typeof value === 'string' && /^-?[0-9]+$/.test(value))
		return value.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
	return value == null ? _('Unavailable') : String(value);
}

function seconds(value) {
	return value == null ? _('Unknown') : `${Number(value).toFixed(0)} s`;
}

function described(table, key) {
	return key == null ? _('Unknown') : table[key] ? table[key]() : String(key);
}

const SOURCE_STATUS = {
	ok: () => _('Available'),
	missing: () => _('Not exported by this kernel'),
	unreadable: () => _('Present but unreadable'),
	malformed: () => _('Unrecognized format; values withheld'),
	truncated: () => _('Output exceeded the read bound; values withheld')
};

function sourceStatus(record) {
	return record == null ? _('Unknown') : `${described(SOURCE_STATUS, record.status)} (${record.source})`;
}

function table(headers, rows) {
	return E('table', { 'class': 'table' }, [
		E('tr', { 'class': 'tr table-titles' }, headers.map(h => E('th', { 'class': 'th' }, h))),
		...rows.map(row => E('tr', { 'class': 'tr' }, row.map(value => E('td', { 'class': 'td' }, display(value)))))
	]);
}

function preflightSection(state) {
	const nodes = [ E('h3', {}, _('Preflight')) ];
	for (const preflight of state.preflight) {
		if (preflight.target !== 'hardware' && !state.proxy?.[preflight.target + '_installed'])
			continue;
		// Info rows are caveats, not failures: listed, but not counted against the checks.
		const problems = preflight.checks.filter(c => c.level === 'error' || c.level === 'warn').length;
		const listed = preflight.checks.filter(c => c.level !== 'ok');
		nodes.push(E('p', {}, `${preflight.target}: ${preflight.passed ? _('Passed') : _('Failed')} · ` +
			`${preflight.checks.length - problems}/${preflight.checks.length} ${_('checks OK')}`));
		if (listed.length)
			nodes.push(table([ _('Result'), _('Check') ], listed.map(c => [ c.level.toUpperCase(), _(c.message) ])));
	}
	return nodes;
}

const FAN_HEALTH = {
	ok: () => _('Normal'),
	'ok-no-fault-flag': () => _('Turning; fault flag not exported'),
	stopped: () => _('Stopped (0 RPM)'),
	fault: () => _('Fault flag set'),
	unreadable: () => _('Reading unavailable'),
	'pwm-out-of-range': () => _('PWM outside the 77-255 guard range')
};

const SENSOR_STATUS = {
	ok: () => _('Valid'),
	missing: () => _('NSS sensor missing'),
	ambiguous: () => _('More than one NSS sensor'),
	invalid: () => _('Invalid reading'),
	'out-of-range': () => _('Outside the 1-130 C guard range')
};

// The fan guard's own record; see fan_protection() in platform.uc.
const GUARD_STATUS = {
	current: () => _('Current record from the running guard'),
	stale: () => _('Last record only; current guard state unknown'),
	exited: () => _('Guard reported that it exited; last record only'),
	unknown: () => _('Unknown')
};

const GUARD_DETAIL = {
	missing: () => _('no record'),
	unreadable: () => _('record unreadable'),
	truncated: () => _('record too large'),
	malformed: () => _('record invalid'),
	'identity-unavailable': () => _('guard identity cannot be checked'),
	'previous-boot': () => _('from a previous boot'),
	'no-guard-process': () => _('no guard process'),
	'multiple-guard-processes': () => _('more than one guard process'),
	'other-instance': () => _('from another guard instance'),
	'age-unknown': () => _('record age unknown'),
	expired: () => _('not updated within 10 s')
};

const GUARD_REASON = {
	'not-sampled': () => _('Not sampled yet'),
	ok: () => _('Guard checks passed'),
	'sensor-missing': () => _('NSS sensor missing'),
	'sensor-invalid': () => _('NSS temperature unreadable or outside 1-130 C'),
	'rpm-invalid': () => _('RPM unreadable'),
	'pwm-invalid': () => _('PWM unreadable'),
	'pwm-out-of-range': () => _('PWM outside the 77-255 guard range'),
	fault: () => _('Fault flag not 0 or unreadable'),
	'rpm-zero': () => _('Stopped (0 RPM)')
};

const GUARD_EVENT = {
	startup: () => _('Startup check'),
	sample: () => _('Periodic check'),
	'controller-unavailable-or-ambiguous': () => _('Stopped: fan controller missing or ambiguous'),
	'pwm-write-failed': () => _('Stopped: PWM write failed'),
	'signal-stop': () => _('Stopped by signal'),
	'loop-ended': () => _('Monitoring loop ended')
};

function latchReason(value) {
	const [ kind, reason ] = String(value).split(':');
	if (value === 'over-temperature') return _('NSS temperature reached 75 C');
	if (kind === 'startup') return `${_('Startup check failed')}: ${described(GUARD_REASON, reason)}`;
	if (kind === 'consecutive-failures') return `${_('Three consecutive failed checks')}: ${described(GUARD_REASON, reason)}`;
	return String(value);
}

const GUARD_PROCESS = {
	running: () => _('Process present'),
	multiple: () => _('More than one process'),
	absent: () => _('Not running')
};

function guardRows(p, process) {
	p = p || { status: 'unknown' };
	const unknown = p.status === 'unknown';
	const last = v => unknown ? _('Unknown') : p.status === 'current' ? v : `${_('Last record')}: ${v}`;
	// Without a current record, process presence tells a silent guard from a missing one.
	const rows = [
		[ _('Fan guard record'), described(GUARD_STATUS, p.status) + (p.detail ? ` (${described(GUARD_DETAIL, p.detail)})` : '') +
			(p.status === 'current' ? '' : ` · ${described(GUARD_PROCESS, process)}`) ],
		[ _('Guard sample result'), last(described(GUARD_REASON, p.sample_reason) +
			(p.sample_age == null ? '' : ` · ${seconds(p.sample_age)}`)) ]
	];
	// Periodic checks are the normal case; only other events say something.
	if (p.event != null && p.event !== 'sample')
		rows.push([ _('Guard event'), last(described(GUARD_EVENT, p.event)) ]);
	rows.push([ _('Consecutive failed checks'), last(display(p.failures)) ]);
	rows.push([ _('Software full-speed latch'), last(p.latched == null ? _('Unknown') : p.latched ?
		`${_('Set')}; ${_('first reason')}: ${latchReason(p.latch_reason)}` : _('Not set')) ]);
	return rows;
}

// One character per sample; a gap in sampling is drawn as a space so a
// missing period never looks like a continuous reading.
function sparkline(samples, key) {
	const values = samples.map(s => s[key]).filter(v => typeof v === 'number');
	if (!values.length) return null;
	const low = Math.min(...values), high = Math.max(...values);
	const bars = '▁▂▃▄▅▆▇█';
	const line = samples.map(s => (s.gap_before ? ' ' : '') + (typeof s[key] !== 'number' ? '·' :
		bars[high === low ? 0 : Math.round((s[key] - low) / (high - low) * 7)])).join('');
	return `${line}  ${_('min')} ${low} / ${_('max')} ${high} / ${_('latest')} ${display(samples[samples.length - 1][key])}`;
}

function fanSection(state) {
	const fan = state.fan || {};
	const trend = fan.trend || { samples: [] };
	const samples = trend.samples || [];
	const rows = [
		[ _('Fan controller'), fan.controller === 'found' ? _('One EMC2301 at 0x2f') : fan.controller === 'ambiguous' ? _('More than one candidate') : _('Not found') ]
	];
	if (fan.temperature?.status !== 'ok')
		rows.push([ _('NSS temperature'), described(SENSOR_STATUS, fan.temperature?.status) ]);
	rows.push(...guardRows(fan.protection, fan.guard_process),
		[ `${_('Temperature trend')} (C)`, sparkline(samples, 'celsius') ],
		[ _('RPM trend'), sparkline(samples, 'rpm') ],
		[ _('PWM trend'), sparkline(samples, 'pwm') ]);
	return [
		E('h3', {}, _('Temperature and fan')),
		table([ _('Sensor'), _('Temperature (C)') ], state.temperatures.map(t => [ t.name, t.celsius ])),
		table([ _('Fan'), _('RPM'), _('PWM (0-255)'), _('Fault'), _('Assessment') ], state.fans.map(f =>
			[ f.hwmon, f.rpm, f.pwm, f.fault_available ? f.fault : _('Not exported'), described(FAN_HEALTH, f.health) ])),
		table([ _('Item'), _('Value') ], rows),
		E('p', {}, `${_('Trends are sampled only while this page is open')} (${samples.length}/${display(trend.capacity)}, ` +
			`${_('every')} ${seconds(trend.interval)}). ` +
			_('The guard record is current only for the one running guard within 10 s. A set latch is a software decision; it does not prove the fan reached full speed.'))
	];
}

function ppeSection(ppe) {
	if (!ppe) return [];
	const flows = ppe.active_flows;
	const resources = ppe.resources || {};
	const rows = [
		[ _('PPE module'), ppe.present ? _('Loaded') : _('Not loaded') ],
		[ _('Kernel debug interface'), ppe.debugfs_accessible ? _('Available') : _('Unavailable') ],
		[ _('Active hardware flows source'), sourceStatus(ppe.active_flows_source) ],
		[ _('Active hardware flows (IPv4 / IPv6 / total / capacity)'), flows == null ? _('Unavailable') :
			[ flows.ipv4, flows.ipv6, flows.total, flows.capacity ].map(v => v == null ? '-' : v).join(' / ') ],
		[ _('Resource table source'), sourceStatus(resources) ]
	];
	if (resources.status === 'ok') {
		if (resources.host) rows.push([ _('Host entries (active / capacity)'), `${counter(resources.host.active)} / ${counter(resources.host.capacity)}` ]);
		if (resources.nexthop) rows.push([ _('Next hops (active / capacity)'), `${counter(resources.nexthop.active)} / ${counter(resources.nexthop.capacity)}` ]);
		const slots = resources.slots, quarantine = resources.quarantine;
		rows.push([ _('Flow directions (IPv4 / IPv6)'), slots == null ? _('Not reported by this kernel') :
			`${counter(slots.ipv4_directions)} / ${counter(slots.ipv6_directions)}` ]);
		rows.push([ _('Weighted flow slots (active / capacity)'), slots == null ? _('Not reported by this kernel') :
			`${counter(slots.active_slots)} / ${counter(slots.capacity)}` ]);
		rows.push([ _('Quarantined rules (rules / slot budget)'), quarantine == null ? _('Not reported by this kernel') :
			`${counter(quarantine.rules)} / ${counter(quarantine.slots_budget)}` ]);
		for (const [ stage, values ] of Object.entries(resources.failures || {})) {
			const nonzero = Object.entries(values).filter(([ k, v ]) => k !== 'last_errno' && v !== '0');
			if (nonzero.length)
				rows.push([ `${_('Resource failures')}: ${stage}`, nonzero.map(([ k, v ]) => `${k} ${counter(v)}`).join(', ') +
					` (${_('last errno')} ${values.last_errno})` ]);
		}
	}
	const ds = ppe.direct_switch || {};
	rows.push([ _('Direct-switch source'), sourceStatus(ds) ]);
	for (const node of ds.nodes || [])
		rows.push([ `${_('Direct-switch node')} ${node.node}`, !node.registered ? _('Not registered') :
			`${node.running ? _('Running') : _('Stopped')}, ${node.faulted ? _('Faulted') : _('No fault flag')}, ` +
			`${_('refill failures')} ${counter(node.counters?.refill_fail)}` ]);
	return [
		E('h3', {}, _('Acceleration diagnostics')),
		table([ _('Item'), _('Value') ], rows),
		E('p', {}, _('Active counts are current state; failure counters accumulate until reset. Flow counts are not throughput; zero flows can be normal and a loaded module does not show that traffic is accelerated. An IPv6 rule takes two slots, and active slots plus the quarantine budget do not give exact free slots.'))
	];
}

const SFP_STATUS = {
	link: () => _('Module detected, link up'),
	'detected-no-link': () => _('Module detected, no link'),
	'detected-link-unknown': () => _('Module detected, link state unreadable'),
	'not-detected': () => _('No module detected by the kernel'),
	unknown: () => _('Unknown: module detection unavailable'),
	'interface-missing': () => _('SFP network device missing')
};

const SFP_PRESENCE = {
	'driver-missing': () => _('SFP driver not bound'),
	ambiguous: () => _('More than one SFP cage'),
	missing: () => _('Kernel debug state not available'),
	unreadable: () => _('Present but unreadable'),
	malformed: () => _('Unrecognized format; values withheld'),
	truncated: () => _('Output exceeded the read bound; values withheld')
};

function sfpSection(sfp) {
	if (!sfp) return [];
	const presence = sfp.presence || {};
	const led = sfp.led || {};
	const rows = [ [ _('State'), described(SFP_STATUS, sfp.status) ] ];
	if (presence.status === 'ok')
		rows.push([ _('Kernel module state'), presence.module_state ]);
	else if (sfp.status !== 'interface-missing')
		rows.push([ _('Module detection source'), `${described(SFP_PRESENCE, presence.status)} (${presence.source || '-'})` ]);
	rows.push([ _('LED presence mode'), !led.presence_mode ? _('Disabled (default link/activity LED)') :
		led.controller === 'running' ? _('Enabled') :
		`${_('Enabled')}; ${led.controller === 'multiple' ? _('More than one process') : _('Not running')}` ]);
	return [
		E('h3', {}, _('SFP port')),
		table([ _('Item'), _('Value') ], rows),
		E('p', {}, _('RC01 has no module-detect line: presence is inferred from module I2C reads, so "not detected" cannot prove the cage is empty. Presence LED mode: uci set system.led_sfp.rc01_presence=1.'))
	];
}

function contents(state, previous) {
	if (state?.schema !== 1 || !Array.isArray(state.preflight) ||
		!state.preflight.every(p => p && typeof p.target === 'string' && typeof p.passed === 'boolean' &&
			Array.isArray(p.checks) && p.checks.every(c => c &&
				[ 'ok', 'info', 'warn', 'error' ].includes(c.level) && typeof c.message === 'string')) ||
		!state.preflight.some(p => p.target === 'hardware'))
		throw new Error(_('Invalid RC01 status response'));
	// A sample that does not advance since the previous refresh is stale.
	const now = state.sampled_at?.uptime;
	const stale = !Number.isFinite(now) || now < 0 ||
		(previous?.sampled_at?.uptime != null && now <= previous.sampled_at.uptime);
	return [
		E('h2', {}, _('RC01 Hardware')),
		E('p', {}, `${display(state.board)} · Linux ${display(state.kernel)} · ${_('uptime at sample')} ${seconds(now)}` +
			(stale ? ` — ${_('sample time did not advance; values may be stale')}` : '')),
		...preflightSection(state),
		...fanSection(state),
		...ppeSection(state.ppe),
		...sfpSection(state.sfp)
	];
}

return view.extend({
	load() { return callStatus().catch(() => null); },
	render(state) {
		let previous = null, nodes;
		const unavailable = () => E('p', { 'class': 'alert-message warning' }, _('RC01 status is unavailable. Previous values are no longer current.'));
		try {
			nodes = contents(state, null);
			previous = state;
		}
		catch (e) {
			nodes = unavailable();
		}
		const wrapper = E('div', {}, nodes);
		poll.add(() => callStatus().then(next => {
			dom.content(wrapper, contents(next, previous));
			previous = next;
		}).catch(() => {
			// Never keep showing readings that can no longer be refreshed.
			// Retain the last successful timestamp to detect replay after recovery.
			dom.content(wrapper, unavailable());
		}), 5);
		return wrapper;
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
