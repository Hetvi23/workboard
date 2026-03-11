// Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("WB Task Checklist Template", {
	refresh(frm) {
		let table_field = "wb_task_checklist_template_details";
		let fieldname = "verification_field";

		if (frm.fields_dict && frm.fields_dict[table_field] && frm.fields_dict[table_field].grid) {
			let grid = frm.fields_dict[table_field].grid;
			let field = grid.get_field(fieldname);
			if (field && field.df) {
				field.df.fieldtype = "Select";
				field.df.focus_select = true;
				grid.refresh();
			}
		}

		// Show fieldname instead of legacy DocField hash: preload map and set formatter
		let verification_values = (frm.doc[table_field] || [])
			.map(function(r) { return r[fieldname]; })
			.filter(Boolean);

		if (verification_values.length) {
			frappe.call({
				method: "workboard.utils.get_docfield_fieldnames",
				args: { docfield_names: verification_values },
				callback: function(r) {
					let map = r.message || {};
					let grid = frm.fields_dict[table_field] && frm.fields_dict[table_field].grid;
					if (grid) {
						let field = grid.get_field(fieldname);
						if (field) {
							field.formatter = function(value) {
								return map[value] || value;
							};
							grid.refresh();
						}
					}
				}
			});
		}
	}
});

frappe.ui.form.on("WB Task Checklist Template Details", {
	verification_doctype: function(frm, cdt, cdn) {
		set_verification_field_options(frm, cdt, cdn);
	},
	form_render: function(frm, cdt, cdn) {
		set_verification_field_options(frm, cdt, cdn);
	},
	on_row_refresh: function(frm, cdt, cdn) {
		set_verification_field_options(frm, cdt, cdn);
	},
	// Handle interaction in the grid
	verification_field: function(frm, cdt, cdn) {
		set_verification_field_options(frm, cdt, cdn);
	}
});

function set_verification_field_options(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row || !row.verification_doctype) return;

	frappe.call({
		method: "workboard.utils.get_doctype_fields",
		args: { doctype: row.verification_doctype },
		callback: function(r) {
			if (r.message && r.message.length) {
				let options = [""].concat(r.message.map(function(f) { return f.value; }));
				
				// Standard way to set row-specific options
				try {
					if (frm && frm.set_df_property) {
						frm.set_df_property("verification_field", "options", options, frm.doc.name, "wb_task_checklist_template_details", cdn);
					}
				} catch (e) {
					console.log("Error setting options via frm.set_df_property", e);
				}
			}
		}
	});
}
