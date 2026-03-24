// Copyright (c) 2026, Nesscale Solutions Pvt Ltd and contributors
// For license information, please see license.txt

frappe.ui.form.on("WB Task Extension", {
	wb_task_reference(frm) {
		if (frm.doc.wb_task_reference && !frm.doc.priority) {
			frm.trigger("fetch_from_wb_task");
		}
	},
	fetch_from_wb_task(frm) {
		if (!frm.doc.wb_task_reference) return;
		// Read only fields that exist on this site's WB Task schema.
		// Cruzine often uses custom_reference_* while some setups may have reference_*.
		var ref_fields = ["priority", "status", "due_date", "task_type", "depends_on_time", "end_datetime", "description", "assign_from", "assign_to"];
		var has_custom_ref = frappe.meta.has_field("WB Task", "custom_reference_doctype");
		var has_std_ref = frappe.meta.has_field("WB Task", "reference_doctype");
		if (has_custom_ref) {
			ref_fields.push("custom_reference_doctype", "custom_reference_document");
		}
		if (has_std_ref) {
			ref_fields.push("reference_doctype", "reference_document");
		}
		frappe.db.get_value(
			"WB Task",
			frm.doc.wb_task_reference,
			ref_fields,
			(r) => {
				if (!r) return;
				var ref_doctype = r.custom_reference_doctype || r.reference_doctype || "";
				var ref_doc = r.custom_reference_document || r.reference_document || "";
				frm.set_value({
					priority: r.priority,
					status: r.status,
					due_date: r.due_date,
					task_type: r.task_type,
					depends_on_time: r.depends_on_time,
					end_datetime: r.end_datetime,
					description: r.description,
					assign_from: r.assign_from,
					assign_to: r.assign_to,
					reference_doctype: ref_doctype,
					reference_document: ref_doc,
				});
			}
		);
	},
});
