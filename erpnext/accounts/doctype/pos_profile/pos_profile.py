# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import frappe
from frappe import msgprint, _
from frappe.utils import cint

from frappe.model.document import Document

class POSProfile(Document):
	def validate(self):
		self.check_for_duplicate()
		self.validate_all_link_fields()

	def check_for_duplicate(self):
		res = frappe.db.sql("""select name, user from `tabPOS Profile`
			where ifnull(user, '') = %s and name != %s and organization = %s""",
			(self.user, self.name, self.organization))
		if res:
			if res[0][1]:
				msgprint(_("POS Profile {0} already created for user: {1} and organization {2}").format(res[0][0],
					res[0][1], self.organization), raise_exception=1)
			else:
				msgprint(_("Global POS Profile {0} already created for organization {1}").format(res[0][0],
					self.organization), raise_exception=1)

	def validate_all_link_fields(self):
		accounts = {"Account": [self.cash_bank_account, self.income_account,
			self.expense_account], "Cost Center": [self.cost_center],
			"Warehouse": [self.warehouse]}

		for link_dt, dn_list in accounts.items():
			for link_dn in dn_list:
				if link_dn and not frappe.db.exists({"doctype": link_dt,
						"organization": self.organization, "name": link_dn}):
					frappe.throw(_("{0} does not belong to organization {1}").format(link_dn, self.organization))

	def on_update(self):
		self.set_defaults()

	def on_trash(self):
		self.set_defaults(include_current_pos=False)

	def set_defaults(self, include_current_pos=True):
		frappe.defaults.clear_default("is_pos")

		if not include_current_pos:
			condition = " where name != '%s'" % self.name.replace("'", "\'")
		else:
			condition = ""

		pos_view_users = frappe.db.sql_list("""select user
			from `tabPOS Profile` {0}""".format(condition))

		for user in pos_view_users:
			if user:
				frappe.defaults.set_user_default("is_pos", 1, user)
			else:
				frappe.defaults.set_global_default("is_pos", 1)

@frappe.whitelist()
def get_series():
	return frappe.get_meta("Sales Invoice").get_field("naming_series").options or ""
