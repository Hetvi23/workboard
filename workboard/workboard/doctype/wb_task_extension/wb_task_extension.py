# Copyright (c) 2026, Nesscale Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class WBTaskExtension(Document):
	def validate(self):
		if self.wb_task_reference and self.new_due_date:
			# Optional: validate new_due_date is not before current due_date if needed
			pass


def update_wb_task_on_extension_submit(doc, method=None):
	"""
	When a WB Task Extension is submitted, update the linked WB Task with the latest
	submitted extension's New Due Date and New End Date & Time.
	If multiple extensions exist, use the most recently submitted one.
	"""
	if not doc.wb_task_reference or doc.docstatus != 1:
		return
	task_name = doc.wb_task_reference
	# Get the latest submitted extension for this WB Task (by modified desc)
	latest = frappe.db.get_all(
		"WB Task Extension",
		filters={"wb_task_reference": task_name, "docstatus": 1},
		fields=["new_due_date", "new_end_datetime"],
		order_by="modified desc",
		limit=1,
	)
	if not latest:
		return
	row = latest[0]
	update_fields = {}
	if row.get("new_due_date") is not None:
		update_fields["due_date"] = row["new_due_date"]
		update_fields["new_due_date"] = row["new_due_date"]
	if row.get("new_end_datetime") is not None:
		update_fields["end_datetime"] = row["new_end_datetime"]
		update_fields["new_end_datetime"] = row["new_end_datetime"]
	if not update_fields:
		return
	# Use db_set to avoid triggering validations and recursion
	for key, value in update_fields.items():
		if frappe.db.has_column("WB Task", key):
			frappe.db.set_value("WB Task", task_name, key, value, update_modified=True)
	frappe.db.commit()
	frappe.clear_cache(doctype="WB Task")
