import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.safe_exec import get_safe_globals
from datetime import timedelta


def _is_employee_holiday(employee, date):
	"""Return True if the given date is a holiday for the employee. Safe if ERPNext/HRMS not available."""
	try:
		from erpnext.setup.doctype.employee.employee import is_holiday
		return bool(is_holiday(employee, date, raise_exception=False))
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


def _get_end_datetime_from_assignee_shift_and_duration(assign_to_user, time_limit_in_minutes, reference_datetime=None):
	"""
	Allocate time_limit_in_minutes only within the assignee's nominal working hours
	(Shift Type start_time–end_time), skipping holidays and grace periods.

	If reference_datetime is given, allocation starts from that time (e.g. start of next working day).
	Otherwise uses current time.

	Rules:
	- Task created before shift start → start counting from shift start.
	- Task created during working hours (start–end) → start counting from now.
	- Task created after working hours or on a holiday → next working day's shift start.
	- Duration spanning multiple shifts → carry remainder to next working shift.

	Returns None if no employee/shift found (caller falls back to now + time_limit_in_minutes).
	"""
	logger = frappe.logger("wb_task_shift", allow_site=True, max_size=5)

	try:
		from hrms.hr.doctype.shift_assignment.shift_assignment import (
			get_actual_start_end_datetime_of_shift,
			get_employee_shift,
		)
	except ImportError:
		logger.warning("[WB Task] HRMS not installed – skipping shift-based end datetime")
		return None

	employee = frappe.db.get_value("Employee", {"user_id": assign_to_user}, "name", cache=True)
	logger.info(f"[WB Task] assign_to_user={assign_to_user}  employee={employee}  limit_mins={time_limit_in_minutes}")
	if not employee:
		logger.warning(f"[WB Task] No Employee found for user {assign_to_user} – using fallback")
		return None

	now = get_datetime(reference_datetime) if reference_datetime is not None else get_datetime(now_datetime())
	limit_mins = cint(time_limit_in_minutes) or 0
	logger.info(f"[WB Task] now={now}  limit_mins={limit_mins}")
	if limit_mins <= 0:
		return None

	def _shift_bounds(shift):
		"""(work_start, work_end) = nominal hours; actual_end includes grace period."""
		work_start = get_datetime(shift.get("start_datetime") or shift.get("actual_start"))
		work_end   = get_datetime(shift.get("end_datetime")   or shift.get("actual_end"))
		actual_end = get_datetime(shift.get("actual_end")     or shift.get("end_datetime"))
		return work_start, work_end, actual_end

	def _shift_for_date(check_date):
		"""
		Return (work_start, work_end, actual_end) for check_date, or (None,None,None).
		Uses midnight of the date to ensure HRMS picks the right day's shift.
		"""
		candidate = get_datetime(str(check_date) + " 00:00:01")
		shift = get_employee_shift(employee, candidate, consider_default_shift=True)
		if not shift:
			logger.info(f"[WB Task] _shift_for_date({check_date}): no shift found")
			return None, None, None
		ws, we, ae = _shift_bounds(shift)
		if getdate(ws) != check_date:
			logger.info(f"[WB Task] _shift_for_date({check_date}): shift date mismatch ws={ws}")
			return None, None, None
		logger.info(f"[WB Task] _shift_for_date({check_date}): ws={ws}  we={we}  ae={ae}")
		return ws, we, ae

	def _next_working_shift_from_date(start_date):
		"""
		Walk forward day by day from start_date until a non-holiday day with a shift is found.
		Returns (work_start, work_end, actual_end) or (None, None, None).
		"""
		check_date = start_date
		for i in range(366):
			is_hol = _is_employee_holiday(employee, check_date)
			logger.info(f"[WB Task] _next_working_shift day={i} check_date={check_date} holiday={is_hol}")
			if not is_hol:
				ws, we, ae = _shift_for_date(check_date)
				if ws is not None:
					logger.info(f"[WB Task] Found next working shift: ws={ws}  we={we}")
					return ws, we, ae
			check_date = getdate(add_days(check_date, 1))
		logger.warning("[WB Task] _next_working_shift_from_date: exhausted 366 days without finding a working shift")
		return None, None, None

	# ── Determine effective start ─────────────────────────────────────────────
	today = getdate(now)
	today_is_holiday = _is_employee_holiday(employee, today)
	logger.info(f"[WB Task] today={today}  today_is_holiday={today_is_holiday}")

	if today_is_holiday:
		logger.info("[WB Task] Today is a holiday → looking for next working day")
		effective_start, _, _ = _next_working_shift_from_date(getdate(add_days(today, 1)))
	else:
		ws_today, we_today, ae_today = _shift_for_date(today)
		if ws_today is None:
			logger.info("[WB Task] No shift for today → looking forward")
			effective_start, _, _ = _next_working_shift_from_date(today)
		elif now <= we_today:
			effective_start = now if now >= ws_today else ws_today
			logger.info(f"[WB Task] Within working hours → effective_start={effective_start}")
		else:
			logger.info(f"[WB Task] After working hours (now={now} > we={we_today}) → next working day")
			effective_start, _, _ = _next_working_shift_from_date(getdate(add_days(today, 1)))

	logger.info(f"[WB Task] effective_start={effective_start}")
	if effective_start is None:
		logger.warning("[WB Task] Could not determine effective_start – using fallback")
		return None

	# ── Allocate limit_mins across consecutive working shifts ─────────────────
	remaining = limit_mins
	current_time = effective_start
	logger.info(f"[WB Task] Starting allocation: remaining={remaining}  current_time={current_time}")

	for iteration in range(366):
		check_date = getdate(current_time)

		if _is_employee_holiday(employee, check_date):
			logger.info(f"[WB Task] Allocation iter={iteration}: {check_date} is holiday – skipping")
			check_date = getdate(add_days(check_date, 1))
			current_time = get_datetime(str(check_date) + " 00:00:00")
			continue

		ws, we, ae = _shift_for_date(check_date)
		if ws is None:
			logger.info(f"[WB Task] Allocation iter={iteration}: no shift on {check_date} – skipping")
			check_date = getdate(add_days(check_date, 1))
			current_time = get_datetime(str(check_date) + " 00:00:00")
			continue

		if current_time < ws:
			current_time = ws
		if current_time > we:
			logger.info(f"[WB Task] Allocation iter={iteration}: current_time {current_time} past we {we} – next day")
			check_date = getdate(add_days(check_date, 1))
			current_time = get_datetime(str(check_date) + " 00:00:00")
			continue

		available = (we - current_time).total_seconds() / 60
		used = min(remaining, available)
		remaining -= used
		current_time = current_time + timedelta(minutes=used)
		logger.info(f"[WB Task] Allocation iter={iteration}: date={check_date}  available={available:.1f}m  used={used:.1f}m  remaining={remaining:.1f}m  current_time={current_time}")

		if remaining <= 0:
			logger.info(f"[WB Task] Allocation complete → end_datetime={current_time}")
			return current_time

		check_date = getdate(add_days(check_date, 1))
		current_time = get_datetime(str(check_date) + " 00:00:00")

	logger.warning("[WB Task] Allocation exhausted 366 iterations without completing – using fallback")
	return None


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
	
	elif assign_to_type == "Role Profile":
		# Get first active user with the specified role profile
		role_profile = rule.get("assign_to_role_profile")
		if not role_profile:
			frappe.throw(_("Assign To Role Profile is required when Assign To Type is 'Role Profile'"))
		
		users = frappe.get_all(
			"User",
			filters={"role_profile_name": role_profile, "enabled": 1},
			fields=["name"],
			limit=1,
		)
		
		if users:
			return users[0].name
		
		# Fallback: allow even if user is disabled (but at least exists)
		users = frappe.get_all(
			"User",
			filters={"role_profile_name": role_profile},
			fields=["name"],
			limit=1,
		)
		if not users:
			frappe.throw(_("No user found with role profile '{0}'").format(role_profile))
		return users[0].name
	
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


def _get_next_working_day(assign_to_user, from_date):
	"""Return the first working day (non-holiday) on or after from_date + 1 day."""
	next_date = add_days(from_date, 1)
	try:
		employee = frappe.db.get_value("Employee", {"user_id": assign_to_user}, "name", cache=True)
		if not employee:
			return next_date
		from erpnext.setup.doctype.employee.employee import is_holiday
		while is_holiday(employee, next_date, raise_exception=False):
			next_date = add_days(next_date, 1)
		return next_date
	except Exception:
		return next_date


def _get_shift_lunch_break_minutes(shift):
	"""Return lunch break minutes configured on Shift Type (custom_lunch_break_minutes)."""
	try:
		if not shift:
			return 0

		shift_type_name = None
		if hasattr(shift, "get"):
			shift_type_name = shift.get("shift_type") or shift.get("name")
		else:
			shift_type_name = getattr(shift, "shift_type", None) or getattr(shift, "name", None)

		if not shift_type_name or not frappe.db.exists("Shift Type", shift_type_name):
			return 0
		if not frappe.db.has_column("Shift Type", "custom_lunch_break_minutes"):
			return 0

		return max(cint(frappe.db.get_value("Shift Type", shift_type_name, "custom_lunch_break_minutes") or 0), 0)
	except Exception:
		return 0


def _get_rule_or_default_buffer_minutes(rule):
	"""Rule buffer takes priority; fallback to Cruzine Setting default."""
	try:
		rule_buffer = rule.get("buffer_time_minutes") if isinstance(rule, dict) else getattr(rule, "buffer_time_minutes", None)
		if rule_buffer not in (None, ""):
			return max(cint(rule_buffer), 0)

		if not frappe.db.exists("DocType", "Cruzine Setting"):
			return 0
		default_buffer = frappe.db.get_single_value("Cruzine Setting", "wb_task_buffer_time_minutes") or 0
		return max(cint(default_buffer), 0)
	except Exception:
		return 0


def get_effective_working_minutes_per_day(assign_to_user):
	"""
	Return effective working minutes for the assignee's current day:
	(shift duration in minutes) - (Shift Type lunch break minutes).
	Uses HRMS shift for today if available, else 480.
	"""
	try:
		employee = frappe.db.get_value("Employee", {"user_id": assign_to_user}, "name", cache=True)
		if not employee:
			return 480
		from hrms.hr.doctype.shift_assignment.shift_assignment import get_employee_shift
		today = getdate(now_datetime())
		candidate = get_datetime(str(today) + " 00:00:01")
		shift = get_employee_shift(employee, candidate, consider_default_shift=True)
		if not shift:
			return 480
		ws = get_datetime(shift.get("start_datetime") or shift.get("actual_start"))
		we = get_datetime(shift.get("end_datetime") or shift.get("actual_end"))
		if ws and we:
			total_mins = int((we - ws).total_seconds() / 60)
			lunch_break_mins = _get_shift_lunch_break_minutes(shift)
			return max(total_mins - lunch_break_mins, 0)
		return 480
	except Exception:
		return 480


def _get_used_task_duration_minutes(assign_to_user, due_date):
	"""Sum of task_time_duration_minutes for WB Tasks assigned to assign_to_user with this due_date (any status)."""
	if not frappe.db.has_column("WB Task", "task_time_duration_minutes"):
		return 0
	result = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(task_time_duration_minutes), 0)
		FROM `tabWB Task`
		WHERE assign_to = %(assign_to)s AND due_date = %(due_date)s
		""",
		{"assign_to": assign_to_user, "due_date": due_date},
		as_dict=False,
	)
	return cint(result[0][0]) if result else 0


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
	if not assign_to:
		frappe.throw(_("Could not resolve assign_to user for task rule '{0}'").format(rule.name))

	due_date = _get_due_date_skipping_holidays(assign_to, cint(rule.due_days or 0))
	task_duration_mins = cint(rule.get("task_time_duration_minutes") if isinstance(rule, dict) else getattr(rule, "task_time_duration_minutes", None) or 0)

	# Capacity-based scheduling: if rule has task time duration, cap today's workload and push overflow to next day
	if task_duration_mins > 0:
		effective_mins = get_effective_working_minutes_per_day(assign_to)
		used_on_due = _get_used_task_duration_minutes(assign_to, due_date)
		buffer_mins = _get_rule_or_default_buffer_minutes(rule)
		if used_on_due + task_duration_mins > (effective_mins + buffer_mins):
			due_date = _get_next_working_day(assign_to, due_date)

	# Calculate end_datetime if time-based task: allocate time_limit_in_minutes only within assignee's shift hours
	end_datetime = None
	depends_on_time = cint(rule.depends_on_time or 0)
	ref_dt_for_end = None
	if due_date and due_date != getdate(now_datetime()):
		ref_dt_for_end = get_datetime(str(due_date) + " 00:00:01")
	if depends_on_time and rule.time_limit_in_minutes:
		end_datetime = _get_end_datetime_from_assignee_shift_and_duration(
			assign_to, cint(rule.time_limit_in_minutes), reference_datetime=ref_dt_for_end
		)
		if not end_datetime:
			end_datetime = add_to_date(now_datetime(), minutes=cint(rule.time_limit_in_minutes))

	rule_name = rule.get("name") if isinstance(rule, dict) else rule.name
	doc_dict = {
		"doctype": "WB Task",
		"title": title,
		"description": description,
		"priority": rule.priority,
		"assign_from": assign_from,
		"assign_to": assign_to,
		"due_date": due_date,
		"status": "Open",
		"task_type": "Auto",
		"wb_task_rule": rule_name,
		"has_checklist": cint(rule.has_checklist or 0),
		"checklist_template": rule.checklist_template,
		"depends_on_time": depends_on_time,
		"end_datetime": end_datetime,
	}
	if frappe.db.has_column("WB Task", "task_time_duration_minutes"):
		doc_dict["task_time_duration_minutes"] = task_duration_mins
	doc = frappe.get_doc(doc_dict)
	# Set reference doctype/document when task is created from an event (context has the triggering doc)
	if context and context.get("doc"):
		ref_doc = context["doc"]
		if frappe.db.has_column("WB Task", "custom_reference_doctype"):
			doc.custom_reference_doctype = ref_doc.doctype
		if frappe.db.has_column("WB Task", "custom_reference_document"):
			doc.custom_reference_document = ref_doc.name

		# Store child-table match details when the rule has a child_table_condition.
		# These get populated by handlers/background jobs using context["child_table_name"] and context["child_table_id"].
		child_table_name = context.get("child_table_name")
		child_table_id = context.get("child_table_id")
		if child_table_name and frappe.db.has_column("WB Task", "custom_child_table_name"):
			doc.custom_child_table_name = child_table_name
		if child_table_id is not None and frappe.db.has_column("WB Task", "custom_child_table_id"):
			doc.custom_child_table_id = child_table_id
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
def get_doctype_table_fields(doctype):
	"""
	Return list of { value: fieldname, label: label or fieldname } for Table-type fields
	of the given doctype. Used to populate Verification Child Table dropdown.
	"""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return []
	meta = frappe.get_meta(doctype)
	return [
		{"value": df.fieldname, "label": df.label or df.fieldname}
		for df in (meta.get("fields") or [])
		if df.fieldtype == "Table"
	]


@frappe.whitelist()
def get_doctype_fields(doctype, child_table_fieldname=None):
	"""
	Return list of { value: fieldname, label: label or fieldname } for the given doctype.
	If child_table_fieldname is set, returns fields of the child doctype (table's options).
	Includes both standard and custom fields.
	"""
	if not doctype or not frappe.db.exists("DocType", doctype):
		return []
	if child_table_fieldname:
		meta = frappe.get_meta(doctype)
		table_field = meta.get_field(child_table_fieldname)
		if table_field and table_field.fieldtype == "Table" and table_field.options:
			doctype = table_field.options
		else:
			return []

	# Standard fields
	standard = frappe.get_all(
		"DocField",
		filters={"parent": doctype, "parenttype": "DocType"},
		fields=["fieldname", "label", "idx"],
		order_by="idx",
	)

	# Custom fields added on this doctype
	custom = frappe.get_all(
		"Custom Field",
		filters={"dt": doctype},
		fields=["fieldname", "label"],
		order_by="label",
	)
	for cf in custom:
		cf["idx"] = 9999  # sort custom fields after standard fields

	# Merge, deduplicate by fieldname
	seen = {}
	for f in standard:
		seen[f["fieldname"]] = f
	for f in custom:
		seen[f["fieldname"]] = f

	all_fields = sorted(seen.values(), key=lambda f: (f["idx"], f.get("label") or f["fieldname"]))

	return [
		{"value": f["fieldname"], "label": (f.get("label") or f["fieldname"])}
		for f in all_fields
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
