// SPDX-License-Identifier: GPL-2.0-only
import { cursor } from 'uci';
import { snapshot } from 'rc01.platform';

// rpcd keeps this plugin loaded, so the cache bounds expensive kernel views
// and keeps a short sample history. It holds diagnostics only, never settings.
let cache = {};

return {
	'rc01': {
		status: { call: function() { return snapshot(cursor(), cache); } }
	}
};
