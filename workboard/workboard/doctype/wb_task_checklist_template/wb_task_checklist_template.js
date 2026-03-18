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
		let row = locals[cdt][cdn];
		if (row) {
			row.verification_child_table = "";
			row.verification_field = "";
		}
		set_verification_child_table_options(frm, cdt, cdn);
		set_verification_field_options(frm, cdt, cdn);
	},
	verification_child_table: function(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row) row.verification_field = "";
		set_verification_field_options(frm, cdt, cdn);
	},
	form_render: function(frm, cdt, cdn) {
		set_verification_child_table_options(frm, cdt, cdn);
		set_verification_field_options(frm, cdt, cdn);
	},
	on_row_refresh: function(frm, cdt, cdn) {
		if (frm.fields_dict.wb_task_checklist_template_details.grid && frm.fields_dict.wb_task_checklist_template_details.grid.grid_rows_by_docname[cdn]) {
			set_verification_child_table_options(frm, cdt, cdn);
			set_verification_field_options(frm, cdt, cdn);
		}
	}
});

function set_verification_child_table_options(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row || !row.verification_doctype) return;

	frappe.call({
		method: "workboard.utils.get_doctype_table_fields",
		args: { doctype: row.verification_doctype },
		callback: function(r) {
			if (!r.message || !r.message.length) return;
			let options = [""].concat(r.message.map(function(f) { return f.value; }));
			let parent_meta = frappe.meta.get_docfield(cdt, "verification_child_table", frm.doc.name);
			if (parent_meta) parent_meta.options = options;
			let grid = frm.fields_dict.wb_task_checklist_template_details && frm.fields_dict.wb_task_checklist_template_details.grid;
			if (grid) {
				let grid_df = grid.get_field("verification_child_table");
				if (grid_df) grid_df.options = options;
			}
		}
	});
}

function set_verification_field_options(frm, cdt, cdn) {
	let row = locals[cdt][cdn];
	if (!row || !row.verification_doctype) return;

	frappe.call({
		method: "workboard.utils.get_doctype_fields",
		args: {
			doctype: row.verification_doctype,
			child_table_fieldname: row.verification_child_table || null
		},
		callback: function(r) {
			if (r.message && r.message.length) {
				let options = [""].concat(r.message.map(function(f) { return f.value; }));
				
				// 1. Update Frappe's standard metadata for this specific row (works in row expanded view)
				let row_meta = frappe.meta.get_docfield(cdt, "verification_field", cdn);
				if (row_meta) {
					row_meta.options = options;
				}

				// 2. Update Frappe's standard metadata for the grid globally
				let parent_meta = frappe.meta.get_docfield(cdt, "verification_field", frm.doc.name);
				if (parent_meta) {
					parent_meta.options = options;
				}
				
				// 3. Update the actively rendered grid safely
				if (frm.fields_dict.wb_task_checklist_template_details) {
					let grid = frm.fields_dict.wb_task_checklist_template_details.grid;
					if (grid) {
						// Note: grid.get_field returns the docfield dictionary directly
						let grid_df = grid.get_field("verification_field");
						if (grid_df) {
							// No .df here, grid_df IS the df
							grid_df.options = options; 
						}
						
						if (grid.grid_rows) {
							grid.grid_rows.forEach(gr => {
								if (gr.doc.name === cdn) {
									// Also update the specific row instance 
									if (gr.get_field) {
										let row_field = gr.get_field("verification_field");
										if (row_field) {
											// Fallback check depending on Frappe version
											if (row_field.df) {
												row_field.df.options = options;
											} else {
												row_field.options = options;
											}
											if (typeof row_field.refresh === "function") {
												row_field.refresh();
											}
										}
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
