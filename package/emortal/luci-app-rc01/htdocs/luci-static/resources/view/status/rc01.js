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

function table(headers, rows) {
	return E('table', { 'class': 'table' }, [
		E('tr', { 'class': 'tr table-titles' }, headers.map(h => E('th', { 'class': 'th' }, h))),
		...rows.map(row => E('tr', { 'class': 'tr' }, row.map(value => E('td', { 'class': 'td' }, display(value)))))
	]);
}

function contents(state) {
	if (state.schema !== 1 || !Array.isArray(state.preflight))
		throw new Error(_('Invalid RC01 status response'));
	const hardware = state.preflight.find(p => p.target === 'hardware');
	if (!hardware) throw new Error(_('Invalid RC01 status response'));
	const nodes = [
		E('h2', {}, _('RC01 Hardware')),
		E('p', {}, _('Read-only status. No service, network or fan settings are changed. Preflight does not replace traffic or hardware acceptance.')),
		E('p', {}, `${display(state.board)} · Linux ${display(state.kernel)}`),
		E('h3', {}, _('Temperature and fan')),
		table([ _('Sensor'), _('Temperature (C)') ], state.temperatures.map(t => [ t.name, t.celsius ])),
		table([ _('Fan'), _('RPM'), _('PWM (0-255)'), _('Fault') ], state.fans.map(f => [ f.hwmon, f.rpm, f.pwm, f.fault ])),
		E('h3', {}, _('Ethernet ports')),
		table([ _('Port'), _('Device'), _('Link'), _('Speed (Mbps)'), _('Duplex') ], state.ports.map(p =>
			[ p.name, p.present ? _('Present') : _('Missing'), p.carrier == null ? null : p.carrier ? _('Up') : _('Down'), p.speed_mbps, p.duplex ])),
		E('h3', {}, _('Storage and acceleration')),
		table([ _('Item'), _('Value') ], [
			[ _('Rootfs partition (MiB)'), state.storage.rootfs?.size == null ? null : state.storage.rootfs.size / 1048576 ],
			[ _('Environment map'), state.storage.environment_map_valid ? _('64 KiB map matches') : _('Mismatch or unavailable') ],
			[ _('Overlay available (MiB)'), state.storage.overlay_available_bytes == null ? null : (state.storage.overlay_available_bytes / 1048576).toFixed(1) ],
			[ _('PPE active flows'), state.ppe.active_flows?.total ],
			[ _('Kernel BTF'), state.proxy.btf ? _('Present') : _('Missing') ]
		]),
		E('p', {}, _('Environment contents and CRC are not read here. Zero PPE flows can be normal; hardware-forwarded traffic may not appear in packet captures.')),
		E('h3', {}, _('Wireless configuration')),
		table([ _('Radio'), _('Configured state') ], (state.radios || []).map(r =>
			[ r.name, r.disabled ? _('Disabled') : _('Enabled') ])),
		E('p', {}, _('Configured state does not confirm whether a radio is transmitting.')),
		E('h3', {}, _('Proxy processes')),
		table([ _('Process'), _('PID'), _('RSS (KiB)') ], state.processes.map(p => [ p.name, p.pid, p.rss_kib ]))
	];
	for (const preflight of state.preflight) {
		nodes.push(E('h3', {}, `${_('Preflight')}: ${preflight.target}`));
		if (preflight.target === 'daed' && !state.proxy.daed_installed) {
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
		const wrapper = E('div', {}, contents(state));
		poll.add(() => callStatus().then(next => dom.content(wrapper, contents(next))).catch(() => {
			dom.content(wrapper, E('p', { 'class': 'alert-message warning' }, _('RC01 status is unavailable. Previous values are no longer current.')));
		}), 5);
		return wrapper;
	},
	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
