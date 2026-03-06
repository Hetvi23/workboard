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
		frappe.db.get_value(
			"WB Task",
			frm.doc.wb_task_reference,
			["priority", "status", "due_date", "task_type", "depends_on_time", "end_datetime", "description", "assign_from", "assign_to", "reference_doctype", "reference_document"],
			(r) => {
				if (!r) return;
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
					reference_doctype: r.reference_doctype,
					reference_document: r.reference_document,
				});
			}
		);
	},
});
