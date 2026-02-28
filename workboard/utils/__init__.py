import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.safe_exec import get_safe_globals


def _get_end_datetime_from_assignee_shift(assign_to_user):
	"""
	Calculate task end_datetime based on Assign To user's Employee shift timing.
	If task is created during user's shift: end = shift end time today.
	If task is created after user's shift ended: end = shift end time tomorrow.
	Returns None if HRMS not installed or employee/shift not found (caller will use time_limit_in_minutes).
	"""
	try:
		from hrms.hr.doctype.shift_assignment.shift_assignment import (
			get_actual_start_end_datetime_of_shift,
			get_employee_shift,
		)
	except ImportError:
		return None

	employee = frappe.db.get_value("Employee", {"user_id": assign_to_user}, "name", cache=True)
	if not employee:
		return None

	now = now_datetime()
	# First check if we're currently within a shift
	shift_info = get_actual_start_end_datetime_of_shift(employee, now, consider_default_shift=True)
	if shift_info:
		return shift_info.get("actual_end") or shift_info.get("end_datetime")

	# We're outside any shift (e.g. after today's shift ended): get next shift (forward)
	next_shift = get_employee_shift(employee, now, consider_default_shift=True, next_shift_direction="forward")
	if next_shift:
		return next_shift.get("actual_end") or next_shift.get("end_datetime")

	return None


def _resolve_assign_to(rule, context=None):
	"""Resolve the assign_to user based on assign_to_type"""
	assign_to_type = rule.get("assign_to_type") or "User"
	
	if assign_to_type == "User":
		if not rule.assign_to:
			frappe.throw(_("Assign To is required when Assign To Type is 'User'"))
		return rule.assign_to
	
	elif assign_to_type == "Field":
		# Get user from reference document field
		if not context or "doc" not in context:
			frappe.throw(_("Cannot resolve assign_to from field: no reference document in context. Field-based assignment requires an event-based task rule."))
		
		ref_doc = context.get("doc")
		assign_to_field = rule.get("assign_to_field")
		
		if not assign_to_field:
			frappe.throw(_("Assign To Field is required when Assign To Type is 'Field'"))
		
		# Handle child table fields (format: "fieldname,parent_field")
		# For now, we'll extract just the fieldname (first part before comma)
		# Child table support can be added later if needed
		if "," in assign_to_field:
			fieldname = assign_to_field.split(",")[0]
		else:
			fieldname = assign_to_field
		
		user = ref_doc.get(fieldname)
		if not user:
			frappe.throw(_("No user found in field '{0}' of {1} {2}").format(
				fieldname, ref_doc.doctype, ref_doc.name
			))
		
		# Validate that the value is actually a user
		if not frappe.db.exists("User", user):
			frappe.throw(_("Value '{0}' in field '{1}' is not a valid user").format(user, fieldname))
		
		return user
	
	elif assign_to_type == "Role":
		# Get first active user with the specified role
		role = rule.get("assign_to_role")
		if not role:
			frappe.throw(_("Assign To Role is required when Assign To Type is 'Role'"))
		
		# Get users with the role, prioritizing enabled users
		users = frappe.get_all(
			"Has Role",
			filters={"role": role, "parenttype": "User"},
			fields=["parent"],
			limit=100
		)
		
		if not users:
			frappe.throw(_("No user found with role '{0}'").format(role))
		
		# Filter to enabled users first
		user_list = [u.parent for u in users]
		enabled_users = frappe.get_all(
			"User",
			filters={"name": ["in", user_list], "enabled": 1},
			fields=["name"],
			limit=1
		)
		
		if enabled_users:
			return enabled_users[0].name
		
		# If no enabled users, return the first user anyway
		return users[0].parent
	
	return None


def _create_task_from_rule(rule, context=None):
	title = rule.title or _("Task")
	description = (
		frappe.render_template(rule.description, context)
		if (rule.description and context)
		else (rule.description or "")
	)

	# Use Administrator as default assign_from for recurring/event tasks if not specified
	assign_from = rule.assign_from
	if not assign_from and (cint(rule.recurring or 0) or cint(rule.event or 0)):
		assign_from = "Administrator"

	# Resolve assign_to based on assign_to_type (needed early for shift-based end_datetime)
	assign_to = _resolve_assign_to(rule, context=context)

	# Calculate end_datetime if time-based task
	# Prefer Assign To user's Employee shift timing: end = shift end (today or tomorrow if shift already over)
	end_datetime = None
	depends_on_time = cint(rule.depends_on_time or 0)
	if depends_on_time and rule.time_limit_in_minutes:
		shift_end = _get_end_datetime_from_assignee_shift(assign_to)
		end_datetime = shift_end if shift_end else add_to_date(now_datetime(), minutes=cint(rule.time_limit_in_minutes))
	if not assign_to:
		frappe.throw(_("Could not resolve assign_to user for task rule '{0}'").format(rule.name))

	doc = frappe.get_doc(
		{
			"doctype": "WB Task",
			"title": title,
			"description": description,
			"priority": rule.priority,
			"assign_from": assign_from,
			"assign_to": assign_to,
			"due_date": add_days(nowdate(), cint(rule.due_days or 0)),
			"status": "Open",
			"task_type": "Auto",
			"has_checklist": cint(rule.has_checklist or 0),
			"checklist_template": rule.checklist_template,
			"depends_on_time": depends_on_time,
			"end_datetime": end_datetime,
		}
	)
	# Set reference doctype/document when task is created from an event (context has the triggering doc)
	if context and context.get("doc"):
		ref_doc = context["doc"]
		if frappe.db.has_column("WB Task", "custom_reference_doctype"):
			doc.custom_reference_doctype = ref_doc.doctype
		if frappe.db.has_column("WB Task", "custom_reference_document"):
			doc.custom_reference_document = ref_doc.name
	doc.fetch_checklist()
	doc.save(ignore_permissions=True)
	return doc


def _context(doc):
	return {
		"doc": doc,
		"nowdate": nowdate,
		"frappe": frappe._dict(utils=get_safe_globals().get("frappe").get("utils")),
	}


@frappe.whitelist()
def get_workboard_settings():
	"""Get WorkBoard Settings without permission checks"""
	return frappe.get_doc("WorkBoard Settings", "WorkBoard Settings")


@frappe.whitelist()
def get_doctype_fields(doctype):
	"""
	Return list of { value: fieldname, label: label or fieldname } for the given doctype.
	Used so Verification Field can show and store fieldname instead of DocField hash.
	"""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return []
	fields = frappe.get_all(
		"DocField",
		filters={"parent": doctype, "parenttype": "DocType"},
		fields=["fieldname", "label"],
		order_by="idx",
	)
	return [
		{"value": f["fieldname"], "label": (f.get("label") or f["fieldname"]) or f["fieldname"]}
		for f in fields
	]


@frappe.whitelist()
def get_docfield_fieldnames(docfield_names):
	"""
	Return dict mapping DocField name -> fieldname for display.
	Used to show fieldname in grid when stored value is legacy DocField name (hash).
	"""
	if not docfield_names:
		return {}
	if isinstance(docfield_names, str):
		docfield_names = [docfield_names]
	rows = frappe.get_all(
		"DocField",
		filters={"name": ["in", docfield_names]},
		fields=["name", "fieldname"],
	)
	return {r["name"]: r["fieldname"] for r in rows}
