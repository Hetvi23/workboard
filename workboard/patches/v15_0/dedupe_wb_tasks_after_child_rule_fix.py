import frappe


def _safe_string(value):
	return "" if value is None else str(value)


def _minute_bucket(dt_value):
	if not dt_value:
		return ""
	try:
		return dt_value.strftime("%Y-%m-%d %H:%M")
	except Exception:
		return _safe_string(dt_value)[:16]


def execute():
	"""One-time cleanup of duplicate WB Tasks created by child-table rule over-triggering.

	Deduplication is intentionally conservative:
	- keep the first-created task
	- only remove tasks that share the same identity key within the same minute bucket
	"""
	base_fields = [
		"name",
		"creation",
		"wb_task_rule",
		"title",
		"assign_to",
		"due_date",
		"task_type",
		"status",
	]

	has_ref_doctype = frappe.db.has_column("WB Task", "custom_reference_doctype")
	has_ref_docname = frappe.db.has_column("WB Task", "custom_reference_document")
	if has_ref_doctype:
		base_fields.append("custom_reference_doctype")
	if has_ref_docname:
		base_fields.append("custom_reference_document")

	tasks = frappe.get_all(
		"WB Task",
		fields=base_fields,
		order_by="creation asc, name asc",
		limit_page_length=0,
	)

	if not tasks:
		return

	seen = {}
	to_delete = []

	for row in tasks:
		key = (
			_safe_string(row.get("wb_task_rule")),
			_safe_string(row.get("title")),
			_safe_string(row.get("assign_to")),
			_safe_string(row.get("due_date")),
			_safe_string(row.get("task_type")),
			_safe_string(row.get("status")),
			_safe_string(row.get("custom_reference_doctype")) if has_ref_doctype else "",
			_safe_string(row.get("custom_reference_document")) if has_ref_docname else "",
			_minute_bucket(row.get("creation")),
		)
		if key in seen:
			to_delete.append(row.name)
		else:
			seen[key] = row.name

	if not to_delete:
		return

	for name in to_delete:
		try:
			frappe.delete_doc("WB Task", name, ignore_permissions=True, force=True)
		except Exception:
			# Fallback hard delete if standard delete is blocked by state/permissions.
			frappe.db.delete("WB Task", {"name": name})
			frappe.db.delete("WB Task Checklist Details", {"parent": name, "parenttype": "WB Task"})

	frappe.db.commit()
