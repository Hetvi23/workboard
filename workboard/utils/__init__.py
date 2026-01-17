import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate, now_datetime, nowdate
from frappe.utils.safe_exec import get_safe_globals


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

	# Calculate end_datetime if time-based task
	end_datetime = None
	depends_on_time = cint(rule.depends_on_time or 0)
	if depends_on_time and rule.time_limit_in_minutes:
		end_datetime = add_to_date(now_datetime(), minutes=cint(rule.time_limit_in_minutes))

	# Use Administrator as default assign_from for recurring/event tasks if not specified
	assign_from = rule.assign_from
	if not assign_from and (cint(rule.recurring or 0) or cint(rule.event or 0)):
		assign_from = "Administrator"

	# Resolve assign_to based on assign_to_type
	assign_to = _resolve_assign_to(rule, context=context)
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
