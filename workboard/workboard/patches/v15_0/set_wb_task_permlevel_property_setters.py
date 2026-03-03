from __future__ import annotations

import frappe


def execute():
	"""Set permlevel = 1 for all WB Task fields via Property Setters,
	except: proof_of_work and task_completion_remark (permlevel 2),
	and checklist_template and wb_task_checklist_details (permlevel 0)."""

	doctype = "WB Task"
	exclude_fields = {
		"proof_of_work",
		"task_completion_remark",
		"checklist_template",
		"wb_task_checklist_details",
	}

	doc = frappe.get_doc("DocType", doctype)

	for df in doc.fields:
		fieldname = df.fieldname
		if not fieldname or fieldname in exclude_fields:
			continue

		# Either update existing Property Setter or create a new one
		existing_name = frappe.db.exists(
			"Property Setter",
			{"doc_type": doctype, "field_name": fieldname, "property": "permlevel"},
		)

		if existing_name:
			ps = frappe.get_doc("Property Setter", existing_name)
			ps.value = "1"
			ps.property_type = "Int"
			ps.doctype_or_field = "DocField"
			ps.save(ignore_permissions=True)
		else:
			ps = frappe.get_doc(
				{
					"doctype": "Property Setter",
					"doctype_or_field": "DocField",
					"doc_type": doctype,
					"field_name": fieldname,
					"property": "permlevel",
					"value": "1",
					"property_type": "Int",
				}
			)
			ps.insert(ignore_permissions=True)

	# Ensure permlevel = 0 for checklist fields (excluded from permlevel 1 above)
	for fieldname in ("checklist_template", "wb_task_checklist_details"):
		existing_name = frappe.db.exists(
			"Property Setter",
			{"doc_type": doctype, "field_name": fieldname, "property": "permlevel"},
		)
		if existing_name:
			ps = frappe.get_doc("Property Setter", existing_name)
			ps.value = "0"
			ps.property_type = "Int"
			ps.doctype_or_field = "DocField"
			ps.save(ignore_permissions=True)
		else:
			frappe.get_doc(
				{
					"doctype": "Property Setter",
					"doctype_or_field": "DocField",
					"doc_type": doctype,
					"field_name": fieldname,
					"property": "permlevel",
					"value": "0",
					"property_type": "Int",
				}
			).insert(ignore_permissions=True)

	frappe.db.commit()

