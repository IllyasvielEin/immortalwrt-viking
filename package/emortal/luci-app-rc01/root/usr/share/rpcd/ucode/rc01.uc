// SPDX-License-Identifier: GPL-2.0-only
import { cursor } from 'uci';
import { snapshot, connectivity } from 'rc01.platform';

// rpcd keeps this plugin loaded, so the cache keeps a short sample history.
// It holds diagnostics only, never settings. Both methods only read.
let cache = {};

return {
	'rc01': {
		status: { call: function() { return snapshot(cursor(), cache); } },
		connectivity: { call: function() { return connectivity(cursor()); } }
	}
};
