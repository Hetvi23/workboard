# Copyright (c) 2025, Nesscale Solutions Pvt Ltd and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class WBTaskRule(Document):
	def validate(self):
		# Rules that use Event Settings (reference doctype + based_on) must have Event=1,
		# otherwise get_all(..., filters={"event": 1}) never returns them.
		if self.recurring:
			return
		if self.based_on and self.based_on in ("New", "Save", "Submit", "Cancel", "Value Change"):
			if self.reference_doctype and not cint(self.event):
				self.event = 1
