// SPDX-License-Identifier: GPL-2.0-or-later
//
// RC01 top RGB light bar: colour, brightness, effects and schedule.
// Effect set, colour maps, option names and schedule semantics are adapted
// from be10000-rgb.uc in the public package be10000-fan-rgb by zh8416
// (https://github.com/zh8416/immortalwrt, commit
// 7c3057979a9e1767fed5e12401ae7d1948223f16, GPL-2.0-or-later). The fan
// daemon of that package is not used here.
//
// This module only computes frames and touches its own three LEDs; the
// caller owns the loop, the clock and the configuration source.
import { readfile, writefile, glob, stat } from 'fs';

export const LEDS = [ 'red:ambient', 'green:ambient', 'blue:ambient' ];
export const MODES = [ 'static', 'breathe', 'duotone', 'rainbow', 'temperature', 'cpu', 'traffic' ];
export const ANIMATED = { breathe: true, duotone: true, rainbow: true };
// Animation runs at 10 Hz; everything else (static, telemetry, schedule off)
// re-evaluates once a second. LED files are written only on change.
export const FRAME_MS = 100;
export const IDLE_MS = 1000;

const DEFAULTS = {
	enabled: '0', mode: 'static', color: '#0066ff', second_color: '#ff00aa',
	brightness: '96', minimum_brightness: '8', speed: '5', sensitivity: '5',
	traffic_interface: 'wan',
	schedule_enabled: '0', on_time: '07:00', off_time: '23:30',
	night_enabled: '0', night_start: '22:00', night_end: '07:00', night_brightness: '24'
};

function clamp(value, low, high) {
	value = int(value);
	return value < low ? low : value > high ? high : value;
}

// Unset and empty options take the package default.
function option(raw, name) {
	return raw[name] == null || raw[name] == '' ? DEFAULTS[name] : raw[name];
}

function number(raw, name, low, high, problems) {
	let value = option(raw, name);
	if (!match('' + value, /^[0-9]+$/) || +value < low || +value > high) {
		push(problems, name);
		value = DEFAULTS[name];
	}
	return clamp(value, low, high);
}

function flag(raw, name) {
	return index([ '1', 'true', 'yes', 'on' ], '' + option(raw, name)) >= 0;
}

function colour(raw, name, problems) {
	let value = option(raw, name);
	if (type(value) != 'string' || !match(value, /^#[0-9a-fA-F]{6}$/)) {
		push(problems, name);
		value = DEFAULTS[name];
	}
	return [ hex(substr(value, 1, 2)), hex(substr(value, 3, 2)), hex(substr(value, 5, 2)) ];
}

function minutes(raw, name, problems) {
	let value = option(raw, name);
	let m = type(value) == 'string' ? match(value, /^([01][0-9]|2[0-3]):([0-5][0-9])$/) : null;
	if (!m) {
		push(problems, name);
		m = match(DEFAULTS[name], /^([0-9]+):([0-9]+)$/);
	}
	return int(m[1]) * 60 + int(m[2]);
}

// Normalise one UCI section. Bad values fall back to the package default
// and are listed in `problems`, so the status shows what was ignored.
export function config(raw) {
	raw = raw || {};
	let problems = [];
	let mode = option(raw, 'mode');
	if (index(MODES, mode) < 0) {
		push(problems, 'mode');
		mode = DEFAULTS.mode;
	}
	let iface = option(raw, 'traffic_interface');
	if (type(iface) != 'string' || !match(iface, /^[A-Za-z0-9_.@-]{1,15}$/)) {
		push(problems, 'traffic_interface');
		iface = DEFAULTS.traffic_interface;
	}
	return {
		enabled: flag(raw, 'enabled'),
		mode,
		color: colour(raw, 'color', problems),
		second_color: colour(raw, 'second_color', problems),
		brightness: number(raw, 'brightness', 0, 255, problems),
		minimum_brightness: number(raw, 'minimum_brightness', 0, 255, problems),
		speed: number(raw, 'speed', 1, 10, problems),
		sensitivity: number(raw, 'sensitivity', 1, 10, problems),
		traffic_interface: iface,
		schedule_enabled: flag(raw, 'schedule_enabled'),
		on_time: minutes(raw, 'on_time', problems),
		off_time: minutes(raw, 'off_time', problems),
		night_enabled: flag(raw, 'night_enabled'),
		night_start: minutes(raw, 'night_start', problems),
		night_end: minutes(raw, 'night_end', problems),
		night_brightness: number(raw, 'night_brightness', 0, 255, problems),
		problems
	};
}

// [start, end) in minutes of the day, wrapping past midnight; equal ends
// mean the whole day.
export function in_window(start, end, minute) {
	if (start == end)
		return true;
	if (start < end)
		return minute >= start && minute < end;
	return minute >= start || minute < end;
}

// Priority: the on/off schedule decides whether the bar is lit at all;
// inside the lit period the night window only lowers the ceiling to
// night_brightness, it never raises it and never lights a scheduled-off bar.
export function level(cfg, minute) {
	if (cfg.schedule_enabled && !in_window(cfg.on_time, cfg.off_time, minute))
		return { level: 0, period: 'schedule-off' };
	let night = cfg.night_enabled && in_window(cfg.night_start, cfg.night_end, minute);
	let value = cfg.brightness;
	if (night && cfg.night_brightness < value)
		value = cfg.night_brightness;
	return { level: value, period: night ? 'night' : 'day' };
}

function mix(a, b, amount) {
	amount = clamp(amount, 0, 1000);
	return map([ 0, 1, 2 ], i => clamp((a[i] * (1000 - amount) + b[i] * amount + 500) / 1000, 0, 255));
}

function triangle(phase) {
	phase = phase % 2000;
	return phase > 1000 ? 2000 - phase : phase;
}

const WHEEL = [ [255, 0, 0], [255, 255, 0], [0, 255, 0], [0, 255, 255], [0, 0, 255], [255, 0, 255], [255, 0, 0] ];

function rainbow(position) {
	position = position % 6000;
	let segment = int(position / 1000);
	return mix(WHEEL[segment], WHEEL[segment + 1], position % 1000);
}

function load_colour(value) {
	value = clamp(value, 0, 1000);
	return value <= 500 ? mix([0, 180, 80], [255, 180, 0], value * 2) : mix([255, 180, 0], [255, 0, 0], (value - 500) * 2);
}

function temperature_colour(celsius, sensitivity) {
	celsius += (sensitivity - 5) * 2;
	if (celsius <= 35) return [0, 255, 80];
	if (celsius <= 55) return mix([0, 255, 80], [255, 128, 0], (celsius - 35) * 50);
	if (celsius <= 75) return mix([255, 128, 0], [255, 0, 0], (celsius - 55) * 50);
	return [255, 0, 0];
}

function traffic_colour(value) {
	return value <= 500 ? mix([0, 100, 255], [0, 255, 180], value * 2) : mix([0, 255, 180], [255, 0, 180], (value - 500) * 2);
}

function integer(path) {
	let raw = readfile(path);
	raw = raw == null ? null : trim(raw);
	return raw != null && match(raw, /^-?[0-9]+$/) ? int(raw) : null;
}

// Same sensor and validity range as the RC01 status page and fan curve.
function read_temperature(ctx) {
	for (let attempt = 0; attempt < 2; attempt++) {
		if (!ctx.thermal) {
			for (let path in glob(ctx.root + '/sys/class/thermal/thermal_zone*') || [])
				if (index([ 'nss-top-thermal', 'nss_top_thermal' ], trim(readfile(path + '/type') || '')) >= 0)
					ctx.thermal = path;
			if (!ctx.thermal)
				return null;
		}
		let milli = integer(ctx.thermal + '/temp');
		if (milli != null)
			return milli < 1000 || milli > 130000 ? null : int(milli / 1000);
		ctx.thermal = null;
	}
	return null;
}

// Busy share of all CPUs since the previous call, 0..1000; null for the
// first sample or an unreadable /proc/stat.
function read_cpu(ctx) {
	let line = split(readfile(ctx.root + '/proc/stat') || '', '\n')[0];
	let fields = split(trim(line), /[ \t]+/);
	if (fields[0] != 'cpu' || length(fields) < 6) {
		ctx.cpu = null;
		return null;
	}
	let total = 0;
	// guest and guest_nice are already part of user and nice.
	for (let i = 1; i < length(fields) && i <= 8; i++)
		total += int(fields[i]);
	let idle = int(fields[4]) + int(fields[5]);
	let previous = ctx.cpu;
	ctx.cpu = [ total, idle ];
	if (!previous || total <= previous[0] || idle < previous[1])
		return null;
	let busy = (total - previous[0]) - (idle - previous[1]);
	return clamp(busy * 1000 / (total - previous[0]), 0, 1000);
}

// The logical network interface's device when it has one, else the name
// itself as a device (e.g. pppoe-wan).
export function traffic_device(cfg, network) {
	let device = network ? network(cfg.traffic_interface) : null;
	if (type(device) == 'string' && match(device, /^[A-Za-z0-9_.@-]{1,15}$/))
		return device;
	return cfg.traffic_interface;
}

// Combined rx+tx rate as 0..1000 of the sensitivity's full scale; null for
// the first sample, a missing device or a counter reset.
function read_traffic(ctx, cfg, now_ms) {
	let base = ctx.root + '/sys/class/net/' + ctx.device + '/statistics/';
	let rx = integer(base + 'rx_bytes'), tx = integer(base + 'tx_bytes');
	let previous = ctx.traffic;
	ctx.traffic = rx == null || tx == null ? null : [ rx + tx, now_ms ];
	if (!previous || !ctx.traffic)
		return null;
	let bytes = ctx.traffic[0] - previous[0], elapsed = now_ms - previous[1];
	if (bytes < 0 || elapsed <= 0)
		return null;
	let full_scale = 12500000 / cfg.sensitivity;    // bytes per second
	return clamp(bytes * 1000 / elapsed * 1000 / full_scale, 0, 1000);
}

export function create(root, device) {
	let ctx = { root: root || '', device, paths: [], last: [ -1, -1, -1 ], missing: [] };
	for (let name in LEDS) {
		let path = ctx.root + '/sys/class/leds/' + name;
		push(ctx.paths, path);
		if (!stat(path + '/brightness'))
			push(ctx.missing, name);
	}
	return ctx;
}

// Detach any trigger so nothing else drives these three LEDs while the
// service runs. Other LEDs are never touched.
export function claim(ctx) {
	let ok = true;
	for (let path in ctx.paths)
		if (writefile(path + '/trigger', 'none') == null)
			ok = false;
	return ok;
}

// Write only changed channels. A failed channel is not cached, so the next
// frame retries it.
export function show(ctx, rgb) {
	let ok = true;
	for (let i = 0; i < 3; i++) {
		if (rgb[i] == ctx.last[i])
			continue;
		if (writefile(ctx.paths[i] + '/brightness', '' + rgb[i]) == null) {
			ctx.last[i] = -1;
			ok = false;
		} else
			ctx.last[i] = rgb[i];
	}
	return ok;
}

export function off(ctx) {
	ctx.last = [ -1, -1, -1 ];
	return show(ctx, [ 0, 0, 0 ]);
}

function scale(rgb, value) {
	return map(rgb, c => clamp((c * value + 127) / 255, 0, 255));
}

// One frame. `elapsed_ms` is monotonic time since start and drives the
// animation phase; `minute` is the local minute of the day. Telemetry modes
// whose source is missing or not yet sampled show the static primary colour
// and say so in `detail`.
export function frame(ctx, cfg, minute, elapsed_ms) {
	let lit = level(cfg, minute);
	let result = { mode: cfg.mode, level: lit.level, period: lit.period, detail: null, fallback: false,
		delay: IDLE_MS, rgb: [ 0, 0, 0 ] };
	if (lit.level == 0)
		return result;

	let phase = int(elapsed_ms * cfg.speed / 25);
	let value = lit.level, rgb = cfg.color, sample;
	switch (cfg.mode) {
	case 'breathe':
		let low = cfg.minimum_brightness < value ? cfg.minimum_brightness : value;
		value = low + int((value - low) * triangle(phase) / 1000);
		break;
	case 'duotone':
		rgb = mix(cfg.color, cfg.second_color, triangle(phase));
		break;
	case 'rainbow':
		rgb = rainbow(phase * 3);
		break;
	case 'temperature':
		sample = read_temperature(ctx);
		if (sample != null) {
			rgb = temperature_colour(sample, cfg.sensitivity);
			result.detail = sprintf('%d C', sample);
		}
		break;
	case 'cpu':
		sample = read_cpu(ctx);
		if (sample != null) {
			// Sensitivity affects the colour, not the reported CPU busy share.
			rgb = load_colour(clamp(sample * (50 + cfg.sensitivity * 10) / 100, 0, 1000));
			result.detail = sprintf('%d%%', sample / 10);
		}
		break;
	case 'traffic':
		sample = read_traffic(ctx, cfg, elapsed_ms);
		if (sample != null) {
			rgb = traffic_colour(sample);
			result.detail = sprintf('%d%%', sample / 10);
		}
		break;
	}
	if (index([ 'temperature', 'cpu', 'traffic' ], cfg.mode) >= 0 && sample == null) {
		// A rate needs two readings: the first one only sets the baseline.
		let baseline = cfg.mode == 'cpu' ? ctx.cpu : cfg.mode == 'traffic' ? ctx.traffic : null;
		result.fallback = true;
		result.detail = baseline ? 'sampling' : 'source unavailable';
	}
	if (ANIMATED[cfg.mode])
		result.delay = FRAME_MS;
	result.rgb = scale(rgb, value);
	return result;
}
