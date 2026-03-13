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
		ctx = _context(doc)
		for r in rules:
			frappe.logger("wb_task_rule").info(f"[WBRule] Evaluating rule={r.name}")

			if event == "Value Change":
				if not r.value_changed or not frappe.db.has_column(doc.doctype, r.value_changed):
					frappe.logger("wb_task_rule").info(f"[WBRule]   SKIP: value_changed field missing or not a column: {r.value_changed}")
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
				# For Save/Submit/Cancel, the in-memory doc may not have the child table
				# populated if it was not sent as part of the form payload. Fall back to DB.
				if not child_rows and event in ("Save", "Submit", "Cancel"):
					try:
						child_field = frappe.get_meta(doc.doctype).get_field(r.reference_child_table)
						if child_field:
							child_rows = frappe.get_all(
								child_field.options,
								filters={"parent": doc.name, "parenttype": doc.doctype, "parentfield": r.reference_child_table},
								fields=["*"],
							)
					except Exception as e:
						frappe.logger("wb_task_rule").info(f"[WBRule]   child_table DB fallback ERROR: {e}")
				frappe.logger("wb_task_rule").info(
					f"[WBRule]   child_table={r.reference_child_table} rows={len(child_rows)} condition={r.child_table_condition!r}"
				)
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
				frappe.logger("wb_task_rule").info(f"[WBRule]   tasks_created={tasks_created}")
				continue

			frappe.logger("wb_task_rule").info(f"[WBRule]   Creating task (no child table condition)")
			_create_task_from_rule(r, context=ctx)
	except Exception:
		frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def _map_method_to_based_on(doc, method):
	m = {"after_insert": "New", "after_save": "Save", "on_submit": "Submit", "on_cancel": "Cancel"}
	if not doc.flags.in_insert:
		m["on_change"] = "Value Change"
	return m.get(method)
