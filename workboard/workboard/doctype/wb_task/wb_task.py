# Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import re
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime, nowdate, flt, cstr

from workboard.utils import get_workboard_settings


def _resolve_verification_fieldname(verification_field):
	"""
	Resolve verification_field to the actual DB fieldname.
	Stored value may be a DocField name (hash) or the fieldname itself.
	"""
	if not verification_field:
		return None
	# If it's a DocField name (e.g. hash like 1c10g6u8qn), get fieldname from DocField
	fieldname = frappe.db.get_value("DocField", verification_field, "fieldname")
	if fieldname:
		return fieldname
	# Backward compat: value might already be fieldname (e.g. after migration)
	return verification_field


def _check_filter_match(actual, expected_value, filter_type):
	"""Return True if actual value matches expected per filter_type."""
	if expected_value is None:
		expected_value = ""
	expected_value = cstr(expected_value).strip()
	actual_str = cstr(actual).strip() if actual is not None else ""

	if filter_type == "Equals":
		return actual_str == expected_value
	if filter_type == "Not Equals":
		return actual_str != expected_value
	if filter_type == "Like":
		if not expected_value:
			return False
		pat = re.escape(expected_value).replace("%", ".*").replace("_", ".")
		return bool(re.match("^" + pat + "$", actual_str, re.IGNORECASE))
	if filter_type == "Not Like":
		if not expected_value:
			return True
		pat = re.escape(expected_value).replace("%", ".*").replace("_", ".")
		return not re.match("^" + pat + "$", actual_str, re.IGNORECASE)
	if filter_type == "In":
		if not expected_value:
			return False
		vals = [v.strip() for v in expected_value.split(",") if v.strip()]
		return actual_str in vals
	if filter_type == "Not In":
		if not expected_value:
			return True
		vals = [v.strip() for v in expected_value.split(",") if v.strip()]
		return actual_str not in vals
	if filter_type == "Is":
		# Is Empty / Is Not Empty
		if expected_value and expected_value.lower() in ("empty", "null", "not set"):
			return not actual_str
		return bool(actual_str)
	# Numeric
	try:
		actual_num = flt(actual)
		expected_num = flt(expected_value)
	except (TypeError, ValueError):
		return actual_str == expected_value
	if filter_type == "Greater Than (when number)":
		return actual_num > expected_num
	if filter_type == "Greater Than or Equals To (when number)":
		return actual_num >= expected_num
	if filter_type == "Less Than (when number)":
		return actual_num < expected_num
	if filter_type == "Less Than or Equals To (when number)":
		return actual_num <= expected_num
	# Date (simplified: compare as dates if possible)
	try:
		actual_d = getdate(actual) if actual else None
		expected_d = getdate(expected_value) if expected_value else None
	except Exception:
		return actual_str == expected_value
	if filter_type == "After (when date)":
		return actual_d and expected_d and actual_d > expected_d
	if filter_type == "Before (when date)":
		return actual_d and expected_d and actual_d < expected_d
	if filter_type == "On or After (when date)":
		return actual_d and expected_d and actual_d >= expected_d
	if filter_type == "On or Before (when date)":
		return actual_d and expected_d and actual_d <= expected_d
	# Default: equals
	return actual_str == expected_value


class WBTask(Document):
	def before_validate(self):
		if not self.assign_from:
			self.assign_from = frappe.session.user

	def validate(self):
		if self.status not in ("Open", "Done", "Completed", "Overdue", "Cancelled"):
			frappe.throw(_("Invalid Status"))
		if self.status in ("Done", "Completed"):
			if not (self.proof_of_work and self.proof_of_work.strip()):
				frappe.throw(_("Proof of Work is required when status is Done or Completed"))
			if not (self.task_completion_remark and self.task_completion_remark.strip()):
				frappe.throw(_("Task Completion Remark is required when status is Done or Completed"))
		self.validate_overdue()
		self.validate_checklist_verification()
		self.enforce_checklist()
		self.stamp_completion()

	def validate_overdue(self):
		if not self.due_date or self.status in ("Done", "Completed"):
			return
		today = getdate(nowdate())
		due = getdate(self.due_date)
		if self.status in ("Open", "In Progress") and due < today:
			self.status = "Overdue"
		if self.status == "Overdue" and due >= today:
			self.status = "Open"

	def enforce_checklist(self):
		if not int(self.has_checklist or 0):
			return
		rows = self.get("wb_task_checklist_details") or []
		if not rows:
			frappe.throw(_("Checklist is required"))
		all_done = all(bool(getattr(r, "completed", 0)) for r in rows)
		if self.status in ("Done", "Completed") and not all_done:
			frappe.throw(_("Complete all checklist items before marking as Done or Completed"))
		if all_done and self.status in ("Open", "In Progress", "Overdue"):
			self.status = "Done" if self.task_type == "Manual" else "Completed"

	def validate_checklist_verification(self):
		"""When a checklist row is marked completed, verify source document has acceptable value if template has verification config."""
		if not int(self.has_checklist or 0) or not self.checklist_template:
			return
		ref_doctype = getattr(self, "custom_reference_doctype", None) or getattr(self, "reference_doctype", None)
		ref_name = getattr(self, "custom_reference_document", None) or getattr(self, "reference_document", None)
		if not ref_doctype or not ref_name:
			return
		if not frappe.db.exists(ref_doctype, ref_name):
			return
		checklist_doc = frappe.get_cached_doc("WB Task Checklist Template", self.checklist_template)
		template_rows = list(checklist_doc.wb_task_checklist_template_details or [])
		task_rows = self.get("wb_task_checklist_details") or []
		for i, task_row in enumerate(task_rows):
			if not int(getattr(task_row, "completed", 0)):
				continue
			if i >= len(template_rows):
				continue
			tmpl = template_rows[i]
			verification_doctype = getattr(tmpl, "verification_doctype", None)
			verification_field = getattr(tmpl, "verification_field", None)
			filter_type = getattr(tmpl, "filter_type", None)
			expected_value = getattr(tmpl, "value", None)
			if not verification_doctype or not verification_field or not filter_type:
				continue
			fieldname = _resolve_verification_fieldname(verification_field)
			if not fieldname:
				continue
			if verification_doctype != ref_doctype:
				continue
			if not frappe.db.has_column(ref_doctype, fieldname):
				continue
			actual = frappe.db.get_value(ref_doctype, ref_name, fieldname)
			if not _check_filter_match(actual, expected_value, filter_type):
				point_no = i + 1
				frappe.throw(
					_("Task checklist point no {0} is not done. Kindly clear it.").format(point_no)
				)

	def stamp_completion(self):
		if self.status == "Completed":
			if not self.date_of_completion:
				self.date_of_completion = nowdate()

			# Calculate timeliness based on whether task is time-based or not
			if int(self.depends_on_time or 0) and self.end_datetime:
				# For time-based tasks, compare completion datetime with end_datetime
				completion_datetime = (
					now_datetime() if not self.date_of_completion else get_datetime(self.date_of_completion)
				)
				end_dt = get_datetime(self.end_datetime)
				self.timeliness = "Ontime" if completion_datetime <= end_dt else "Late"
			elif self.due_date and self.date_of_completion:
				# For date-based tasks, compare completion date with due_date
				self.timeliness = (
					"Ontime" if getdate(self.date_of_completion) <= getdate(self.due_date) else "Late"
				)
		else:
			self.timeliness = None

	@frappe.whitelist()
	def mark_done(self):
		"""Mark task as Done by the assignee (task doer)"""
		if self.status not in ("Open", "Overdue"):
			frappe.throw(_("Only Open or Overdue tasks can be marked Done"))

		# Check if user has admin role
		settings = get_workboard_settings()
		admin_role = settings.get("workboard_admin_role")
		has_admin_role = admin_role and admin_role in frappe.get_roles(frappe.session.user)

		# Only assignee or admin role can mark done
		if (
			frappe.session.user != self.assign_to
			and frappe.session.user != "Administrator"
			and not has_admin_role
		):
			frappe.throw(_("Only the assigned user can mark this task as Done"))

		self.enforce_checklist()
		self.status = "Done"
		self.save(ignore_permissions=True)

	@frappe.whitelist()
	def mark_completed(self):
		"""Mark task as Completed - controlled by settings for Manual tasks"""
		# For Manual tasks, check settings to determine completion permissions
		if self.task_type == "Manual":
			if self.status != "Done":
				frappe.throw(_("Manual tasks must be marked as Done first before completion"))

			# Check WorkBoard Settings
			settings = get_workboard_settings()
			only_assignee_can_complete = settings.get("only_assignee_can_complete", 0)
			admin_role = settings.get("workboard_admin_role")

			current_user = frappe.session.user
			is_assignee = current_user == self.assign_to
			is_assigner = current_user == self.assign_from
			is_admin = current_user == "Administrator"
			has_admin_role = admin_role and admin_role in frappe.get_roles(current_user)

			if only_assignee_can_complete:
				# Only assignee or admin role can mark complete
				if not is_assignee and not is_admin and not has_admin_role:
					frappe.throw(_("Only the assigned user can mark this task as Completed"))
			else:
				# Only assigner or admin role can mark complete (approval workflow)
				if not is_assigner and not is_admin and not has_admin_role:
					frappe.throw(_("Only the task assigner can mark this task as Completed"))
		else:
			# For Auto tasks, allow direct completion
			if self.status not in ("Open", "Overdue"):
				frappe.throw(_("Only Open or Overdue tasks can be marked Completed"))

		self.status = "Completed"
		self.save(ignore_permissions=True)

	@frappe.whitelist()
	def fetch_checklist(self):
		self.wb_task_checklist_details = []
		if not self.checklist_template:
			return
		checklist_doc = frappe.get_doc("WB Task Checklist Template", self.checklist_template)
		for row in checklist_doc.wb_task_checklist_template_details:
			self.append("wb_task_checklist_details", {"checklist_item": row.checklist_item})

	@frappe.whitelist()
	def reopen(self):
		"""Re-open a Done or Completed task and revert energy points."""
		if self.status not in ("Done", "Completed"):
			frappe.throw(_("Only Done or Completed tasks can be re-opened"))

		settings = get_workboard_settings()
		admin_role = settings.get("workboard_admin_role")
		current_user = frappe.session.user
		is_assigner = current_user == self.assign_from
		has_admin = current_user == "Administrator" or (
			admin_role and admin_role in frappe.get_roles(current_user)
		)
		if not is_assigner and not has_admin:
			frappe.throw(_("Only the assigner can re-open this task"))

		# Revert energy points linked to this task
		logs = frappe.get_all(
			"Energy Point Log",
			filters={
				"reference_doctype": "WB Task",
				"reference_name": self.name,
				"type": "Auto",
				"reverted": 0,
			},
			pluck="name",
		)
		for log_name in logs:
			log_doc = frappe.get_doc("Energy Point Log", log_name)
			log_doc.revert(_("Task re-opened"), ignore_permissions=True)

		self.proof_of_work = None
		self.task_completion_remark = None
		self.date_of_completion = None
		self.timeliness = None
		self.status = "Open"
		self.save(ignore_permissions=True)

	@frappe.whitelist()
	def cancel_task(self):
		"""Cancel an Open or Overdue task."""
		if self.status not in ("Open", "Overdue"):
			frappe.throw(_("Only Open or Overdue tasks can be cancelled"))

		self.status = "Cancelled"
		self.save(ignore_permissions=True)
