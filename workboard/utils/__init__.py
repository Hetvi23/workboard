import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.safe_exec import get_safe_globals
from datetime import timedelta


def _is_employee_holiday(employee, date):
	"""
	Return True if `date` is a holiday for the employee.
	Uses Frappe DB directly (no ERPNext import dependency) so it works on any site.
	Looks up the Employee's holiday_list; falls back to Company default_holiday_list.
	"""
	try:
		holiday_list, company = frappe.db.get_value(
			"Employee", employee, ["holiday_list", "company"]
		) or (None, None)
		if not holiday_list and company:
			holiday_list = frappe.db.get_value("Company", company, "default_holiday_list")
		if not holiday_list:
			return False
		return bool(
			frappe.db.exists("Holiday", {"parent": holiday_list, "holiday_date": getdate(date)})
		)
	except Exception:
		return False


def _get_shift_for_datetime(employee, dt, consider_default_shift=True):
	"""Return shift dict that contains dt, or the next shift after dt. Returns {} if none."""
	from hrms.hr.doctype.shift_assignment.shift_assignment import (
		get_actual_start_end_datetime_of_shift,
		get_employee_shift,
	)
	in_shift = get_actual_start_end_datetime_of_shift(employee, dt, consider_default_shift)
	if in_shift:
		return in_shift
	return get_employee_shift(employee, dt, consider_default_shift, next_shift_direction="forward") or {}


def _get_end_datetime_from_assignee_shift_and_duration(assign_to_user, time_limit_in_minutes):
	"""
	Calculate task end_datetime by allocating time_limit_in_minutes only within the assignee's
	shift hours (Employee > Shift). Does not count time outside shifts.

	- If task is created before shift start (e.g. 8:00, shift 9:00–15:30): start counting from 9:00.
	- If task is created during shift (e.g. 10:00, shift 9:00–15:30): start counting from 10:00.
	- If task is created after shift (e.g. 22:00): start counting from next shift start (e.g. tomorrow 9:00).
	- If the duration does not fit in one shift, remaining time is allocated from the next working shift(s).

	Returns None if HRMS not installed or employee/shift not found (caller uses now + time_limit_in_minutes).
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

	now = get_datetime(now_datetime())
	limit_mins = cint(time_limit_in_minutes) or 0
	if limit_mins <= 0:
		return None

	# Use nominal working hours only (start_datetime/end_datetime from Shift Type),
	# never the grace-period extensions (actual_start/actual_end) for task deadline calculation.
	def _work_start(shift):
		v = shift.get("start_datetime")
		return get_datetime(v) if v else get_datetime(shift.get("actual_start"))
	def _work_end(shift):
		v = shift.get("end_datetime")
		return get_datetime(v) if v else get_datetime(shift.get("actual_end"))

	# 1) Effective start: shift work start if before shift, now if during working hours, next shift work start if after.
	#    Skip any shift that falls on a holiday (task end datetime must not be on a holiday).
	current_shift = get_actual_start_end_datetime_of_shift(employee, now, consider_default_shift=True)
	work_start = _work_start(current_shift) if current_shift else None
	work_end = _work_end(current_shift) if current_shift else None
	in_work_window = current_shift and work_start <= now <= work_end

	if current_shift and not _is_employee_holiday(employee, getdate(now)) and in_work_window:
		# We're inside working hours and today is not a holiday: start counting from now
		effective_start = now
	elif current_shift and _is_employee_holiday(employee, getdate(now)):
		# Today is holiday; ignore current shift and use next working day's shift
		cursor = _work_end(current_shift) + timedelta(seconds=1)
		effective_start = None
		for _ in range(366):
			next_shift = get_employee_shift(employee, cursor, consider_default_shift=True, next_shift_direction="forward")
			if not next_shift:
				return None
			ws = _work_start(next_shift)
			if not _is_employee_holiday(employee, getdate(ws)):
				effective_start = ws
				break
			cursor = _work_end(next_shift) + timedelta(seconds=1)
		if effective_start is None:
			return None
	elif current_shift and not in_work_window:
		# In shift grace period but outside work window (e.g. after 15:00): use next shift
		cursor = work_end + timedelta(seconds=1)
		effective_start = None
		for _ in range(366):
			next_shift = get_employee_shift(employee, cursor, consider_default_shift=True, next_shift_direction="forward")
			if not next_shift:
				return None
			ws = _work_start(next_shift)
			if not _is_employee_holiday(employee, getdate(ws)):
				effective_start = ws
				break
			cursor = _work_end(next_shift) + timedelta(seconds=1)
		if effective_start is None:
			return None
	else:
		# Outside shift: use next shift's work start, skipping any shift that falls on a holiday
		cursor = now
		effective_start = None
		for _ in range(366):
			next_shift = get_employee_shift(employee, cursor, consider_default_shift=True, next_shift_direction="forward")
			if not next_shift:
				return None
			ws = _work_start(next_shift)
			if not _is_employee_holiday(employee, getdate(ws)):
				effective_start = ws
				break
			cursor = _work_end(next_shift) + timedelta(seconds=1)
		if effective_start is None:
			return None

	remaining_minutes = limit_mins
	current_time = effective_start
	max_days = 366
	day_count = 0

	while remaining_minutes > 0 and day_count < max_days:
		# Get shift that contains current_time (or next shift if we're between shifts)
		shift = _get_shift_for_datetime(employee, current_time)
		if not shift:
			return None
		shift_start = _work_start(shift)
		shift_end = _work_end(shift)

		# Skip this shift if it falls on a holiday (don't allocate task time on holidays)
		if _is_employee_holiday(employee, getdate(shift_start)):
			current_time = shift_end + timedelta(seconds=1)
			day_count += 1
			continue

		# If we're before this shift (e.g. between shifts), jump to shift start
		if current_time < shift_start:
			current_time = shift_start

		minutes_available = max(0, (shift_end - current_time).total_seconds() / 60)
		minutes_to_use = min(remaining_minutes, minutes_available)
		remaining_minutes -= minutes_to_use
		current_time = current_time + timedelta(minutes=minutes_to_use)

		if remaining_minutes <= 0:
			return current_time

		# Move to next shift: use moment after this shift end so "forward" gives next shift
		current_time = shift_end + timedelta(seconds=1)
		day_count += 1

	return current_time if remaining_minutes <= 0 else None


def _get_end_datetime_from_assignee_shift(assign_to_user):
	"""
	Calculate task end_datetime based on Assign To user's Employee shift timing.
	If task is created during user's shift: end = shift end time today (only if in future).
	If task is created after user's shift ended: end = shift end time of next shift.
	Never returns a datetime in the past (caller will use time_limit_in_minutes if no future shift).
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

	# First check if we're currently within a shift and shift end is still in the future
	shift_info = get_actual_start_end_datetime_of_shift(employee, now, consider_default_shift=True)
	if shift_info:
		shift_end = shift_info.get("actual_end") or shift_info.get("end_datetime")
		if shift_end and get_datetime(shift_end) > now:
			return shift_end
		# Shift end is in the past; fall through to next shift

	# We're outside any shift or shift end already passed: get next shift (forward)
	next_shift = get_employee_shift(employee, now, consider_default_shift=True, next_shift_direction="forward")
	if next_shift:
		next_end = next_shift.get("actual_end") or next_shift.get("end_datetime")
		if next_end and get_datetime(next_end) > now:
			return next_end

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


def _get_due_date_skipping_holidays(assign_to_user, due_days):
	"""
	Return due_date = today + due_days, skipping holidays so the due date falls on a working day.
	Uses assignee's Employee holiday list if HRMS/ERPNext is available; otherwise returns add_days(today, due_days).
	"""
	due_days = cint(due_days or 0)
	due_date = add_days(nowdate(), due_days)

	try:
		employee = frappe.db.get_value("Employee", {"user_id": assign_to_user}, "name", cache=True)
		if not employee:
			return due_date
		from erpnext.setup.doctype.employee.employee import is_holiday

		# If due_date falls on a holiday, advance to the next working day
		while is_holiday(employee, due_date, raise_exception=False):
			due_date = add_days(due_date, 1)
		return due_date
	except Exception:
		return due_date


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

	# Calculate end_datetime if time-based task: allocate time_limit_in_minutes only within assignee's shift hours
	end_datetime = None
	depends_on_time = cint(rule.depends_on_time or 0)
	if depends_on_time and rule.time_limit_in_minutes:
		end_datetime = _get_end_datetime_from_assignee_shift_and_duration(
			assign_to, cint(rule.time_limit_in_minutes)
		)
		if not end_datetime:
			end_datetime = add_to_date(now_datetime(), minutes=cint(rule.time_limit_in_minutes))
	if not assign_to:
		frappe.throw(_("Could not resolve assign_to user for task rule '{0}'").format(rule.name))

	due_date = _get_due_date_skipping_holidays(assign_to, cint(rule.due_days or 0))

	doc = frappe.get_doc(
		{
			"doctype": "WB Task",
			"title": title,
			"description": description,
			"priority": rule.priority,
			"assign_from": assign_from,
			"assign_to": assign_to,
			"due_date": due_date,
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
