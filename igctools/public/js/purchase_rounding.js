/* Monetary previews use the same server calculator as saves. No global flt,
 * precision, currency handler or shared DocField metadata is replaced. */
(() => {
	if (window.igc_purchase_rounding_v1) return;
	window.igc_purchase_rounding_v1 = true;
	const states = new WeakMap();
	const method = "igctools.purchase_rounding.api.preview";
	const doctypes = ["Supplier Quotation", "Purchase Order", "Purchase Receipt", "Purchase Invoice"];

	function state(frm) {
		if (!states.has(frm)) states.set(frm, { sequence: 0, originals: new Map(), timer: null, applying: false });
		return states.get(frm);
	}

	function restore_precision(frm) {
		const s = state(frm);
		for (const [key, value] of s.originals) {
			const [table, field] = key.split(":");
			if (table) frm.fields_dict[table]?.grid?.update_docfield_property(field, "precision", value);
			else frm.set_df_property(field, "precision", value);
		}
		s.originals.clear();
	}

	function set_precision(frm, table, values) {
		const s = state(frm);
		for (const [field, precision] of Object.entries(values || {})) {
			const grid = table ? frm.fields_dict[table]?.grid : null;
			const df = table ? grid?.docfields?.find(d => d.fieldname === field) : frm.fields_dict[field]?.df;
			if (!df) continue;
			// Keep editable input precision native, particularly manual Actual
			// taxes during exchange-rate callbacks. Only format calculated outputs.
			if (!df.read_only || (table === "taxes" && field === "tax_amount")) continue;
			const key = `${table}:${field}`;
			if (!s.originals.has(key)) s.originals.set(key, df.precision);
			if (df.precision === precision) continue;
			if (table) grid.update_docfield_property(field, "precision", precision);
			else frm.set_df_property(field, "precision", precision);
		}
	}

	function apply(frm, result) {
		const s = state(frm);
		s.applying = true;
		try {
			restore_precision(frm);
			if (!result?.enabled) return;
			set_precision(frm, "", result.precision);
			const editable = result.editable && frm.doc.docstatus === 0;
			if (editable) Object.assign(frm.doc, result.values);
			for (const [table, rows] of Object.entries(result.tables)) {
				for (const row of rows) {
					set_precision(frm, table, row.precision);
					if (!editable) continue;
					const target = (frm.doc[table] || []).find(d => d.name === row.name);
					if (!target) continue;
					Object.assign(target, row.values);
					if (table === "taxes") target.item_wise_tax_detail = row.item_wise_tax_detail;
				}
				frm.refresh_field(table);
			}
			if (editable && result.other_charges_calculation !== undefined) {
				frm.doc.other_charges_calculation = result.other_charges_calculation;
				frm.refresh_field("other_charges_calculation");
			}
			for (const field of Object.keys(result.values)) frm.refresh_field(field);
		} finally {
			s.applying = false;
		}
	}

	async function preview(frm, require_current = false) {
		const s = state(frm);
		clearTimeout(s.timer);
		if (!frm.doc.company || !frm.doc.currency) return;
		for (let attempt = 0; attempt < (require_current ? 3 : 1); attempt++) {
			const document = JSON.stringify(frm.doc);
			const sequence = ++s.sequence;
			const response = await frappe.call({ method, args: { document }, quiet: true });
			if (sequence !== s.sequence) {
				if (require_current) continue;
				return;
			}
			if (document !== JSON.stringify(frm.doc)) {
				if (require_current) continue;
				schedule(frm);
				return;
			}
			apply(frm, response.message);
			return;
		}
		if (require_current) frappe.throw(__("Wait for the purchase calculation to finish, then save again."));
	}

	function schedule(frm) {
		const s = state(frm);
		if (s.applying) return;
		clearTimeout(s.timer);
		s.timer = setTimeout(() => preview(frm).catch(() => {
			frappe.show_alert({ message: __("Purchase rounding preview could not finish. Saving will retry the calculation."), indicator: "orange" });
		}), 180);
	}

	function install(frm) {
		const controller = frm.cscript;
		const original = controller?.calculate_taxes_and_totals;
		if (!original || original.igc_purchase_rounding) return;
		function wrapped(...args) {
			// Native preview must run with native precision, including on a
			// currency switch. The authoritative result restores display precision.
			restore_precision(frm);
			const result = original.apply(this, args);
			if (!state(frm).applying && frm.doc.docstatus === 0) schedule(frm);
			return result;
		}
		wrapped.igc_purchase_rounding = true;
		controller.calculate_taxes_and_totals = wrapped;
	}

	for (const doctype of doctypes) {
		frappe.ui.form.on(doctype, {
			setup: install,
			onload(frm) { install(frm); schedule(frm); },
			refresh(frm) { install(frm); schedule(frm); },
			currency: schedule,
			supplier: schedule,
			custom_igc_use_native_rounding(frm) { restore_precision(frm); frm.cscript.calculate_taxes_and_totals(); schedule(frm); },
			before_save(frm) { return preview(frm, true); },
		});
	}
})();
