// Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("WB Task Checklist Template", {
	refresh(frm) {
		// Filter Verification Field by selected Verification Doctype (DocField.parent = doctype)
		var grid = frm.fields_dict.wb_task_checklist_template_details && frm.fields_dict.wb_task_checklist_template_details.grid;
		if (grid && grid.get_field("verification_field")) {
			grid.get_field("verification_field").get_query = function(doc, cdt, cdn) {
				var row = locals[cdt][cdn];
				if (row && row.verification_doctype) {
					return { filters: [["parent", "=", row.verification_doctype]] };
				}
			};
		}
	}
});
