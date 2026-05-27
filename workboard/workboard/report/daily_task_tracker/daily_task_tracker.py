import frappe
from frappe.utils import flt, getdate, now_datetime, time_diff_in_seconds, get_datetime


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{"fieldname": "task_id", "label": "Task ID", "fieldtype": "Link", "options": "WB Task", "width": 120},
		{"fieldname": "status", "label": "Status", "fieldtype": "Data", "width": 100},
		{"fieldname": "subject", "label": "Subject", "fieldtype": "Data", "width": 200},
		{"fieldname": "task_type", "label": "Task Type", "fieldtype": "Data", "width": 90},
		{"fieldname": "priority", "label": "Priority", "fieldtype": "Data", "width": 100},
		{"fieldname": "reference_doctype", "label": "Reference Doctype", "fieldtype": "Data", "width": 140},
		{"fieldname": "reference_document", "label": "Reference Document", "fieldtype": "Dynamic Link", "options": "reference_doctype", "width": 160},
		{"fieldname": "assign_from", "label": "Assign From", "fieldtype": "Link", "options": "User", "width": 150},
		{"fieldname": "assign_to", "label": "Assign To", "fieldtype": "Link", "options": "User", "width": 150},
		{"fieldname": "reporting_manager", "label": "Reporting Manager", "fieldtype": "Link", "options": "User", "width": 150},
		{"fieldname": "due_date", "label": "Due Date", "fieldtype": "Date", "width": 110},
		{"fieldname": "end_datetime", "label": "End Date & Time", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "new_due_date", "label": "New Due Date", "fieldtype": "Date", "width": 110},
		{"fieldname": "new_end_datetime", "label": "New End Date & Time", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "time_limit_in_minutes", "label": "Time Limit (Minutes)", "fieldtype": "Int", "width": 130},
		{"fieldname": "created_on", "label": "Created On", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "date_of_completion", "label": "Date of Completion", "fieldtype": "Datetime", "width": 160},
		{"fieldname": "completion_duration_minutes", "label": "Completion Duration in Minutes", "fieldtype": "Float", "width": 170},
		{"fieldname": "completion_duration_days", "label": "Completion Duration in Days", "fieldtype": "Float", "width": 160},
		{"fieldname": "timeliness", "label": "Timeliness", "fieldtype": "Data", "width": 100},
		# Created On Ageing buckets
		{"fieldname": "ageing_0_15min", "label": "0-15min", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_15_30min", "label": "15-30min", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_30_60min", "label": "30-60min", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_1_2hour", "label": "1-2hour", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_2_4hour", "label": "2-4hour", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_4_8hour", "label": "4-8hour", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_1_2days", "label": "1-2days", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_2_4days", "label": "2-4days", "fieldtype": "Check", "width": 80},
		{"fieldname": "ageing_4_plus_days", "label": "4+ Days", "fieldtype": "Check", "width": 80},
		# Additional columns
		{"fieldname": "wb_task_rule", "label": "WB Task Rule", "fieldtype": "Link", "options": "WB Task Rule", "width": 140},
		{"fieldname": "has_checklist", "label": "Has Checklist", "fieldtype": "Check", "width": 100},
		{"fieldname": "time_based_task", "label": "Time Based Task", "fieldtype": "Check", "width": 120},
		{"fieldname": "no_of_extension", "label": "No of Extension", "fieldtype": "Int", "width": 120},
		{"fieldname": "extension_id", "label": "Extension ID", "fieldtype": "Data", "width": 130},
		{"fieldname": "no_of_view", "label": "No of View", "fieldtype": "Int", "width": 100},
	]


def get_data(filters):
	conditions = get_conditions(filters)

	has_ref_doctype = frappe.db.has_column("WB Task", "custom_reference_doctype")
	has_ref_document = frappe.db.has_column("WB Task", "custom_reference_document")

	ref_doctype_field = ", t.custom_reference_doctype" if has_ref_doctype else ""
	ref_document_field = ", t.custom_reference_document" if has_ref_document else ""

	tasks = frappe.db.sql(
		"""
		SELECT
			t.name AS task_id,
			t.status,
			t.title AS subject,
			t.task_type,
			t.priority,
			t.assign_from,
			t.assign_to,
			t.reporting_manager,
			t.due_date,
			t.end_datetime,
			t.new_due_date,
			t.new_end_datetime,
			t.time_limit_in_minutes,
			t.creation AS created_on,
			t.date_of_completion,
			t.timeliness,
			t.wb_task_rule,
			t.has_checklist,
			t.depends_on_time AS time_based_task
			{ref_doctype_field}
			{ref_document_field}
		FROM `tabWB Task` t
		WHERE t.creation BETWEEN %(from_date)s AND %(to_date)s
		{conditions}
		ORDER BY t.creation DESC
		""".format(
			ref_doctype_field=ref_doctype_field,
			ref_document_field=ref_document_field,
			conditions=conditions,
		),
		{
			"from_date": getdate(filters.get("from_date")),
			"to_date": f"{getdate(filters.get('to_date'))} 23:59:59",
			"status": filters.get("status"),
			"priority": filters.get("priority"),
			"assign_to": filters.get("assign_to"),
			"assign_from": filters.get("assign_from"),
			"wb_task_rule": filters.get("wb_task_rule"),
		},
		as_dict=True,
	)

	if not tasks:
		return []

	task_names = [t.task_id for t in tasks]

	# Extension counts and latest extension ID per task
	extensions = frappe.db.sql(
		"""
		SELECT
			wb_task_reference,
			COUNT(*) AS ext_count,
			MAX(name) AS latest_extension
		FROM `tabWB Task Extension`
		WHERE wb_task_reference IN %(task_names)s AND docstatus = 1
		GROUP BY wb_task_reference
		""",
		{"task_names": task_names},
		as_dict=True,
	)
	ext_map = {e.wb_task_reference: e for e in extensions}

	# View counts per task
	view_counts = frappe.db.sql(
		"""
		SELECT
			reference_name,
			COUNT(*) AS view_count
		FROM `tabView Log`
		WHERE reference_doctype = 'WB Task' AND reference_name IN %(task_names)s
		GROUP BY reference_name
		""",
		{"task_names": task_names},
		as_dict=True,
	)
	view_map = {v.reference_name: v.view_count for v in view_counts}

	now = now_datetime()
	data = []
	for task in tasks:
		created_on = get_datetime(task.created_on)

		# Completion duration
		completion_minutes = 0
		completion_days = 0
		if task.date_of_completion:
			diff_seconds = time_diff_in_seconds(task.date_of_completion, created_on)
			completion_minutes = round(flt(diff_seconds) / 60, 2)
			completion_days = round(flt(diff_seconds) / 86400, 2)

		# Created On ageing — time from creation to now (or completion)
		ref_time = get_datetime(task.date_of_completion) if task.date_of_completion else now
		ageing_minutes = flt(time_diff_in_seconds(ref_time, created_on)) / 60

		ext_info = ext_map.get(task.task_id, {})

		row = {
			"task_id": task.task_id,
			"status": task.status,
			"subject": task.subject,
			"task_type": task.task_type,
			"priority": task.priority,
			"reference_doctype": task.get("custom_reference_doctype") or "",
			"reference_document": task.get("custom_reference_document") or "",
			"assign_from": task.assign_from,
			"assign_to": task.assign_to,
			"reporting_manager": task.reporting_manager,
			"due_date": task.due_date,
			"end_datetime": task.end_datetime,
			"new_due_date": task.new_due_date,
			"new_end_datetime": task.new_end_datetime,
			"time_limit_in_minutes": task.time_limit_in_minutes,
			"created_on": task.created_on,
			"date_of_completion": task.date_of_completion,
			"completion_duration_minutes": completion_minutes if task.date_of_completion else None,
			"completion_duration_days": completion_days if task.date_of_completion else None,
			"timeliness": task.timeliness,
			# Ageing buckets
			"ageing_0_15min": 1 if ageing_minutes <= 15 else 0,
			"ageing_15_30min": 1 if 15 < ageing_minutes <= 30 else 0,
			"ageing_30_60min": 1 if 30 < ageing_minutes <= 60 else 0,
			"ageing_1_2hour": 1 if 60 < ageing_minutes <= 120 else 0,
			"ageing_2_4hour": 1 if 120 < ageing_minutes <= 240 else 0,
			"ageing_4_8hour": 1 if 240 < ageing_minutes <= 480 else 0,
			"ageing_1_2days": 1 if 480 < ageing_minutes <= 2880 else 0,
			"ageing_2_4days": 1 if 2880 < ageing_minutes <= 5760 else 0,
			"ageing_4_plus_days": 1 if ageing_minutes > 5760 else 0,
			# Additional
			"wb_task_rule": task.wb_task_rule,
			"has_checklist": task.has_checklist,
			"time_based_task": task.time_based_task,
			"no_of_extension": ext_info.get("ext_count") or 0,
			"extension_id": ext_info.get("latest_extension") or "",
			"no_of_view": view_map.get(task.task_id) or 0,
		}
		data.append(row)

	return data


def get_conditions(filters):
	conditions = []
	if filters.get("status"):
		conditions.append("AND t.status = %(status)s")
	if filters.get("priority"):
		conditions.append("AND t.priority = %(priority)s")
	if filters.get("assign_to"):
		conditions.append("AND t.assign_to = %(assign_to)s")
	if filters.get("assign_from"):
		conditions.append("AND t.assign_from = %(assign_from)s")
	if filters.get("wb_task_rule"):
		conditions.append("AND t.wb_task_rule = %(wb_task_rule)s")
	return "\n".join(conditions)
