import frappe
from frappe import _
from frappe.utils import parse_val

from workboard.utils import _context, _create_task_from_rule


def create_task_for_event(doc, method):
	try:
		if (
			(frappe.flags.in_import and frappe.flags.mute_emails)
			or frappe.flags.in_patch
			or frappe.flags.in_install
			or frappe.flags.in_migrate
		):
			return
		event = _map_method_to_based_on(doc, method)
		if not event:
			return
		rules = frappe.get_all(
			"WB Task Rule",
			filters={
				"enabled": 1,
				"event": 1,
				"based_on": event,
				"reference_doctype": doc.doctype,
			},
			fields=["*"],
		)

		frappe.logger("wb_task_rule").info(
			f"[WBRule] doc={doc.doctype}/{doc.name} method={method} event={event} rules_found={len(rules)}"
		)

		if not rules:
			return

		# Deduplicate: skip if a task was already created for this doc+rule in this request
		# (prevents double-firing when both after_save and on_change match the same rule condition)
		created_key = f"_wb_task_created_{doc.doctype}_{doc.name}"
		already_created = frappe.flags.get(created_key) or set()

		ctx = _context(doc)
		for r in rules:
			if r.name in already_created:
				frappe.logger("wb_task_rule").info(f"[WBRule] SKIP (already created this request): rule={r.name}")
				continue
			frappe.logger("wb_task_rule").info(f"[WBRule] Evaluating rule={r.name}")

			if event == "Value Change":
				if not r.value_changed:
					frappe.logger("wb_task_rule").info(f"[WBRule]   SKIP: value_changed is empty")
					continue

				# Support child table value change: "child_table_field.child_fieldname"
				if "." in r.value_changed:
					table_fieldname, child_fieldname = r.value_changed.split(".", 1)
					meta = frappe.get_meta(doc.doctype)
					table_df = meta.get_field(table_fieldname) if meta else None
					if not table_df or table_df.fieldtype != "Table" or not table_df.options:
						frappe.logger("wb_task_rule").info(
							f"[WBRule]   SKIP: value_changed table field invalid: {r.value_changed}"
						)
						continue
					child_doctype = table_df.options
					if not frappe.db.has_column(child_doctype, child_fieldname):
						frappe.logger("wb_task_rule").info(
							f"[WBRule]   SKIP: child field is not a column: {child_doctype}.{child_fieldname}"
						)
						continue

					doc_before_save = doc.get_doc_before_save()

					def _get_rows_from_doc(d):
						return (d.get(table_fieldname) or []) if d else []

					def _get_rows_from_db():
						return frappe.get_all(
							child_doctype,
							filters={
								"parent": doc.name,
								"parenttype": doc.doctype,
								"parentfield": table_fieldname,
							},
							fields=["name", child_fieldname, "idx"],
							order_by="idx asc",
						)

					current_rows = doc.get(table_fieldname) or []
					before_rows = _get_rows_from_doc(doc_before_save)

					# For safety if before_rows isn't present, load from DB
					if doc_before_save is None or before_rows is None:
						before_rows = _get_rows_from_db()

					def _row_key(row):
						# Prefer stable child row name; fall back to idx position
						if hasattr(row, "get"):
							return row.get("name") or row.get("idx")
						return getattr(row, "name", None) or getattr(row, "idx", None)

					def _row_val(row):
						val = row.get(child_fieldname) if hasattr(row, "get") else getattr(row, child_fieldname, None)
						return parse_val(val)

					before_map = { _row_key(rw): _row_val(rw) for rw in (before_rows or []) }
					current_map = { _row_key(rw): _row_val(rw) for rw in (current_rows or []) }

					# Detect row add/remove or any value change
					changed = False
					all_keys = set(before_map.keys()) | set(current_map.keys())
					if len(before_map) != len(current_map):
						changed = True
					else:
						for k in all_keys:
							if before_map.get(k) != current_map.get(k):
								changed = True
								break

					frappe.logger("wb_task_rule").info(
						f"[WBRule]   Value Change (child) check: field={r.value_changed} changed={changed}"
					)
					if not changed:
						frappe.logger("wb_task_rule").info(f"[WBRule]   SKIP: value has not changed (child)")
						continue

				else:
					# Parent (non–child-table) field: compare before vs after on the main document only
					if not frappe.db.has_column(doc.doctype, r.value_changed):
						frappe.logger("wb_task_rule").info(
							f"[WBRule]   SKIP: value_changed field missing or not a column: {r.value_changed}"
						)
						continue
					doc_before_save = doc.get_doc_before_save()
					field_value_before_save = doc_before_save.get(r.value_changed) if doc_before_save else None
					field_value_before_save = parse_val(field_value_before_save)
					current_value = doc.get(r.value_changed)
					frappe.logger("wb_task_rule").info(
						f"[WBRule]   Value Change check: field={r.value_changed} before={field_value_before_save!r} after={current_value!r}"
					)
					if current_value == field_value_before_save:
						frappe.logger("wb_task_rule").info(f"[WBRule]   SKIP: value has not changed")
						continue

			if r.condition:
				result = frappe.safe_eval(r.condition, None, ctx)
				frappe.logger("wb_task_rule").info(f"[WBRule]   condition={r.condition!r} result={result}")
				if not result:
					frappe.logger("wb_task_rule").info(f"[WBRule]   SKIP: condition is False")
					continue

			if r.reference_child_table and r.child_table_condition:
				child_rows = doc.get(r.reference_child_table) or []
				# For Save/Submit/Cancel, in-memory doc may not include child table rows.
				if not child_rows and event in ("Save", "Submit", "Cancel"):
					try:
						child_field = frappe.get_meta(doc.doctype).get_field(r.reference_child_table)
						if child_field:
							child_rows = frappe.get_all(
								child_field.options,
								filters={
									"parent": doc.name,
									"parenttype": doc.doctype,
									"parentfield": r.reference_child_table,
								},
								fields=["*"],
							)
					except Exception as e:
						frappe.logger("wb_task_rule").info(f"[WBRule]   child_table DB fallback ERROR: {e}")

				frappe.logger("wb_task_rule").info(
					f"[WBRule]   child_table={r.reference_child_table} rows={len(child_rows)} condition={r.child_table_condition!r}"
				)

				# IMPORTANT: trigger once if any row matches (not once per matching row)
				tasks_created = False
				for i, row in enumerate(child_rows):
					row_ctx = ctx.copy()
					row_ctx["row"] = row
					try:
						row_result = frappe.safe_eval(r.child_table_condition, None, row_ctx)
					except Exception as e:
						frappe.logger("wb_task_rule").info(f"[WBRule]   row[{i}] condition ERROR: {e}")
						continue

					frappe.logger("wb_task_rule").info(f"[WBRule]   row[{i}] result={row_result}")
					if row_result:
						_create_task_from_rule(r, context=row_ctx)
						tasks_created = True
						already_created.add(r.name)
						frappe.flags[created_key] = already_created
						break

				frappe.logger("wb_task_rule").info(f"[WBRule]   tasks_created={tasks_created}")
				continue

			frappe.logger("wb_task_rule").info(f"[WBRule]   Creating task (no child table condition)")
			_create_task_from_rule(r, context=ctx)
			already_created.add(r.name)
			frappe.flags[created_key] = already_created
	except Exception:
		frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def _map_method_to_based_on(doc, method):
	m = {"after_insert": "New", "after_save": "Save", "on_submit": "Submit", "on_cancel": "Cancel"}
	if not doc.flags.in_insert:
		m["on_change"] = "Value Change"
	return m.get(method)
