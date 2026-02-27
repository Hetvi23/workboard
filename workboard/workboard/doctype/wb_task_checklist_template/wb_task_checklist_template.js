// Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("WB Task Checklist Template", {
	refresh(frm) {
		var grid = frm.fields_dict.wb_task_checklist_template_details && frm.fields_dict.wb_task_checklist_template_details.grid;
		if (!grid || !grid.get_field("verification_field")) return;

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
					grid.get_field("verification_field").formatter = function(value) {
						return map[value] || value;
					};
					grid.refresh();
				}
			});
		} else {
			grid.get_field("verification_field").formatter = null;
		}

		// Open field selector dialog when user focuses Verification Field (if doctype set)
		grid.form.on("verification_field", "focus", function() {
			var row = grid.form.get_selected_row();
			if (!row || !row.verification_doctype) return;
			open_verification_field_dialog(frm, grid, row);
		});
	}
});

function open_verification_field_dialog(frm, grid, row) {
	frappe.call({
		method: "workboard.utils.get_doctype_fields",
		args: { doctype: row.verification_doctype },
		callback: function(r) {
			var options = r.message || [];
			if (!options.length) {
				frappe.msgprint(__("No fields found for {0}", [row.verification_doctype]));
				return;
			}
			var d = new frappe.ui.Dialog({
				title: __("Select Verification Field"),
				fields: [{
					fieldtype: "Select",
					fieldname: "fieldname",
					label: __("Field"),
					options: options.map(function(o) { return o.label + " (" + o.value + ")"; }),
					onchange: function() {
						var val = this.value;
						if (val) {
							var fieldname = val.replace(/^.*\(([^)]+)\)$/, "$1");
							row.verification_field = fieldname;
							grid.refresh();
							frm.dirty();
							d.hide();
						}
					}
				}]
			});
			d.show();
		}
	});
}
