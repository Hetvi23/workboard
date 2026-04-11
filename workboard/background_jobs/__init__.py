import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, flt, getdate, now_datetime, nowdate, today

from workboard.utils import _context, _create_task_from_rule, _eval_proxy, child_row_id_for_wb_task_context

_WB_SAFE_EVAL_GLOBALS = {"len": len, "cint": cint, "flt": flt}


def _safe_eval_rule_expr(expr, ctx):
	"""Evaluate WB Task Rule expression safely; return False on error."""
	try:
		return bool(frappe.safe_eval(expr, dict(_WB_SAFE_EVAL_GLOBALS), ctx))
	except Exception:
		frappe.log_error(title=_("WorkBoard Rule Condition Error"), message=frappe.get_traceback())
		return False


def trigger_daily_rules():
	try:
		_run_recurring_rules()
		_run_offset_rules()
	except Exception:
		frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def _run_recurring_rules():
	rules = frappe.get_all("WB Task Rule", filters={"enabled": 1, "recurring": 1}, fields=["*"])
	if not rules:
		return
	today_dt = getdate(today())
	selected = []
	for r in rules:
		if r.frequency == "Daily":
			selected.append(r)
		elif r.frequency == "Weekly" and today_dt.strftime("%A") == r.day_of_week:
			selected.append(r)
		elif r.frequency == "Fortnightly" and today_dt.strftime("%A") == r.day_of_week:
			# Check if it's a fortnightly occurrence (every 14 days)
			# We use a simple check: if the day number is in the first or third week of the month
			day_of_month = today_dt.day
			if (day_of_month <= 7) or (15 <= day_of_month <= 21):
				selected.append(r)
		elif r.frequency == "Monthly" and cint(today_dt.day) == cint(r.date_of_month):
			selected.append(r)
		elif r.frequency == "Quarterly" and cint(today_dt.day) == cint(r.date_of_month):
			# Quarterly: every 3 months (January, April, July, October)
			if today_dt.month in [1, 4, 7, 10]:
				selected.append(r)
		elif (
			r.frequency == "Yearly"
			and cint(today_dt.day) == cint(r.date_of_month)
			and cint(today_dt.month) == cint(r.month_of_year)
		):
			selected.append(r)
	for r in selected:
		try:
			_create_task_from_rule(r)
			frappe.db.commit()
		except Exception:
			frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def _run_offset_rules(hourly_only=False):
	filters = {
		"enabled": 1,
		"event": 1,
		"based_on": ["in", ["Days Before", "Days After"]],
	}
	if hourly_only:
		filters["hours_before_or_after"] = [">", 0]
	rules = frappe.get_all(
		"WB Task Rule",
		filters=filters,
		fields=["*"],
	)
	for r in rules:
		for ref_doc in _docs_matching_offset_window(r):
			try:
				ctx = _context(ref_doc)
				if r.condition and not _safe_eval_rule_expr(r.condition, ctx):
					continue

				if r.reference_child_table and r.child_table_condition:
					child_rows = ref_doc.get(r.reference_child_table) or []
					for i, row in enumerate(child_rows):
						row_ctx = ctx.copy()
						row_ctx["row"] = _eval_proxy(row)
						row_ctx["child_table_name"] = r.reference_child_table
						row_ctx["child_table_id"] = child_row_id_for_wb_task_context(row, i)
						if _safe_eval_rule_expr(r.child_table_condition, row_ctx):
							_create_task_from_rule(r, context=row_ctx)
					continue

				_create_task_from_rule(r, context=ctx)
			except Exception:
				frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def _docs_matching_offset_window(rule):
	out = []
	if not rule.reference_doctype or not rule.reference_date:
		return out
	if not frappe.db.has_column(rule.reference_doctype, rule.reference_date):
		return out

	diff_days = cint(rule.days_before_or_after or 0)
	diff_hours = cint(rule.get("hours_before_or_after") if isinstance(rule, dict) else getattr(rule, "hours_before_or_after", 0) or 0)

	if diff_days == 0 and diff_hours == 0:
		# No offset — match documents created today
		ref_date = nowdate()
		start = f"{ref_date} 00:00:00.000000"
		end = f"{ref_date} 23:59:59.000000"
	elif diff_hours > 0:
		# Hours-level precision: find docs whose reference_date is exactly
		# (days + hours) ago (for Days After) or in the future (for Days Before).
		# We use a 1-hour matching window around the target datetime.
		now = now_datetime()
		if rule.based_on == "Days After":
			target = add_to_date(now, days=-diff_days, hours=-diff_hours)
		else:
			target = add_to_date(now, days=diff_days, hours=diff_hours)
		# 1-hour window so the scheduler (which runs every ~hour) catches the docs
		window_start = add_to_date(target, hours=-1)
		start = window_start.strftime("%Y-%m-%d %H:%M:%S.000000")
		end = target.strftime("%Y-%m-%d %H:%M:%S.000000")
	else:
		# Days-only offset (original logic)
		diff = diff_days
		if rule.based_on == "Days After":
			diff = -diff
		ref_date = add_to_date(nowdate(), days=diff)
		start = f"{ref_date} 00:00:00.000000"
		end = f"{ref_date} 23:59:59.000000"

	names = frappe.get_all(
		rule.reference_doctype,
		fields=["name"],
		filters=[
			[rule.reference_doctype, rule.reference_date, ">=", start],
			[rule.reference_doctype, rule.reference_date, "<=", end],
		],
	)
	for n in names:
		out.append(frappe.get_doc(rule.reference_doctype, n.name))
	return out


def trigger_hourly_offset_rules():
	"""Hourly scheduler entry: run offset rules that have hours_before_or_after set."""
	try:
		_run_offset_rules(hourly_only=True)
	except Exception:
		frappe.log_error(title=_("WorkBoard Error"), message=frappe.get_traceback())


def update_task_status():
	names = frappe.get_all("WB Task", filters={"status": ["not in", ["Completed"]]}, pluck="name")
	for name in names:
		try:
			d = frappe.get_doc("WB Task", name)
			prev = d.status
			d.validate()
			if d.status != prev:
				d.save(ignore_permissions=True)
		except Exception:
			frappe.log_error(title=_("WorkBoard Status update error"), message=frappe.get_traceback())
