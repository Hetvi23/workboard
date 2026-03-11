// Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("WB Task Checklist Template", {
	refresh(frm) {
		// Show fieldname instead of legacy DocField hash: preload map and set formatter
		let verification_values = (frm.doc.wb_task_checklist_template_details || [])
			.map(function(r) { return r.verification_field; })
			.filter(Boolean);

		if (verification_values.length) {
			frappe.call({
				method: "workboard.utils.get_docfield_fieldnames",
				args: { docfield_names: verification_values },
				callback: function(r) {
					let map = r.message || {};
					let grid = frm.fields_dict.wb_task_checklist_template_details && frm.fields_dict.wb_task_checklist_template_details.grid;
					if (grid) {
						let field = grid.get_field("verification_field");
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
		// Only update options if this row is currently being edited to avoid overwriting other rows' options globally
		if (frm.fields_dict.wb_task_checklist_template_details.grid && frm.fields_dict.wb_task_checklist_template_details.grid.grid_rows_by_docname[cdn]) {
			set_verification_field_options(frm, cdt, cdn);
		}
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
				
				let df = frappe.meta.get_docfield(cdt, "verification_field", frm.doc.name);
				if (df) {
					df.options = options;
				}
				
				if (frm.fields_dict.wb_task_checklist_template_details) {
					let grid = frm.fields_dict.wb_task_checklist_template_details.grid;
					if (grid) {
						let field = grid.get_field("verification_field");
						if (field) {
							field.df.options = options;
						}
						
						if (grid.grid_rows) {
							grid.grid_rows.forEach(gr => {
								if (gr.doc.name === cdn) {
									let row_field = gr.get_field("verification_field");
									if (row_field) {
										row_field.df.options = options;
										row_field.refresh();
									}
								}
							});
						}
					}
				}
			}
		}
	});
}
