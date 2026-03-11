// Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("WB Task Checklist Template", {
	refresh(frm) {
		let grid = frm.fields_dict.wb_task_checklist_template_details && frm.fields_dict.wb_task_checklist_template_details.grid;
		if (grid) {
			let df = grid.get_field("verification_field").df;
			df.fieldtype = "Select";
			df.options = "";
			grid.refresh();
		}

		// Show fieldname instead of legacy DocField hash: preload map and set formatter
		var verification_values = (frm.doc.wb_task_checklist_template_details || [])
			.map(function(r) { return r.verification_field; })
			.filter(Boolean);
		if (verification_values.length) {
			frappe.call({
				method: "workboard.utils.get_docfield_fieldnames",
				args: { docfield_names: verification_values },
				callback: function(r) {
					var map = r.message || {};
					if (grid) {
						grid.get_field("verification_field").formatter = function(value) {
							return map[value] || value;
						};
						grid.refresh();
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
	// Handle focus/click in the grid
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
				
				// Safely update the field property for THIS specific row
				// Using the standard frm.set_df_property which is the correct way for row-specific options
				try {
					frm.set_df_property("verification_field", "options", options, frm.doc.name, "wb_task_checklist_template_details", cdn);
				} catch (e) {
					// Fallback if set_df_property fails
					let grid = frm.fields_dict.wb_task_checklist_template_details.grid;
					let field = grid.get_field("verification_field");
					if (field) {
						field.df.options = options;
						grid.refresh_field("verification_field");
					}
				}
			}
		}
	});
}
