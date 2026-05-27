frappe.query_reports["Daily Task Tracker"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nNew\nOpen\nExtended\nDone\nCompleted\nOverdue\nCancelled",
		},
		{
			fieldname: "priority",
			label: __("Priority"),
			fieldtype: "Select",
			options: "\nEmergency\nHigh\nMedium\nLow\nRepeat\nReminder",
		},
		{
			fieldname: "assign_to",
			label: __("Assign To"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "assign_from",
			label: __("Assign From"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "wb_task_rule",
			label: __("WB Task Rule"),
			fieldtype: "Link",
			options: "WB Task Rule",
		},
	],
};
