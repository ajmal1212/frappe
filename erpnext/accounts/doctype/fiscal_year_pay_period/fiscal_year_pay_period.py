# -*- coding: utf-8 -*-
# Copyright (c) 2017, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from dateutil.relativedelta import relativedelta
from frappe import _ as translate
from frappe.model.document import Document
from frappe.utils import getdate, DATE_FORMAT, add_months, add_to_date, add_days, datetime, math, date_diff, time_diff
from six.moves import range


class FiscalYearPayPeriod(Document):
	def validate(self):
		self.validate_dates()
		self.validate_payment_frequency()
		self.validate_period_start_end_dates()

	def validate_period_start_end_dates(self):
		start_date = getdate(self.pay_period_start_date)
		end_date = getdate(self.pay_period_end_date)
		payment_frequency = self.payment_frequency.lower()

		if not dates_interval_valid(start_date, end_date, payment_frequency):
			frappe.throw(
				translate('The end date selected is not valid for {0} frequency.'.format(payment_frequency))
			)

	def validate_payment_frequency(self):
		_validate_payment_frequency(self.payment_frequency)

	def validate_dates(self):
		_validate_dates(self.pay_period_start_date, self.pay_period_end_date)


@frappe.whitelist()
def get_pay_period_dates(payroll_start, payroll_end, payroll_frequency):
	if dates_interval_valid(getdate(payroll_start), getdate(payroll_end), payroll_frequency):
		dates = []
		payroll_frequency = payroll_frequency.lower()
		start = getdate(payroll_start)

		# `frequency_key` contains the maximum number of iterations for each key in the dict
		loop_keys = get_frequency_loop_values()
		frequency_kwarg = get_frequency_kwargs()

		for _ in range(loop_keys.get(payroll_frequency)):
			end = add_to_date(start, **frequency_kwarg.get(payroll_frequency))
			dates.append(
				{
					'start_date': start.strftime(DATE_FORMAT),
					'end_date': add_to_date(end, days=-1).strftime(DATE_FORMAT),
				}
			)

			if end > getdate(payroll_end):
				break
			else:
				start = end

		return dates
	else:
		frappe.throw(
			translate('The end date selected is not valid for {0} frequency.'.format(payroll_frequency))
		)


def get_frequency_kwargs():
	return {
		'bimonthly': {'years': 0, 'months': 2, 'days': 0},
		'monthly': {'years': 0, 'months': 1, 'days': 0},
		'fortnightly': {'years': 0, 'months': 0, 'days': 14},
		'weekly': {'years': 0, 'months': 0, 'days': 7},
		'daily': {'years': 0, 'months': 0, 'days': 1}
	}


def get_frequency_loop_values():
	return {
		'monthly': 12, 'bimonthly': 6,
		'fortnightly': 27, 'weekly': 52,
		'daily': 366
	}


def dates_interval_valid(start, end, frequency):
	frequency = frequency.lower()
	diff = relativedelta(add_days(end, 1), start)
	diff_td = add_days(end, 1) - start

	_validate_dates(start, end)

	if frequency == 'monthly':
		return (diff.months and not diff.days) or \
			(diff.years and not diff.months and not diff.days)
	elif frequency == 'bimonthly':
		return diff.months % 2 == 0 and not diff.days
	elif frequency == 'fortnightly':
		return diff_td.days % 14 == 0
	elif frequency == 'weekly':
		return diff_td.days % 7 == 0
	elif frequency == 'daily':
		return True
	else:
		_validate_payment_frequency(frequency)


def _validate_payment_frequency(payment_frequency):
	if payment_frequency.lower() not in ['monthly', 'fortnightly', 'weekly', 'bimonthly', 'daily']:
		frappe.throw(
			translate('{0} is not a valid Payment Frequency'.format(payment_frequency))
		)


def _validate_dates(start_date, end_date):
	"""
	Checks that `start_date` is earlier than `end_date`.
	Throws `frappe.ValidationError` is otherwise is the case
	"""
	start_date = getdate(start_date)
	end_date = getdate(end_date)
	if getdate(start_date) > getdate(end_date):
		frappe.throw(
			translate('{0} cannot be earlier than {1}'.format(end_date, start_date))
		)
