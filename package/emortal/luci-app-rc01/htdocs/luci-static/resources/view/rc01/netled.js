// SPDX-License-Identifier: GPL-2.0-only
'use strict';
'require view';
'require form';
'require rpc';
'require poll';
'require dom';
'require network';

const callConnectivity = rpc.declare({ object: 'rc01', method: 'connectivity', expect: { '': {} } });

const MAX_TARGETS = 3;

const SERVICE_STATUS = {
	ok: () => _('Running; report is current'),
	disabled: () => _('Disabled in the configuration; the LEDs are not driven'),
	'not-running': () => _('Enabled but the service is not running (invalid configuration, other board, or stopped)'),
	starting: () => _('Starting; no report yet'),
	stale: () => _('Report is older than its validity bound; result withheld'),
	malformed: () => _('Report unreadable; result withheld'),
	unreadable: () => _('Report unreadable; result withheld'),
	truncated: () => _('Report unreadable; result withheld')
};

const OVERALL = {
	up: () => _('All enabled families reach a target'),
	degraded: () => _('Only some enabled families reach a target'),
	down: () => _('No enabled family reaches a target'),
	unknown: () => _('Not determined yet, or probes are failing')
};

const FAMILY = {
	up: () => _('Reachable: a target answered ICMP echo'),
	down: () => _('Unreachable: a route exists but no target answered'),
	'no-path': () => _('No applicable route or source address for this family'),
	'egress-unavailable': () => _('Chosen interface is missing, down or has no device'),
	stale: () => _('Stale: the probes could not run; the earlier result is withheld'),
	unknown: () => _('Unknown: no confirmed result yet'),
	disabled: () => _('Disabled')
};

const LAST = {
	ok: () => _('reply'),
	fail: () => _('no reply'),
	nopath: () => _('no route/address'),
	unavail: () => _('interface unavailable'),
	error: () => _('probe error')
};

const LED_CONTROL = {
	owned: () => _('Driven by this service'),
	'write-failed': () => _('Driven by this service; last write failed, retrying'),
	yielded: () => _('Left alone: another trigger was selected'),
	unmanaged: () => _('Not driven: configured under System → LED Configuration, or LEDs turned off'),
	missing: () => _('LED not found')
};

const LED_SHOWN = {
	on: () => _('on'),
	off: () => _('off'),
	blink: () => _('blinking')
};

function describe(table, value) {
	return value == null ? _('Unknown') : table[value] ? table[value]() : String(value);
}

function familyRow(name, family, counts) {
	if (!family?.enabled)
		return [ name, describe(FAMILY, 'disabled'), '-', '-', '-' ];
	const last = family.last == null ? _('none yet') :
		`${describe(LAST, family.last)}${family.reason ? ` (${family.reason})` : ''}`;
	return [
		name,
		describe(FAMILY, family.state),
		`${family.interface === 'auto' ? _('automatic (kernel route)') : family.interface}${family.device ? ` → ${family.device}` : ''}`,
		`${(family.targets || []).join(', ')}${family.target ? ` · ${_('last')}: ${family.target}` : ''}`,
		`${last} · ${_('streak')} ${family.ok_streak}/${family.bad_streak}/${family.error_streak} ${counts}`
	];
}

function table(headers, rows) {
	return E('table', { 'class': 'table' }, [
		E('tr', { 'class': 'tr table-titles' }, headers.map(h => E('th', { 'class': 'th' }, h))),
		...rows.map(row => E('tr', { 'class': 'tr' }, row.map(v => E('td', { 'class': 'td' }, v == null ? _('Unknown') : String(v)))))
	]);
}

function statusNodes(state) {
	if (state == null || typeof state !== 'object' || state.status == null)
		return [ E('p', { 'class': 'alert-message warning' }, _('Connectivity status is unavailable. Previous values are no longer current.')) ];
	const nodes = [ E('p', {}, `${_('Service')}: ${describe(SERVICE_STATUS, state.status)}`) ];
	const report = state.status === 'ok' ? state.report : null;
	if (report == null) return nodes;
	nodes.push(E('p', {}, `${_('Overall')}: ${describe(OVERALL, report.overall)} · ${_('report age')} ${state.age ?? '?'} s · ${_('rounds')} ${report.rounds}`));
	const counts = `(${_('confirm')} ${report.fail_count}, ${_('recover')} ${report.recover_count})`;
	nodes.push(table([ _('Family'), _('State'), _('Egress'), _('Targets'), _('Last round · ok/bad/error streak') ], [
		familyRow('IPv4', report.families.ipv4, counts),
		familyRow('IPv6', report.families.ipv6, counts)
	]));
	nodes.push(table([ _('LED'), _('Control'), _('Shown') ], [ [ 'white', report.leds.white ], [ 'amber', report.leds.amber ] ].map(([ color, led ]) => [
		led?.name || `${color}:network`, describe(LED_CONTROL, led?.control), led?.shown ? describe(LED_SHOWN, led.shown) : '-'
	])));
	return nodes;
}

function targetsValidator(family, unicast) {
	return function(section_id, value) {
		const values = [].concat(this.formvalue(section_id) || []).filter(v => v !== '');
		if (values.length === 0)
			return _('At least one %s target is required').format(family);
		if (values.length > MAX_TARGETS)
			return _('At most %d targets').format(MAX_TARGETS);
		if (value == null || value === '') return true;
		return unicast(value) || _('Use a unicast %s address (not loopback, link-local or multicast)').format(family);
	};
}

function validateFamilies(section_id) {
	const checked = key => this.map.lookupOption(key, section_id)[0].formvalue(section_id) === '1';
	return !checked('enabled') || checked('ipv4') || checked('ipv6') ||
		_('Enable IPv4 or IPv6 before enabling the service');
}

// Same rules as rc01-netled's own check, so the page never saves a value
// the service then refuses: dotted quad without leading zeros, no 0/8,
// loopback or 224/3; IPv6 without embedded IPv4, zone, unspecified,
// loopback, link-local or multicast. LuCI's ip4addr/ip6addr come first.
function unicast4(value) {
	const octet = '(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])';
	if (!new RegExp(`^${octet}(\\.${octet}){3}$`).test(value)) return false;
	const first = +value.split('.')[0];
	return first !== 0 && first !== 127 && first < 224;
}

function unicast6(value) {
	const v = value.toLowerCase();
	if (!/^[0-9a-f:]+$/.test(v) || v.length > 39 || v === '::' || /^::0{0,3}1$/.test(v)) return false;
	const first = v.split(':')[0];
	return !(first.length === 4 && (/^ff/.test(first) || /^fe[89ab]/.test(first)));
}

// LuCI's uinteger accepts leading zeros ("030"); the service does not, as
// shell arithmetic would read them as octal.
function plainInteger(section_id, value) {
	return value == null || value === '' || /^(0|[1-9][0-9]*)$/.test(value) || _('Whole number without leading zeros');
}

return view.extend({
	statusNodes,
	unicast4,
	unicast6,
	plainInteger,

	load() {
		return Promise.all([
			callConnectivity().catch(() => null),
			network.getNetworks().catch(() => [])
		]);
	},

	render([ state, networks ]) {
		const m = new form.Map('rc01_netled', _('Connectivity LED'),
			_('Shows ICMP echo reachability from the chosen egress to the configured target addresses on the front network LEDs (white and amber). It does not test DNS, web pages or proxies: networks that filter ICMP can show unreachable while other traffic works, and a lit white LED does not prove that DNS, web or proxy traffic works. System status, RGB, Ethernet port and SFP LEDs are not touched.'));

		const s = m.section(form.NamedSection, 'main', 'netled', _('Settings'));
		s.addremove = false;
		let o = s.option(form.Flag, 'enabled', _('Enable'),
			_('Off by default. While enabled the service drives the white:network and amber:network LEDs unless a System → LED Configuration entry names them. Stopping turns both off again (board default).'));
		o.rmempty = false;
		o.validate = validateFamilies;

		for (const [ family, unicast, datatype ] of [ [ 'IPv4', unicast4, 'ip4addr("nomask")' ], [ 'IPv6', unicast6, 'ip6addr("nomask")' ] ]) {
			const key = family.toLowerCase();
			o = s.option(form.Flag, key, _('Check %s').format(family),
				_('With both families enabled, one reachable family shows as partial (white blinking).'));
			o.default = '1';
			o.rmempty = false;
			o.validate = validateFamilies;

			o = s.option(form.DynamicList, key + '_target', _('%s targets').format(family),
				_('1 to %d addresses. The family counts as reachable when any of them answers; one failing target alone does not mark the egress down. Defaults are public anycast DNS resolvers (AliDNS, DNSPod) and can be replaced.').format(MAX_TARGETS));
			o.datatype = datatype;
			o.validate = targetsValidator(family, unicast);
			o.rmempty = false;
			o.retain = true;
			o.depends({ enabled: '1', [key]: '1' });

			o = s.option(form.Value, key + '_interface', _('%s egress').format(family),
				_('"auto" follows the kernel route to each target (works with the LAN DHCP bypass, where the default route is on lan). A named interface pins the probe to its device; if that interface is missing or down the family shows "interface unavailable", and without a route through it "no applicable route".'));
			o.value('auto', _('auto (default route)'));
			for (const net of networks || []) {
				const name = net.getName?.();
				if (name && name !== 'loopback') o.value(name);
			}
			o.default = 'auto';
			o.datatype = 'or("auto", uciname)';
			o.rmempty = false;
			o.depends(key, '1');
		}

		o = s.option(form.Value, 'interval', _('Interval (s)'), _('Seconds between rounds, 10 to 3600.'));
		o.datatype = 'and(uinteger,range(10,3600))';
		o.validate = plainInteger;
		o.default = '30';
		o.rmempty = false;

		o = s.option(form.Value, 'timeout', _('Reply timeout (s)'),
			_('Wait per echo, 1 to 5 s. Each command also has a hard time bound.'));
		o.datatype = 'and(uinteger,range(1,5))';
		o.validate = plainInteger;
		o.default = '2';
		o.rmempty = false;

		o = s.option(form.Value, 'fail_count', _('Failure confirmation (rounds)'),
			_('Consecutive failed rounds before a family is shown unreachable, 1 to 10. The same number of rounds where the probes cannot run turns a result into stale.'));
		o.datatype = 'and(uinteger,range(1,10))';
		o.validate = plainInteger;
		o.default = '3';
		o.rmempty = false;

		o = s.option(form.Value, 'recover_count', _('Recovery confirmation (rounds)'),
			_('Consecutive successful rounds before an unreachable family is shown reachable again, 1 to 10.'));
		o.datatype = 'and(uinteger,range(1,10))';
		o.validate = plainInteger;
		o.default = '2';
		o.rmempty = false;

		const status = E('div', {}, statusNodes(state));
		poll.add(() => callConnectivity().then(next => {
			dom.content(status, statusNodes(next));
		}).catch(() => {
			dom.content(status, statusNodes(null));
		}), 5);

		return m.render().then(node => E('div', {}, [
			node,
			E('h3', {}, _('Current state')),
			status,
			E('h3', {}, _('LED meaning')),
			E('ul', {}, [
				E('li', {}, _('White steady: every enabled family reaches a target.')),
				E('li', {}, _('White blinking: some enabled families reach a target, others do not or are not confirmed.')),
				E('li', {}, _('Amber steady: no enabled family reaches a target (unreachable, no route, or interface unavailable).')),
				E('li', {}, _('Amber blinking: not determined yet, or the probes keep failing to run (stale).')),
				E('li', {}, _('Both off: service disabled or stopped, or the LEDs are configured elsewhere.'))
			]),
			E('p', {}, _('Results change only after the configured number of consecutive rounds. After a configuration change or restart the service starts again from unknown.'))
		]));
	}
});
