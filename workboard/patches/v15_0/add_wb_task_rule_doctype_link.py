# Copyright (c) 2025, WorkBoard and contributors
"""Add DocType Link so WB Task Rule form shows Connection tab with WB Task (group: Reference)."""
import frappe


def execute():
	existing = frappe.get_all(
		"DocType Link",
		filters={"parent": "WB Task Rule", "link_doctype": "WB Task", "link_fieldname": "wb_task_rule"},
		limit=1,
	)
	if existing:
		return
	doc = frappe.get_doc("DocType", "WB Task Rule")
	doc.append(
		"links",
		{"link_doctype": "WB Task", "link_fieldname": "wb_task_rule", "group": "Reference"},
	)
	doc.save(ignore_permissions=True)
	frappe.db.commit()
