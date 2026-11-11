// SPDX-License-Identifier: GPL-2.0-only
'use strict';
'require view';
'require rpc';
'require poll';
'require dom';

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

const SOURCE_STATUS = {
	ok: () => _('Available'),
	missing: () => _('Not exported by this kernel'),
	unreadable: () => _('Present but unreadable'),
	malformed: () => _('Unrecognized format; values withheld'),
	truncated: () => _('Output exceeded the read bound; values withheld')
};

function sourceStatus(record) {
	const describe = SOURCE_STATUS[record?.status];
	return record == null ? _('Unknown') : `${describe ? describe() : record.status} (${record.source})`;
}

function table(headers, rows) {
	return E('table', { 'class': 'table' }, [
		E('tr', { 'class': 'tr table-titles' }, headers.map(h => E('th', { 'class': 'th' }, h))),
		...rows.map(row => E('tr', { 'class': 'tr' }, row.map(value => E('td', { 'class': 'td' }, display(value)))))
	]);
}

function ppeSection(ppe, stale) {
	if (!ppe) return [];
	const nodes = [ E('h3', {}, _('Acceleration diagnostics')) ];
	const flows = ppe.active_flows;
	const resources = ppe.resources || {};
	const rows = [
		[ _('PPE module'), ppe.present ? _('Loaded') : _('Not loaded') ],
		[ _('Kernel debug interface'), ppe.debugfs_mounted ? _('Mounted') : _('Not mounted') ],
		[ _('Active hardware flows source'), sourceStatus(ppe.active_flows_source) ],
		[ _('Active hardware flows (IPv4 / IPv6 / total / capacity)'), flows == null ? _('Unavailable') :
			[ flows.ipv4, flows.ipv6, flows.total, flows.capacity ].map(v => v == null ? '-' : v).join(' / ') ],
		[ _('Resource table source'), sourceStatus(resources) ]
	];
	if (resources.status === 'ok') {
		if (resources.host) rows.push([ _('Host entries (active / capacity)'), `${counter(resources.host.active)} / ${counter(resources.host.capacity)}` ]);
		if (resources.nexthop) rows.push([ _('Next hops (active / capacity)'), `${counter(resources.nexthop.active)} / ${counter(resources.nexthop.capacity)}` ]);
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
	nodes.push(table([ _('Item'), _('Value') ], rows));

	const detail = ppe.detail || {};
	const old = detail.age != null && detail.interval != null && detail.age > 2 * detail.interval;
	nodes.push(E('p', {}, `${_('Flow events and reject stages are sampled at most every')} ${seconds(detail.interval)}; ` +
		`${_('sample age')} ${seconds(detail.age)}${old || stale ? ' — ' + _('stale') : ''}.`));
	const events = detail.flows;
	const eventRows = [ [ _('Flow event source'), sourceStatus(events) ] ];
	if (events?.status === 'ok') {
		for (const [ k, v ] of Object.entries(events.events || {})) eventRows.push([ `${_('Event')}: ${k}`, counter(v) ]);
		for (const [ k, v ] of Object.entries(events.rejects || {})) eventRows.push([ `${_('Rejected at')}: ${k}`, counter(v) ]);
		for (const [ k, v ] of Object.entries(events.topology || {})) eventRows.push([ `${_('Topology')}: ${k}`, counter(v) ]);
		for (const [ k, v ] of Object.entries(events.rule || {})) eventRows.push([ `${_('Flow rule')}: ${k}`, counter(v) ]);
	}
	const reasons = detail.reasons;
	eventRows.push([ _('CPU/drop reason source'), sourceStatus(reasons) ]);
	if (reasons?.status === 'ok') {
		eventRows.push([ _('Rows with unreadable hardware counters'), reasons.read_failures ]);
		for (const row of reasons.rows || [])
			eventRows.push([ `${row.kind === 'cpu' ? _('CPU code') : _('Drop code')} ${row.code}${row.port != null ? ' / ' + _('port') + ' ' + row.port : ''}: ${row.name}`,
				`${counter(row.packets)} ${_('packets')}, ${counter(row.bytes)} ${_('bytes')}` ]);
	}
	nodes.push(table([ _('Counter'), _('Cumulative value') ], eventRows));
	nodes.push(E('p', {}, _('Counts are read from the kernel and are cumulative since the driver loaded. A loaded module, an enabled setting or a successful read does not show that traffic is accelerated, and flow counts are not throughput.')));
	return nodes;
}

function contents(state, previous) {
	if (state.schema !== 1 || !Array.isArray(state.preflight))
		throw new Error(_('Invalid RC01 status response'));
	const hardware = state.preflight.find(p => p.target === 'hardware');
	if (!hardware) throw new Error(_('Invalid RC01 status response'));
	// A sample that does not advance since the previous refresh is stale.
	const now = state.sampled_at?.uptime;
	const stale = now == null || (previous?.sampled_at?.uptime != null && now <= previous.sampled_at.uptime);
	const nodes = [
		E('h2', {}, _('RC01 Hardware')),
		E('p', {}, _('Read-only status. No service, network or fan settings are changed. Preflight does not replace traffic or hardware acceptance.')),
		E('p', {}, `${display(state.board)} · Linux ${display(state.kernel)} · ${_('uptime at sample')} ${seconds(now)}` +
			(stale ? ` — ${_('sample time did not advance; values may be stale')}` : '')),
		E('h3', {}, _('Temperature and fan')),
		table([ _('Sensor'), _('Temperature (C)') ], state.temperatures.map(t => [ t.name, t.celsius ])),
		table([ _('Fan'), _('RPM'), _('PWM (0-255)'), _('Fault') ], state.fans.map(f => [ f.hwmon, f.rpm, f.pwm, f.fault ])),
		E('h3', {}, _('Ethernet ports')),
		table([ _('Port'), _('Device'), _('Link'), _('Speed (Mbps)'), _('Duplex') ], state.ports.map(p =>
			[ p.name, p.present ? _('Present') : _('Missing'), p.carrier == null ? null : p.carrier ? _('Up') : _('Down'), p.speed_mbps, p.duplex ])),
		E('h3', {}, _('Storage')),
		table([ _('Item'), _('Value') ], [
			[ _('Rootfs partition (MiB)'), state.storage.rootfs?.size == null ? null : state.storage.rootfs.size / 1048576 ],
			[ _('Environment map'), state.storage.environment_map_valid ? _('64 KiB map matches') : _('Mismatch or unavailable') ],
			[ _('Overlay available (MiB)'), state.storage.overlay_available_bytes == null ? null : (state.storage.overlay_available_bytes / 1048576).toFixed(1) ],
			[ _('Kernel BTF'), state.proxy.btf ? _('Present') : _('Missing') ]
		]),
		E('p', {}, _('Environment contents and CRC are not read here. Zero PPE flows can be normal; hardware-forwarded traffic may not appear in packet captures.')),
		...ppeSection(state.ppe, stale),
		E('h3', {}, _('Wireless configuration')),
		table([ _('Radio'), _('Configured state') ], (state.radios || []).map(r =>
			[ r.name, r.disabled ? _('Disabled') : _('Enabled') ])),
		E('p', {}, _('Configured state does not confirm whether a radio is transmitting.')),
		E('h3', {}, _('Proxy processes')),
		table([ _('Process'), _('PID'), _('RSS (KiB)') ], state.processes.map(p => [ p.name, p.pid, p.rss_kib ]))
	];
	for (const preflight of state.preflight) {
		nodes.push(E('h3', {}, `${_('Preflight')}: ${preflight.target}`));
		if (['dae', 'daed'].includes(preflight.target) && !state.proxy[preflight.target + '_installed']) {
			nodes.push(E('p', {}, _('Not included in this build')));
			continue;
		}
		nodes.push(table([ _('Result'), _('Check') ], preflight.checks.map(c => [ c.level.toUpperCase(), _(c.message) ])));
	}
	return nodes;
}

return view.extend({
	load() { return callStatus(); },
	render(state) {
		let previous = state;
		const wrapper = E('div', {}, contents(state, null));
		poll.add(() => callStatus().then(next => {
			dom.content(wrapper, contents(next, previous));
			previous = next;
		}).catch(() => {
			// Never keep showing readings that can no longer be refreshed.
			previous = null;
			dom.content(wrapper, E('p', { 'class': 'alert-message warning' }, _('RC01 status is unavailable. Previous values are no longer current.')));
		}), 5);
		return wrapper;
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
