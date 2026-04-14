# Copyright (c) 2026, WorkBoard and contributors
"""Merge duplicate permission rows for WB Task Extension (same role, permlevel, if_owner).

The DocType JSON previously had two System Manager rules at level 0, which violates
Frappe validation and blocks Role Permissions Manager (add/remove rules).
"""
import frappe
from frappe.utils import cint

DOCTYPE = "WB Task Extension"

PERM_FIELDS = (
	"select",
	"read",
	"write",
	"create",
	"delete",
	"submit",
	"cancel",
	"amend",
	"report",
	"import",
	"export",
	"share",
	"print",
	"email",
)


def _merge_in_table(table_doctype: str):
	fields = ["name", "role", "permlevel", "if_owner", *PERM_FIELDS]
	rows = frappe.get_all(
		table_doctype,
		filters={"parent": DOCTYPE},
		fields=fields,
		order_by="name asc",
	)
	if not rows:
		return

	groups = {}
	for row in rows:
		key = (row["role"], cint(row.get("permlevel")), cint(row.get("if_owner")))
		groups.setdefault(key, []).append(row)

	for _key, group in groups.items():
		if len(group) < 2:
			continue
		keeper = group[0]
		merged = {}
		for f in PERM_FIELDS:
			merged[f] = 1 if any(cint(r.get(f) or 0) for r in group) else 0
		frappe.db.set_value(table_doctype, keeper["name"], merged)
		for r in group[1:]:
			frappe.db.delete(table_doctype, {"name": r["name"]})


def execute():
	if not frappe.db.exists("DocType", DOCTYPE):
		return

	_merge_in_table("DocPerm")
	_merge_in_table("Custom DocPerm")

	frappe.clear_cache(doctype=DOCTYPE)
