// SPDX-License-Identifier: GPL-2.0-only
import { cursor } from 'uci';
import { snapshot } from 'rc01.platform';

return {
	'rc01': {
		status: { call: function() { return snapshot(cursor()); } }
	}
};
