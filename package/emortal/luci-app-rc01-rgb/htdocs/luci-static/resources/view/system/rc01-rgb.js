// SPDX-License-Identifier: GPL-2.0-or-later
// Settings page for the rc01-rgb service. Writes only the rc01_rgb UCI
// config; the service restarts itself when that config is applied.
// Layout and options adapted from be10000-rgb.js by zh8416
// (github.com/zh8416/immortalwrt 7c3057979a9e1767fed5e12401ae7d1948223f16).
'use strict';
'require view';
'require form';
'require fs';
'require rpc';
'require uci';

const callServiceList = rpc.declare({
	object: 'rc',
	method: 'list',
	params: [ 'name' ],
	expect: { '': {} }
});

const COLOR = /^#[0-9a-fA-F]{6}$/;
const TIME = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;
const DYNAMIC = [ 'breathe', 'duotone', 'rainbow' ];
const TELEMETRY = [ 'temperature', 'cpu', 'traffic' ];

function validateColor(section_id, value) {
	return COLOR.test(value || '') ? true : _('Use the #RRGGBB format');
}

function validateTime(section_id, value) {
	return TIME.test(value || '') ? true : _('Use the HH:MM format (00:00-23:59)');
}

function dependsOnModes(option, modes) {
	modes.forEach(function(mode) { option.depends('mode', mode); });
}

const ColorValue = form.Value.extend({
	renderWidget: function(section_id, option_index, cfgvalue) {
		const widget = this.super('renderWidget', [ section_id, option_index, cfgvalue ]);
		const input = widget.querySelector('input');
		const picker = E('input', {
			'type': 'color',
			'value': COLOR.test(cfgvalue || '') ? cfgvalue : this.default,
			'style': 'width:3em;height:2em;margin-left:.5em;vertical-align:middle',
			'change': function(ev) {
				input.value = ev.target.value;
				input.dispatchEvent(new Event('input', { bubbles: true }));
				input.dispatchEvent(new Event('change', { bubbles: true }));
			}
		});
		input.addEventListener('input', function() {
			if (COLOR.test(input.value))
				picker.value = input.value;
		});
		return E('div', { 'style': 'display:flex;align-items:center' }, [ widget, picker ]);
	}
});

function statusText(service, raw) {
	const info = service['rc01-rgb'] || {};
	if (!info.enabled)
		return _('Service disabled at boot');
	if (!info.running)
		return _('Not running (RGB off, LED missing or system LEDs off)');

	let status;
	try { status = JSON.parse(raw || ''); } catch (e) { status = null; }
	if (!status)
		return _('Running; no status yet');

	const period = {
		'day': _('normal'),
		'night': _('night brightness'),
		'schedule-off': _('off by schedule')
	}[status.period] || status.period;
	let text = _('Running: %s, %s, brightness %d').format(status.mode, period, status.level);
	if (status.fallback)
		text += ' · ' + _('data %s, showing primary colour').format(
			{ 'sampling': _('being sampled'), 'source unavailable': _('unavailable') }[status.detail] || status.detail);
	else if (status.detail)
		text += ' · ' + status.detail;
	if (status.led_write_failed)
		text += ' · ' + _('LED write failed');
	if (status.ignored_options && status.ignored_options.length)
		text += ' · ' + _('invalid options replaced by defaults: %s').format(status.ignored_options.join(', '));
	return text;
}

return view.extend({
	load: function() {
		return Promise.all([
			uci.load('rc01_rgb'),
			L.resolveDefault(callServiceList('rc01-rgb'), {}),
			L.resolveDefault(fs.read('/var/run/rc01-rgb.status'), null)
		]);
	},

	render: function(data) {
		let m, s, o;

		m = new form.Map('rc01_rgb', _('RGB Light'),
			_('Top RGB light bar. Off by default; the wiring is taken from a public reference and still needs checking colour by colour on the device. This service only drives red/green/blue:ambient, never the system, network, port or SFP LEDs, and never the fan.'));

		s = m.section(form.NamedSection, 'main', 'rgb', _('Status'));
		s.anonymous = true;
		o = s.option(form.DummyValue, '_status', _('Service'));
		o.cfgvalue = function() { return statusText(data[1], data[2]); };

		s = m.section(form.NamedSection, 'main', 'rgb', _('Light'));
		s.anonymous = true;

		o = s.option(form.Flag, 'enabled', _('Enable'));
		o.rmempty = false;

		o = s.option(form.ListValue, 'mode', _('Mode'),
			_('Temperature, CPU and traffic modes show the primary colour while their data is missing or still being sampled.'));
		o.value('static', _('Static'));
		o.value('breathe', _('Breathe'));
		o.value('duotone', _('Two-colour fade'));
		o.value('rainbow', _('Rainbow'));
		o.value('temperature', _('Temperature (NSS sensor)'));
		o.value('cpu', _('CPU load'));
		o.value('traffic', _('Network traffic'));
		o.default = 'static';

		o = s.option(ColorValue, 'color', _('Primary colour'));
		o.default = '#0066ff';
		o.rmempty = false;
		o.validate = validateColor;

		o = s.option(ColorValue, 'second_color', _('Second colour'));
		o.default = '#ff00aa';
		o.rmempty = false;
		o.validate = validateColor;
		o.depends('mode', 'duotone');

		o = s.option(form.Value, 'brightness', _('Brightness'), _('0 (off) to 255.'));
	o.datatype = 'and(uinteger,range(0,255))';
		o.default = '96';
		o.rmempty = false;

		o = s.option(form.Value, 'minimum_brightness', _('Minimum brightness'), _('Lowest level of the breathe effect, 0-255.'));
	o.datatype = 'and(uinteger,range(0,255))';
		o.default = '8';
		o.depends('mode', 'breathe');

		o = s.option(form.Value, 'speed', _('Effect speed'), _('1 (slow) to 10 (fast).'));
	o.datatype = 'and(uinteger,range(1,10))';
		o.default = '5';
		dependsOnModes(o, DYNAMIC);

		o = s.option(form.Value, 'sensitivity', _('Sensitivity'), _('1 to 10; higher values change colour sooner.'));
	o.datatype = 'and(uinteger,range(1,10))';
		o.default = '5';
		dependsOnModes(o, TELEMETRY);

		o = s.option(form.Value, 'traffic_interface', _('Traffic interface'),
			_('Network interface (e.g. wan, lan) or device name (e.g. pppoe-wan).'));
		o.validate = function(section_id, value) {
			return /^[A-Za-z0-9_.@-]{1,15}$/.test(value || '') ? true : _('Invalid interface name');
		};
		o.default = 'wan';
		o.depends('mode', 'traffic');

		s = m.section(form.NamedSection, 'main', 'rgb', _('Schedule'),
			_('The on/off schedule wins: outside its on period the bar is off. Inside it the night window can only lower the brightness. Equal start and end times mean the whole day. Uses the router clock and time zone.'));
		s.anonymous = true;

		o = s.option(form.Flag, 'schedule_enabled', _('On/off schedule'));
		o.rmempty = false;
		o = s.option(form.Value, 'on_time', _('On at'));
		o.default = '07:00';
		o.validate = validateTime;
		o.depends('schedule_enabled', '1');
		o = s.option(form.Value, 'off_time', _('Off at'));
		o.default = '23:30';
		o.validate = validateTime;
		o.depends('schedule_enabled', '1');

		o = s.option(form.Flag, 'night_enabled', _('Night brightness'));
		o.rmempty = false;
		o = s.option(form.Value, 'night_start', _('Night starts'));
		o.default = '22:00';
		o.validate = validateTime;
		o.depends('night_enabled', '1');
		o = s.option(form.Value, 'night_end', _('Night ends'));
		o.default = '07:00';
		o.validate = validateTime;
		o.depends('night_enabled', '1');
		o = s.option(form.Value, 'night_brightness', _('Night maximum brightness'), _('0 (off) to 255.'));
	o.datatype = 'and(uinteger,range(0,255))';
		o.default = '24';
		o.depends('night_enabled', '1');

		return m.render();
	}
});
