// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Payment Period Based On Invoice Date"] = {
	"filters": [
		{
			fieldname:"organization",
			label: __("organization"),
			fieldtype: "Link",
			options: "organization",
			reqd: 1,
			default: frappe.defaults.get_user_default("organization")
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.defaults.get_user_default("year_start_date"),
		},
		{
			fieldname:"to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: get_today()
		},
		{
			fieldname:"payment_type",
			label: __("Payment Type"),
			fieldtype: "Select",
			options: "Incoming\nOutgoing",
			default: "Incoming"
		},
		{
			"fieldname":"party_type",
			"label": __("Party Type"),
			"fieldtype": "Link",
			"options": "DocType",
			"get_query": function() {
				return {
					filters: {"name": ["in", ["Customer", "Supplier"]]}
				}
			}
		},
		{
			"fieldname":"party",
			"label": __("Party"),
			"fieldtype": "Dynamic Link",
			"get_options": function() {
				var party_type = frappe.query_report.filters_by_name.party_type.get_value();
				var party = frappe.query_report.filters_by_name.party.get_value();
				if(party && !party_type) {
					frappe.throw(__("Please select Party Type first"));
				}
				return party_type;
			}
		},
	]
}
