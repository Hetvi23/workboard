import frappe
from frappe import _
from frappe.utils import cint, flt, parse_val

from workboard.utils import _context, _create_task_from_rule, _eval_proxy

# frappe.safe_eval() whitelist does not include len/cint/flt; child_table_condition often needs them.
_WB_SAFE_EVAL_GLOBALS = {"len": len, "cint": cint, "flt": flt}


def _safe_eval_rule_expr(expr, ctx, log_prefix):
	"""Evaluate WB Task Rule expression safely; return False on error."""
	try:
		return bool(frappe.safe_eval(expr, dict(_WB_SAFE_EVAL_GLOBALS), ctx))
	except Exception as e:
		frappe.logger("wb_task_rule").info(f"{log_prefix} ERROR evaluating expression {expr!r}: {e}")
		return False


def _child_field_val_for_compare(row, child_fieldname, child_doctype=None):
	"""Normalize child field values for before/after diff.

	- Float/Currency/Int/Percent: flt() so 10 vs 10.0 matches.
	- Check: cint().
	- Select / Data / Link / other strings: stripped string (do NOT use flt — flt('Available') is 0 and breaks change detection).
	"""
	val = row.get(child_fieldname) if hasattr(row, "get") else getattr(row, child_fieldname, None)
	val = parse_val(val)
	if val is None:
		return None

	if child_doctype:
		df = frappe.get_meta(child_doctype).get_field(child_fieldname)
		if df:
			if df.fieldtype == "Check":
				return cint(val)
			if df.fieldtype in ("Float", "Currency", "Int", "Percent"):
				try:
					return flt(val)
				except Exception:
					return val
	if isinstance(val, str):
		return val.strip()
	try:
		return flt(val)
	except Exception:
		return val


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

		def _create_once(rule_dict, task_ctx, task_key):
			"""Create a WB Task. De-dupe using task_key (rule + optional child row id)."""
			if task_key in already_created:
				return
			already_created.add(task_key)
			frappe.flags[created_key] = already_created
			try:
				_create_task_from_rule(rule_dict, context=task_ctx)
			except Exception:
				already_created.discard(task_key)
				frappe.flags[created_key] = already_created
				raise

		for r in rules:
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

					current_rows = doc.get(table_fieldname) or []

					def _row_key(row):
						# Prefer stable child row name; fall back to idx position
						if hasattr(row, "get"):
							return row.get("name") or row.get("idx")
						return getattr(row, "name", None) or getattr(row, "idx", None)

					def _row_val(row):
						return _child_field_val_for_compare(row, child_fieldname, child_doctype)

					# Never use DB rows as "before" state here: after save, DB already matches current — diff would be empty.
					if doc_before_save is None:
						frappe.logger("wb_task_rule").info(
							f"[WBRule]   Value Change (child): doc_before_save missing for {doc.doctype}/{doc.name}; "
							"treating as changed (cannot compare)"
						)
						changed = True
					else:
						before_rows = _get_rows_from_doc(doc_before_save) or []
						before_map = {_row_key(rw): _row_val(rw) for rw in before_rows}
						current_map = {_row_key(rw): _row_val(rw) for rw in (current_rows or [])}

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
				result = _safe_eval_rule_expr(r.condition, ctx, "[WBRule]   condition")
				frappe.logger("wb_task_rule").info(f"[WBRule]   condition={r.condition!r} result={result}")
				if not result:
					frappe.logger("wb_task_rule").info(f"[WBRule]   SKIP: condition is False")
					continue

			child_cond = (r.child_table_condition or "").strip()
			if r.reference_child_table and child_cond:
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
					f"[WBRule]   child_table={r.reference_child_table} rows={len(child_rows)} condition={child_cond!r}"
				)

				# Create one task per matching child row
				for i, row in enumerate(child_rows):
					row_ctx = ctx.copy()
					row_ctx["row"] = _eval_proxy(row)
					row_ctx["child_table_name"] = r.reference_child_table
					child_row_id = row.get("idx") if hasattr(row, "get") else None
					child_row_id = child_row_id or (row.get("name") if hasattr(row, "get") else None) or i
					row_ctx["child_table_id"] = child_row_id
					row_result = _safe_eval_rule_expr(child_cond, row_ctx, f"[WBRule]   row[{i}] condition")

					frappe.logger("wb_task_rule").info(f"[WBRule]   row[{i}] result={row_result}")
					if row_result:
						task_key = f"{r.name}|{r.reference_child_table}|{child_row_id}"
						_create_once(r, row_ctx, task_key=task_key)
				continue

			frappe.logger("wb_task_rule").info(f"[WBRule]   Creating task (no child table condition)")
			_create_once(r, ctx, task_key=r.name)
	except Exception:
		frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def _map_method_to_based_on(doc, method):
	# Note: Frappe Document._save does not call run_method("after_save"); use on_update for "Save" rules.
	m = {"after_insert": "New", "after_save": "Save", "on_submit": "Submit", "on_cancel": "Cancel"}
	if not doc.flags.in_insert:
		m["on_change"] = "Value Change"
		m["on_update"] = "Save"
	return m.get(method)
