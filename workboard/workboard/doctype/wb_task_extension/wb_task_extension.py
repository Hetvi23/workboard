# Copyright (c) 2026, Nesscale Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, date_diff, time_diff_in_seconds, add_to_date


class WBTaskExtension(Document):
	def validate(self):
		if not self.wb_task_reference:
			return
			
		task_doc = frappe.get_cached_doc("WB Task", self.wb_task_reference)
		
		# Validation for Max Double rule
		if int(task_doc.depends_on_time or 0):
			if self.new_end_datetime and task_doc.end_datetime:
				new_end = get_datetime(self.new_end_datetime)
				curr_end = get_datetime(self.end_datetime or task_doc.end_datetime) # use the end_datetime fetched in Extension doc
				
				# Get duration in minutes
				duration_mins = task_doc.time_limit_in_minutes or task_doc.task_time_duration_minutes or 0
				if not duration_mins:
					# Fallback to current end_datetime - creation
					duration_mins = time_diff_in_seconds(curr_end, get_datetime(task_doc.creation)) / 60
				
				# Max double means we can add at most duration_mins to the CURRENT deadline
				max_end = add_to_date(curr_end, minutes=duration_mins)
				
				if new_end > max_end:
					frappe.throw(_("New End Date & Time cannot exceed {0} (Maximum double of original duration allowed)").format(max_end.strftime('%d-%m-%Y %I:%M %p')))
		else:
			if self.new_due_date and task_doc.due_date:
				new_due = getdate(self.new_due_date)
				curr_due = getdate(self.due_date or task_doc.due_date)
				
				# Get duration in days
				due_days = task_doc.due_days or 0
				if not due_days and task_doc.wb_task_rule:
					due_days = frappe.db.get_value("WB Task Rule", task_doc.wb_task_rule, "due_days") or 0
				
				if not due_days:
					due_days = date_diff(curr_due, getdate(task_doc.creation))
				
				max_due = add_to_date(curr_due, days=due_days)
				
				if new_due > max_due:
					frappe.throw(_("New Due Date cannot exceed {0} (Maximum double of original duration allowed)").format(max_due.strftime('%d-%m-%Y')))


def _is_extension_approved(doc) -> bool:
	if getattr(doc, "docstatus", None) == 1:
		return True
	wf_state = (doc.get("workflow_state") or "").strip().lower()
	return wf_state == "approved"


def update_wb_task_on_extension_submit(doc, method=None):
	"""
	When a WB Task Extension is submitted, update the linked WB Task with the latest
	submitted extension's New Due Date and New End Date & Time.
	If multiple extensions exist, use the most recently submitted one.
	"""
	if not doc.wb_task_reference:
		return

	if not _is_extension_approved(doc):
		return
	task_name = doc.wb_task_reference

	row = None
	if doc.docstatus == 1:
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
	else:
		# Workflow approval can happen without submit; use current doc values.
		row = {"new_due_date": doc.get("new_due_date"), "new_end_datetime": doc.get("new_end_datetime")}

	update_fields = {"status": "Extended"}
	if row.get("new_due_date") is not None:
		update_fields["new_due_date"] = row["new_due_date"]
	if row.get("new_end_datetime") is not None:
		update_fields["new_end_datetime"] = row["new_end_datetime"]
	if not update_fields:
		return
	# Use db_set to avoid triggering validations and recursion
	for key, value in update_fields.items():
		if frappe.db.has_column("WB Task", key):
			frappe.db.set_value("WB Task", task_name, key, value, update_modified=True)
	frappe.db.commit()
	frappe.clear_cache(doctype="WB Task")
