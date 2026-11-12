/* Run the production page's options through the pinned LuCI form parser
 * and validator. Mapping, DOM widgets and UCI storage are fixture objects;
 * no browser session, RPC, network command or real UCI file is accessed. */
'use strict';
const fs = require('fs');
String.prototype.format = function(...args) {
    let index = 0;
    return this.replace(/%[sd]/g, () => String(args[index++]));
};
global._ = value => value;
global.findParent = () => true;
global.CustomEvent = class { constructor(name, options) { this.name = name; this.detail = options?.detail; } };
global.L = {
    bind: (fn, context, ...args) => fn.bind(context, ...args),
    isObject: value => value != null && typeof value === 'object',
    raise: (type, message) => { throw new Error(message); }
};
function extend(properties) {
    const parent = typeof this === 'function' ? this.prototype : null;
    function Class(...args) { this.__init__?.(...args); }
    Class.prototype = Object.assign(Object.create(parent), properties);
    Class.extend = extend;
    return Class;
}
const baseclass = { extend };
const Validation = new Function('baseclass', fs.readFileSync(process.argv[4], 'utf8'))(baseclass);
const validation = new Validation();
const Form = new Function('ui', 'uci', 'rpc', 'dom', 'baseclass', fs.readFileSync(process.argv[3], 'utf8'))(
    { tabs: { updateTabs() {} } },
    { loadPackage: () => Promise.resolve() }, { declare: () => () => Promise.resolve({}) },
    { elem: () => false }, baseclass);
const realForm = Object.create(Form.prototype);
const E = (tag, attrs, children) => ({ tag, attrs, children });

async function check(test) {
    const original = { enabled: '1', ipv4: '1', ipv6: '1', ipv4_target: ['203.0.113.1'],
        ipv6_target: ['2001:db8::1'], ipv4_interface: 'auto', ipv6_interface: 'auto',
        interval: '30', timeout: '2', fail_count: '3', recover_count: '2' };
    const values = { ...original, ...test.values };
    const saved = { ...original };
    const options = {};
    const removed = [];
    class Map {
        constructor(config) { this.config = config; }
        lookupOption(name, section) { return options[name] ? [options[name], section] : null; }
        isDependencySatisfied(...args) { return realForm.Map.prototype.isDependencySatisfied.apply(this, args); }
        section() {
            const section = { map: this, children: [], cfgsections: () => ['main'] };
            section.option = (type, name, title) => {
                const option = Object.create(type.prototype);
                option.super = () => {};
                realForm.AbstractValue.prototype.__init__.call(option, this, section, name);
                Object.assign(option, { title, enabled: '1', disabled: '0', keylist: [], vallist: [] });
                option.cfgvalue = () => original[name];
                option.stripTags = text => text;
                option.write = (id, value) => { saved[name] = value; };
                option.remove = () => { delete saved[name]; removed.push(name); };
                option.isActive = id => option.checkDepends(id);
                const widget = {
                    getValue: () => values[name] ?? option.default,
                    isChecked: () => values[name] === '1',
                    getValidationError: () => widget.error || '',
                    setInvalid: message => { widget.error = message; },
                    isValid: () => {
                        const current = option.formvalue('main');
                        const items = Array.isArray(current) ? current : [current];
                        widget.error = '';
                        // An empty DynamicList has no item validator. Its
                        // mandatory constraint must still hold in form.parse.
                        return items.every(value => {
                            const field = { value: value ?? '', classList: { add() {}, remove() {} },
                                setAttribute() {}, removeAttribute() {}, dispatchEvent() {} };
                            const validator = validation.create(field, option.datatype || 'string',
                                option.optional || option.rmempty, option.getValidator('main'));
                            const valid = validator.validate();
                            if (!valid) widget.error = validator.error;
                            return valid;
                        });
                    }
                };
                option.getUIElement = () => widget;
                options[name] = option;
                section.children.push(option);
                return option;
            };
            return section;
        }
        render() { return Promise.resolve({}); }
    }
    const page = new Function('view', 'form', 'rpc', 'poll', 'dom', 'network', 'E', '_',
        fs.readFileSync(process.argv[2], 'utf8'))({ extend: value => value }, { ...Form.prototype, Map },
        { declare: () => () => Promise.resolve({}) }, { add() {} }, {}, {}, E, value => value);
    await page.render([null, []]);
    const results = await Promise.allSettled(Object.values(options).map(option => option.parse('main')));
    return { name: test.name, accepted: results.every(result => result.status === 'fulfilled'),
        errors: results.filter(result => result.status === 'rejected').map(result => String(result.reason)),
        saved, removed };
}
Promise.all(JSON.parse(fs.readFileSync(0, 'utf8')).map(check))
    .then(results => process.stdout.write(JSON.stringify(results)))
    .catch(error => { console.error(error); process.exitCode = 1; });
