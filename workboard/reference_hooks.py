# Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
# For license information, please see license.txt

"""Cancel WB Tasks when their reference document is cancelled (e.g. Quotation)."""

import frappe


def ignore_wb_task_links_on_quotation_cancel(doc, method=None):
	ignored = set(doc.get("ignore_linked_doctypes") or [])
	ignored.add("WB Task")
	doc.ignore_linked_doctypes = list(ignored)


def cancel_linked_wb_tasks_on_quotation_cancel(doc, method=None):
	"""Doc event: Quotation on_cancel — cancel WB Tasks linked to this quotation."""
	cancel_wb_tasks_for_reference("Quotation", doc.name)


def cancel_wb_tasks_for_reference(reference_doctype, reference_name):
	"""
	Find WB Tasks pointing at reference_doctype + reference_name and set status to Cancelled.
	Uses custom_reference_* (e.g. Cruzine) and/or reference_doctype / reference_document if columns exist.
	Does not block the caller if a task save fails (errors are logged).
	"""
	if not reference_doctype or not reference_name:
		return

	names = set()

	if frappe.db.has_column("WB Task", "custom_reference_doctype"):
		for name in frappe.get_all(
			"WB Task",
			filters={
				"custom_reference_doctype": reference_doctype,
				"custom_reference_document": reference_name,
			},
			pluck="name",
		):
			names.add(name)

	if frappe.db.has_column("WB Task", "reference_doctype"):
		for name in frappe.get_all(
			"WB Task",
			filters={
				"reference_doctype": reference_doctype,
				"reference_document": reference_name,
			},
			pluck="name",
		):
			names.add(name)

	for name in names:
		try:
			task = frappe.get_doc("WB Task", name)
			# Match manual cancel_task: only Open or Overdue are cancellable
			if task.status not in ("Open", "Overdue"):
				continue
			task.status = "Cancelled"
			task.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(
				title=f"WB Task auto-cancel failed ({reference_doctype} {reference_name})",
				message=frappe.get_traceback(),
			)
