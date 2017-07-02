// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt
cur_frm.add_fetch('grade', 'level_value', 'level_value');

frappe.provide("erpnext.hr");
erpnext.hr.EmployeeController = frappe.ui.form.Controller.extend({
	setup: function() {
		this.frm.fields_dict.user_id.get_query = function(doc, cdt, cdn) {
			return {
				query: "frappe.core.doctype.user.user.user_query",
				filters: {ignore_user_type: 1}
			}
		}
		// this.frm.fields_dict.reports_to.get_query = function(doc, cdt, cdn) {
		// 	return { query: "erpnext.controllers.queries.employee_query"} }
	},

	onload: function() {
		this.frm.set_query("leave_approver", "leave_approvers", function(doc) {
			return {
				query:"erpnext.hr.doctype.employee_leave_approver.employee_leave_approver.get_approvers",
				filters:{
					user: doc.user_id
				}
			}
		});
	},

	refresh: function() {
		var me = this;
		erpnext.toggle_naming_series();
	},

	date_of_birth: function() {
		return cur_frm.call({
			method: "get_retirement_date",
			args: {date_of_birth: this.frm.doc.date_of_birth}
		});
	},

	salutation: function() {
		if(this.frm.doc.salutation) {
			this.frm.set_value("gender", {
				"Mr": "Male",
				"Ms": "Female"
			}[this.frm.doc.salutation]);
		}
	},

});
frappe.ui.form.on('Employee',{
	prefered_contact_email:function(frm){
		frm.events.update_contact(frm)
	},
	personal_email:function(frm){
		frm.events.update_contact(frm)
	},
	company_email:function(frm){
		frm.events.update_contact(frm)
	},
	level:function(frm){
		frm.events.update_level(frm)
	},
	user_id:function(frm){
		frm.events.update_contact(frm)
	},
	employment_type:function(frm){
		if(frm.doc.employment_type =="Full-time")
		{
			frm.set_value("naming_series","EMP/1");
		}
		else
		if(frm.doc.employment_type =="Consalteant")
		{
			frm.set_value("naming_series","EMP/2");
		}
		else
		if(frm.doc.employment_type =="Contractor")
		{
			frm.set_value("naming_series","EMP/3");
		}
	},
	update_contact:function(frm){
		var prefered_email_fieldname = frappe.model.scrub(frm.doc.prefered_contact_email) || 'user_id';
		frm.set_value("prefered_email",
			frm.fields_dict[prefered_email_fieldname].value)
	},
	update_level:function(frm){
		frappe.call({
			  doc: frm.doc,
			  method: "update_level",
			  callback: function(r) {
				 console.log(r.message)
			  }
			});
	},
	status: function(frm) {
		return frm.call({
			method: "deactivate_sales_person",
			args: {
				employee: frm.doc.employee,
				status: frm.doc.status
			}
		});
	},
});
cur_frm.cscript = new erpnext.hr.EmployeeController({frm: cur_frm});
