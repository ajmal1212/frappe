# -*- coding: utf-8 -*-
# Copyright (c) 2018, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.utils import date_diff, getdate, formatdate, cint
from frappe.model.document import Document
from erpnext.hr.doctype.employee.employee import get_holiday_list_for_employee

class PayrollPeriod(Document):
	def validate(self):
		self.validate_dates()
		self.validate_overlap()

	def validate_dates(self):
		if getdate(self.start_date) > getdate(self.end_date):
			frappe.throw(_("End date can not be less than start date"))

	def validate_overlap(self):
		query = """
			select name
			from `tab{0}`
			where name != %(name)s
			and company = %(company)s and (start_date between %(start_date)s and %(end_date)s \
				or end_date between %(start_date)s and %(end_date)s \
				or (start_date < %(start_date)s and end_date > %(end_date)s))
			"""
		if not self.name:
			# hack! if name is null, it could cause problems with !=
			self.name = "New "+self.doctype

		overlap_doc = frappe.db.sql(query.format(self.doctype),{
				"start_date": self.start_date,
				"end_date": self.end_date,
				"name": self.name,
				"company": self.company
			}, as_dict = 1)

		if overlap_doc:
			msg = _("A {0} exists between {1} and {2} (").format(self.doctype,
				formatdate(self.start_date), formatdate(self.end_date)) \
				+ """ <b><a href="#Form/{0}/{1}">{1}</a></b>""".format(self.doctype, overlap_doc[0].name) \
				+ _(") for {0}").format(self.company)
			frappe.throw(msg)

def get_payroll_period_days(start_date, end_date, employee):
	company = frappe.db.get_value("Employee", employee, "company")
	payroll_period_dates = frappe.db.sql("""
	select start_date, end_date from `tabPayroll Period`
	where company=%(company)s
	and (
		(%(start_date)s between start_date and end_date)
		or (%(end_date)s between start_date and end_date)
		or (start_date between %(start_date)s and %(end_date)s)
	)""", {
		'company': company,
		'start_date': start_date,
		'end_date': end_date
	})

	if len(payroll_period_dates) > 0:
		working_days = date_diff(getdate(payroll_period_dates[0][1]), getdate(payroll_period_dates[0][0])) + 1
		if not cint(frappe.db.get_value("HR Settings", None, "include_holidays_in_total_working_days")):
			holidays = get_holidays_for_employee(employee, getdate(payroll_period_dates[0][0]), getdate(payroll_period_dates[0][1]))
			working_days -= len(holidays)
		return working_days
	return False

def get_holidays_for_employee(employee, start_date, end_date):
	holiday_list = get_holiday_list_for_employee(employee)
	holidays = frappe.db.sql_list('''select holiday_date from `tabHoliday`
		where
			parent=%(holiday_list)s
			and holiday_date >= %(start_date)s
			and holiday_date <= %(end_date)s''', {
				"holiday_list": holiday_list,
				"start_date": start_date,
				"end_date": end_date
			})

	holidays = [cstr(i) for i in holidays]

	return holidays
