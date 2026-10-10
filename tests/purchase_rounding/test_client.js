const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");
const source = fs.readFileSync(path.join(__dirname, "../../igctools/public/js/purchase_rounding.js"), "utf8");

function setup() {
	const handlers = {}, pending = [], timers = new Map();
	let timer = 0;
	const native_currency = () => "actual-tax-handler";
	const total_df = { precision: 4, read_only: 1 };
	const tax_df = { fieldname: "tax_amount", precision: 4, read_only: 0 };
	const frm = {
		doc: { doctype: "Purchase Invoice", name: "new", docstatus: 0, company: "Test", currency: "TST", items: [], taxes: [{ name: "tax", tax_amount: 7.1234 }] },
		fields_dict: {
			grand_total: { df: total_df },
			taxes: { grid: { docfields: [tax_df], update_docfield_property(field, property, value) { tax_df[property] = value; } } },
		},
		cscript: { currency: native_currency, calculate_taxes_and_totals() { assert.equal(total_df.precision, 4); return "native-result"; } },
		set_df_property(field, property, value) { this.fields_dict[field].df[property] = value; },
		refresh_field() {},
	};
	vm.runInNewContext(source, {
		window: {}, WeakMap, Map, Object, JSON, __: s => s,
		setTimeout(fn) { timers.set(++timer, fn); return timer; },
		clearTimeout(id) { timers.delete(id); },
		frappe: {
			ui: { form: { on(dt, events) { handlers[dt] = events; } } },
			call(args) { return new Promise((resolve, reject) => pending.push({ args, resolve, reject })); },
			throw(message) { throw new Error(message); }, show_alert() {},
		},
	});
	const events = handlers[frm.doc.doctype];
	events.setup(frm);
	const response = (overrides = {}) => ({ message: {
		enabled: true, editable: true, policy: {}, precision: { grand_total: 2 }, values: { grand_total: 1395 },
		tables: { taxes: [{ name: "tax", values: { tax_amount: 7.12 }, precision: { tax_amount: 2 } }] }, ...overrides,
	} });
	return { frm, events, pending, timers, total_df, tax_df, response, native_currency };
}

test("preserves existing Actual-tax currency handler and native return value", () => {
	const s = setup();
	assert.equal(s.frm.cscript.currency, s.native_currency);
	assert.equal(s.frm.cscript.calculate_taxes_and_totals(), "native-result");
	assert.equal(s.timers.size, 1);
	s.events.setup(s.frm);
	assert.equal(s.frm.cscript.currency, s.native_currency);
});

test("save awaits authoritative preview and keeps editable Actual precision native", async () => {
	const s = setup();
	const promise = s.events.before_save(s.frm);
	assert.equal(s.pending.length, 1);
	s.pending[0].resolve(s.response());
	await promise;
	assert.equal(s.frm.doc.grand_total, 1395);
	assert.equal(s.total_df.precision, 2);
	assert.equal(s.tax_df.precision, 4);
	assert.equal(s.frm.cscript.calculate_taxes_and_totals(), "native-result");
	assert.equal(s.total_df.precision, 4);
});

test("currency change while response is in flight retries without applying stale result", async () => {
	const s = setup();
	const promise = s.events.before_save(s.frm);
	s.frm.doc.currency = "USD";
	s.pending[0].resolve(s.response());
	await new Promise(resolve => setImmediate(resolve));
	assert.equal(s.pending.length, 2);
	assert.equal(s.frm.doc.grand_total, undefined);
	s.pending[1].resolve(s.response({ enabled: false }));
	await promise;
	assert.equal(s.frm.doc.grand_total, undefined);
	assert.equal(s.frm.doc.taxes[0].tax_amount, 7.1234);
	assert.equal(s.total_df.precision, 4);
});

test("submitted documents receive formatting only", async () => {
	const s = setup();
	s.frm.doc.docstatus = 1;
	s.frm.doc.grand_total = 777;
	const promise = s.events.before_save(s.frm);
	s.pending[0].resolve(s.response({ editable: false }));
	await promise;
	assert.equal(s.frm.doc.grand_total, 777);
	assert.equal(s.frm.doc.taxes[0].tax_amount, 7.1234);
	assert.equal(s.total_df.precision, 2);
});

test("preview errors are propagated to the awaited save", async () => {
	const s = setup();
	const promise = s.events.before_save(s.frm);
	s.pending[0].reject(new Error("server validation"));
	await assert.rejects(promise, /server validation/);
	assert.equal(s.frm.doc.grand_total, undefined);
});
