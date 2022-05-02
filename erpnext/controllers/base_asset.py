# Copyright (c) 2021, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt, cint, getdate, get_datetime, add_months, format_date, nowdate
from frappe.utils.data import get_link_to_form, get_last_day
import json

from erpnext.controllers.accounts_controller import AccountsController
from erpnext.accounts.general_ledger import make_gl_entries, make_reverse_gl_entries
from erpnext.assets.doctype.asset_activity.asset_activity import create_asset_activity
from erpnext.assets.doctype.asset_category.asset_category import get_asset_category_account

from assets.asset.doctype.depreciation_schedule_.depreciation_schedule_ import (
	create_depreciation_schedules,
	create_a_single_depreciation_schedule,
	delete_existing_schedules
)


class BaseAsset(AccountsController):
	def validate(self):
		if self.doctype == "Asset Serial No":
			self.get_asset_values()

		self.validate_number_of_assets()
		self.get_enable_finance_books_value()
		self.set_missing_values()

		if self.is_not_serialized_asset() and self.is_depreciable_asset():
			# since depreciation details will only be entered later for Asset Serial Nos
			if not(self.doctype == "Asset Serial No" and self.is_new()):
				self.validate_depreciation_template_fields()
				self.validate_available_for_use_date()
				self.validate_depreciation_posting_start_date()
				self.validate_salvage_value()
				self.validate_opening_accumulated_depreciation()

				if self.is_new():
					self.set_initial_asset_value_for_finance_books()
				else:
					self.create_schedules_if_depr_details_have_been_updated()

		self.status = self.get_status()

	def before_submit(self):
		if self.is_not_serialized_asset():
			if self.is_depreciable_asset():
				self.submit_depreciation_schedules()

			if not self.flags.split_asset:
				self.record_asset_purchase()
				self.record_asset_creation()
				self.record_asset_receipt()

			if not self.booked_fixed_asset and self.validate_make_gl_entry():
				self.make_gl_entries()

		self.set_status()

	def on_cancel(self):
		self.validate_cancellation()
		self.cancel_movement_entries()
		self.delete_depreciation_entries()
		self.delete_depreciation_schedules()
		self.set_status()

		self.ignore_linked_doctypes = ("GL Entry", "Stock Ledger Entry")
		make_reverse_gl_entries(voucher_type=self.doctype, voucher_no=self.name)
		self.db_set("booked_fixed_asset", 0)

	# to reduce number of db calls
	def get_asset_values(self):
		self.asset_values = frappe.get_value(
			"Asset",
			self.asset,
			[
				"calculate_depreciation",
				"num_of_assets",
				"asset_category",
				"gross_purchase_amount",
				"opening_accumulated_depreciation",
				"purchase_date",
				"asset_name",
				"company",
				"cost_center",
				"purchase_receipt",
				"purchase_invoice",
				"is_existing_asset"
			],
			as_dict = 1
		)

	def is_not_serialized_asset(self):
		"""
			Certain actions should only be performed on Asset Serial No docs or non-serialized Assets.
		"""
		if self.doctype == "Asset Serial No" or not self.is_serialized_asset:
			return True

		return False

	def is_depreciable_asset(self):
		if self.doctype == "Asset":
			return self.calculate_depreciation
		else:
			if not self.get("asset_values"):
				self.get_asset_values()

			return self.asset_values["calculate_depreciation"]

	def validate_number_of_assets(self):
		if self.doctype == "Asset" and self.num_of_assets <= 0:
			frappe.throw(_("Number of Assets needs to be greater than zero."))

		purchase_doctype, purchase_docname = get_purchase_details(self)

		if purchase_docname:
			num_of_items_in_purchase_doc = get_num_of_items_in_purchase_doc(self, purchase_doctype, purchase_docname)
			num_of_assets_already_created = self.get_num_of_assets_already_created(purchase_doctype, purchase_docname)
			num_of_assets = self.get_num_of_assets_in_this_group()

			self.validate_num_of_assets_purchased(num_of_assets, num_of_items_in_purchase_doc, purchase_docname)
			self.validate_total_num_of_assets(num_of_assets, num_of_assets_already_created,
				num_of_items_in_purchase_doc, purchase_docname)

	def get_num_of_assets_already_created(self, purchase_doctype, purchase_docname):
		purchase_doctype = "purchase_receipt" if purchase_doctype == "Purchase Receipt" else "purchase_invoice"
		asset_name = self.name if self.doctype == "Asset" else self.asset

		num_of_assets_already_created = frappe.db.get_all(
			"Asset",
			filters = {
				purchase_doctype: purchase_docname,
				"name": ["!=", asset_name]
			},
			pluck = "num_of_assets"
		)
		num_of_assets_already_created = sum(num_of_assets_already_created)

		return num_of_assets_already_created

	def get_num_of_assets_in_this_group(self):
		if self.doctype == "Asset":
			return self.num_of_assets
		else:
			return self.asset_values["num_of_assets"]

	def validate_num_of_assets_purchased(self, num_of_assets, num_of_items_in_purchase_doc, purchase_docname):
		if num_of_assets > num_of_items_in_purchase_doc:
			frappe.throw(_("Number of Assets cannot be greater than the qty of {0} purchased in {1}, \
				which is {2}.").format(frappe.bold(self.item_code), frappe.bold(purchase_docname),
				frappe.bold(int(num_of_items_in_purchase_doc))))

	def validate_total_num_of_assets(self, num_of_assets, num_of_assets_already_created, num_of_items_in_purchase_doc, purchase_docname):
		if (num_of_assets_already_created + num_of_assets) > num_of_items_in_purchase_doc:
			max_num_of_assets = num_of_items_in_purchase_doc - num_of_assets_already_created

			frappe.throw(_("The Number of Assets to be created needs to be decreased. \
				A maximum of {0} Assets can be created now, as only {1} were purchased in {2}, \
				of which {3} have already been created.")
				.format(frappe.bold(int(max_num_of_assets)), frappe.bold(int(num_of_items_in_purchase_doc)),
				frappe.bold(purchase_docname), frappe.bold(int(num_of_assets_already_created))),
				title=_("Number of Assets Exceeded Limit"))

	def set_missing_values(self):
		if not self.get("asset_value") and self.is_not_serialized_asset():
			self.set_initial_asset_value()

		if self.enable_finance_books and self.is_depreciable_asset() and not self.get("finance_books"):
			asset_category = self.get_asset_category()
			finance_books = get_finance_books(asset_category)
			self.set("finance_books", finance_books)

		elif self.doctype == "Asset" and not self.get("asset_category"):
			self.set_asset_category()

	def set_initial_asset_value(self):
		self.asset_value = self.get_initial_asset_value()

	def get_initial_asset_value(self):
		purchase_doc = get_purchase_details(self)
		gross_purchase_amount, opening_accumulated_depreciation = self.get_gross_purchase_amount_and_opening_accumulated_depreciation()

		if self.is_depreciable_asset() and not purchase_doc:
			asset_value = gross_purchase_amount - opening_accumulated_depreciation
		else:
			asset_value = gross_purchase_amount

		return asset_value

	def get_asset_category(self):
		if self.doctype == "Asset":
			if not self.get("asset_category"):
				self.set_asset_category()

			return self.asset_category
		else:
			return self.asset_values["asset_category"]

	def set_asset_category(self):
		if not self.get("asset_category"):
			self.asset_category = frappe.get_cached_value("Item", self.item_code, "asset_category")

	def get_gross_purchase_amount_and_opening_accumulated_depreciation(self):
		if self.doctype == "Asset":
			return self.gross_purchase_amount, self.opening_accumulated_depreciation
		else:
			return self.asset_values["gross_purchase_amount"], self.asset_values["opening_accumulated_depreciation"]

	def get_enable_finance_books_value(self):
		self.enable_finance_books = frappe.db.get_single_value("Accounts Settings", "enable_finance_books")

	def validate_available_for_use_date(self):
		purchase_date = self.get_purchase_date()

		if self.available_for_use_date and getdate(self.available_for_use_date) < getdate(purchase_date):
			frappe.throw(_("Available-for-use Date should be after purchase date"))

	def validate_depreciation_posting_start_date(self):
		if not self.depreciation_posting_start_date:
			self.depreciation_posting_start_date = get_last_day(self.available_for_use_date)

		if self.depreciation_posting_start_date == self.available_for_use_date:
			frappe.throw(_("Depreciation Posting Date should not be equal to Available for Use Date."),
				title=_("Incorrect Date"))

		if not self.enable_finance_books:
			self.check_if_depr_posting_start_date_is_too_late(self.frequency_of_depreciation)
		else:
			for row in self.finance_books:
				self.check_if_depr_posting_start_date_is_too_late(row.frequency_of_depreciation, row.idx)

	def check_if_depr_posting_start_date_is_too_late(self, frequency_of_depreciation, row = None):
		from assets.asset.doctype.depreciation_schedule_.depreciation_schedule_ import get_frequency_of_depreciation_in_months

		freq_of_depr = get_frequency_of_depreciation_in_months(frequency_of_depreciation)
		latest_possible_depr_posting_start_date = add_months(self.available_for_use_date, freq_of_depr)

		if self.depreciation_posting_start_date > latest_possible_depr_posting_start_date:
			message = _("Depreciation Posting Start Date cannot be after {0} as the Available for Use Date  \
				is {1} and the Frequency of Depreciation is {2}").format(
					frappe.bold(format_date(latest_possible_depr_posting_start_date)),
					frappe.bold(format_date(self.available_for_use_date)),
					frappe.bold(frequency_of_depreciation)
				)

			if row:
				message += _(" in Row {} of the Template Details table.").format(row)

			frappe.throw(message, title = _("Invalid Depreciation Posting Start Date"))

	def validate_salvage_value(self):
		gross_purchase_amount = self.get("gross_purchase_amount") \
			if self.doctype == "Asset" \
			else self.asset_values["gross_purchase_amount"]

		if self.salvage_value and flt(self.salvage_value) >= flt(gross_purchase_amount):
			frappe.throw(
				_("Expected Value After Useful Life must be less than Gross Purchase Amount"),
				title = _("Invalid Salvage Value"),
			)

	def validate_opening_accumulated_depreciation(self):
		if self.doctype == "Asset":
			gross_purchase_amount = self.gross_purchase_amount
			is_existing_asset = self.is_existing_asset
		else:
			gross_purchase_amount = self.asset_values["gross_purchase_amount"]
			is_existing_asset = self.asset_values["is_existing_asset"]

		if is_existing_asset:
			depreciable_amount = flt(gross_purchase_amount) - flt(self.salvage_value)

			if flt(self.opening_accumulated_depreciation) > depreciable_amount:
				frappe.throw(
					_("Opening Accumulated Depreciation must be less than equal to {0}").format(
						depreciable_amount
					)
				)

	def validate_depreciation_template_fields(self):
		if self.enable_finance_books:
			if not self.finance_books:
				frappe.throw(_("Please enter Depreciation Template Details"), title = _("Missing Values"))

			for row in self.finance_books:
				self.set_missing_template_values(row)
		else:
			self.set_missing_template_values()

	def set_missing_template_values(self, row = None):
		row_or_doc = self.get_row_or_doc(row)
		self.validate_depreciation_template(row_or_doc, row)

		depr_method, freq_of_depr, asset_life, asset_life_unit, rate_of_depr = self.fetch_template_values(row_or_doc)

		if not row_or_doc.depreciation_method:
			row_or_doc.depreciation_method = depr_method

		if not row_or_doc.frequency_of_depreciation:
			row_or_doc.frequency_of_depreciation = freq_of_depr

		if not row_or_doc.asset_life_in_months:
			if asset_life_unit == "Months":
				row_or_doc.asset_life_in_months = asset_life
			else:
				row_or_doc.asset_life_in_months = asset_life * 12

		if row_or_doc.depreciation_method == "Written Down Value" and not row_or_doc.rate_of_depreciation:
			row_or_doc.rate_of_depreciation = rate_of_depr

	def get_row_or_doc(self, row):
		if row:
			return row
		else:
			return self

	def validate_depreciation_template(self, row_or_doc, row):
		if not row_or_doc.depreciation_template:
			message = _("Please enter Depreciation Template in the Template Details table")

			if row:
				message = _("Row {0}: ").format(row.idx) + message

			frappe.throw(message, title = _("Missing Depreciation Template"))

	def fetch_template_values(self, row_or_doc):
		return frappe.get_value(
			"Depreciation Template",
			row_or_doc.depreciation_template,
			["depreciation_method", "frequency_of_depreciation", "asset_life", "asset_life_unit", "rate_of_depreciation"]
		)

	def create_schedules_if_depr_details_have_been_updated(self):
		if self.has_updated_basic_depr_details():
			delete_existing_schedules(self)
			create_depreciation_schedules(self)

			if self.has_value_changed("gross_purchase_amount"):
				self.set_initial_asset_value()

			self.set_initial_asset_value_for_finance_books()
			return

		if self.enable_finance_books:
			doc_before_save = self.get_doc_before_save()

			if self.has_updated_finance_books(doc_before_save):
				old_finance_books = doc_before_save.get("finance_books")

				self.delete_schedules_belonging_to_deleted_finance_books(old_finance_books)
				self.create_new_schedules_for_new_finance_books(old_finance_books)

				self.set_initial_asset_value_for_finance_books()
		else:
			if self.has_updated_template_details():
				delete_existing_schedules(self)
				create_depreciation_schedules(self)

	def set_initial_asset_value_for_finance_books(self):
		for row in self.get("finance_books"):
			row.asset_value = self.asset_value

	def update_asset_value(self, change_in_value=0):
		if self.get("finance_books"):
			self.asset_value = self.finance_books[0].asset_value
		else:
			self.asset_value += change_in_value

	def has_updated_basic_depr_details(self):
		return self.has_value_changed("available_for_use_date") or self.has_value_changed("gross_purchase_amount") \
			or self.has_value_changed("depreciation_posting_start_date") or self.has_value_changed("salvage_value") \
			or self.has_value_changed("opening_accumulated_depreciation")

	def has_updated_finance_books(self, doc_before_save):
		return doc_before_save.get("finance_books") != self.get("finance_books")

	def has_updated_template_details(self):
		return self.has_value_changed("depreciation_template") or self.has_value_changed("depreciation_method") \
			or self.has_value_changed("frequency_of_depreciation") or self.has_value_changed("asset_life_in_months") \
			or self.has_value_changed("rate_of_depreciation")

	def create_new_schedules_for_new_finance_books(self, old_finance_books):
		for fb in self.finance_books:
			if fb not in old_finance_books:
				delete_existing_schedules(self, fb)
				create_a_single_depreciation_schedule(self, fb)
			else:
				old_finance_books.remove(fb)

	def delete_schedules_belonging_to_deleted_finance_books(self, old_finance_books):
		for fb in old_finance_books:
			delete_existing_schedules(self, fb)

	def get_purchase_date(self):
		if self.doctype == "Asset":
			return self.purchase_date
		else:
			return self.asset_values["purchase_date"]

	def submit_depreciation_schedules(self, notes=None):
		filters = {
			"asset": self.get_asset(),
			"serial_no": self.get_serial_no()
		}

		depreciation_schedules = frappe.get_all(
			"Depreciation Schedule",
			filters = filters,
			fields = ["name", "status"]
		)

		for schedule in depreciation_schedules:
			if schedule["status"] == "Draft":
				ds = frappe.get_doc("Depreciation Schedule", schedule["name"])
				ds.submit()
			elif schedule["status"] == "Active":
				self.cancel_active_schedule(schedule["name"], notes)

	def cancel_active_schedule(self, schedule_name, notes):
		active_schedule = frappe.get_doc("Depreciation Schedule", schedule_name)

		active_schedule.flags.ignore_validate_update_after_submit = True
		active_schedule.notes = notes
		active_schedule.status = "Cancelled"
		active_schedule.save()

		active_schedule.cancel()

	def record_asset_purchase(self):
		purchase_doctype, purchase_docname = get_purchase_details(self)

		if purchase_docname:
			serial_no = self.get_serial_no()
			asset = self.get_asset()

			create_asset_activity(
				asset = asset,
				asset_serial_no = serial_no,
				activity_type = "Purchase",
				reference_doctype = purchase_doctype,
				reference_docname = purchase_docname,
				activity_date = self.get_purchase_date()
			)

	def record_asset_creation(self):
		asset = self.get_asset()
		serial_no = self.get_serial_no()

		create_asset_activity(
			asset = asset,
			asset_serial_no = serial_no,
			activity_type = "Creation",
			reference_doctype = self.doctype,
			reference_docname = self.name
		)

	def record_asset_receipt(self):
		reference_doctype, reference_docname = get_purchase_details(self)
		transaction_date = getdate(self.get_purchase_date())
		serial_no = self.get_serial_no()
		asset = self.get_asset()
		asset_name, company = self.get_asset_details()

		if reference_docname:
			posting_date, posting_time = frappe.db.get_value(
				reference_doctype, reference_docname, ["posting_date", "posting_time"]
			)
			transaction_date = get_datetime("{} {}".format(posting_date, posting_time))

		assets = [{
			"asset": asset,
			"asset_name": asset_name,
			"serial_no": serial_no,
			"target_location": self.location,
			"to_employee": self.custodian
		}]

		asset_movement = frappe.get_doc({
			"doctype": "Asset Movement",
			"assets": assets,
			"purpose": "Receipt",
			"company": company,
			"transaction_date": transaction_date,
			"reference_doctype": reference_doctype,
			"reference_name": reference_docname
		}).insert()
		asset_movement.submit()

	def get_serial_no(self):
		if self.doctype == "Asset":
			return ""
		else:
			return self.serial_no

	def get_asset(self):
		if self.doctype == "Asset":
			return self.name
		else:
			return self.asset

	def get_asset_details(self):
		if self.doctype == "Asset":
			return self.asset_name, self.company
		else:
			return self.asset_values["asset_name"], self.asset_values["company"]

	def validate_make_gl_entry(self):
		purchase_document, asset_bought_with_invoice = self.get_purchase_document()

		if not purchase_document:
			return False

		cwip_enabled = is_cwip_accounting_enabled(self.asset_category)
		cwip_account = self.get_cwip_account(cwip_enabled=cwip_enabled)

		query = """SELECT name FROM `tabGL Entry` WHERE voucher_no = %s and account = %s"""

		if asset_bought_with_invoice:
			return self.has_expense_or_cwip_been_booked(query, purchase_document, cwip_account)
		else:
			return self.has_cwip_been_booked(query, purchase_document, cwip_account)

	def get_purchase_document(self):
		if self.doctype == "Asset":
			purchase_receipt, purchase_invoice = self.purchase_receipt, self.purchase_invoice
		else:
			purchase_receipt = self.asset_values["purchase_receipt"]
			purchase_invoice = self.asset_values["purchase_invoice"]

		asset_bought_with_invoice = self.was_asset_bought_with_invoice(purchase_invoice)
		purchase_document = purchase_invoice if asset_bought_with_invoice else purchase_receipt

		return purchase_document, asset_bought_with_invoice

	def was_asset_bought_with_invoice(self, purchase_invoice):
		return purchase_invoice and frappe.db.get_value(
			"Purchase Invoice", purchase_invoice, "update_stock"
		)

	def get_cwip_account(self, cwip_enabled=False):
		cwip_account = None

		try:
			if self.doctype == "Asset":
				asset, asset_category, company = self.name, self.asset_category, self.company
			else:
				asset = self.asset
				asset_category = self.asset_values["asset_category"]
				company = self.asset_values["company"]

			cwip_account = get_asset_account(
				"capital_work_in_progress_account", asset, asset_category, company
			)
		except Exception:
			# if no cwip account found in category or company and "cwip is enabled"
			# then raise else silently pass
			if cwip_enabled:
				raise

		return cwip_account

	def has_expense_or_cwip_been_booked(self, query, purchase_document, cwip_account):
		fixed_asset_account = self.get_fixed_asset_account()

		# with invoice purchase either expense or cwip has been booked
		expense_booked = frappe.db.sql(query, (purchase_document, fixed_asset_account), as_dict=1)
		if expense_booked:
			# if expense is already booked from invoice
			# then do not make gl entries regardless of cwip enabled/disabled
			return False

		cwip_booked = frappe.db.sql(query, (purchase_document, cwip_account), as_dict=1)
		if cwip_booked:
			# if cwip is booked from invoice then make gl entries regardless of cwip enabled/disabled
			return True

	def get_fixed_asset_account(self):
		if self.doctype == "Asset":
			asset, asset_category, company = self.name, self.asset_category, self.company
		else:
			asset = self.asset
			asset_category = self.asset_values["asset_category"]
			company = self.asset_values["company"]

		fixed_asset_account = get_asset_category_account(
			"fixed_asset_account", None, asset, None, asset_category, company
		)

		if not fixed_asset_account:
			frappe.throw(
				_("Set {0} in asset category {1} for company {2}").format(
					frappe.bold("Fixed Asset Account"),
					frappe.bold(self.asset_category),
					frappe.bold(self.company),
				),
				title=_("Account not Found"),
			)

		return fixed_asset_account

	def has_cwip_been_booked(self, query, purchase_document, cwip_account):
		# with receipt purchase either cwip has been booked or no entries have been made
		if not cwip_account:
			# if cwip account isn't available do not make gl entries
			return False

		cwip_booked = frappe.db.sql(query, (purchase_document, cwip_account), as_dict=1)
		# if cwip is not booked from receipt then do not make gl entries
		# if cwip is booked from receipt then make gl entries
		return cwip_booked

	def make_gl_entries(self):
		gl_entries = []

		purchase_document = self.get_purchase_document()
		fixed_asset_account, cwip_account = self.get_fixed_asset_account(), self.get_cwip_account()

		if self.doctype == "Asset":
			gross_purchase_amount = self.gross_purchase_amount
			cost_center = self.cost_center
		else:
			gross_purchase_amount = self.asset_values["gross_purchase_amount"]
			cost_center = self.asset_values["cost_center"]

		if (
			purchase_document and gross_purchase_amount and self.available_for_use_date <= getdate()
		):

			gl_entries.append(
				self.get_gl_dict(
					{
						"account": cwip_account,
						"against": fixed_asset_account,
						"remarks": self.get("remarks") or _("Accounting Entry for {0}").format(self.doctype),
						"posting_date": self.available_for_use_date,
						"credit": gross_purchase_amount,
						"credit_in_account_currency": gross_purchase_amount,
						"cost_center": cost_center,
					},
					item=self,
				)
			)

			gl_entries.append(
				self.get_gl_dict(
					{
						"account": fixed_asset_account,
						"against": cwip_account,
						"remarks": self.get("remarks") or _("Accounting Entry for {0}").format(self.doctype),
						"posting_date": self.available_for_use_date,
						"debit": gross_purchase_amount,
						"debit_in_account_currency": gross_purchase_amount,
						"cost_center": cost_center,
					},
					item=self,
				)
			)

		if gl_entries:
			make_gl_entries(gl_entries)
			self.db_set("booked_fixed_asset", 1)

	def set_status(self, status=None):
		if not status:
			status = self.get_status()
		self.db_set("status", status)

	def get_status(self):
		if self.docstatus == 0:
			status = "Draft"

		elif self.docstatus == 1:
			status = "Submitted"

			if self.journal_entry_for_scrap:
				status = "Scrapped"

			elif self.is_depreciable_asset() and self.is_not_serialized_asset() and self.get("finance_books"):
				idx = self.get_default_finance_book_idx() or 0
				gross_purchase_amount, _ = self.get_gross_purchase_amount_and_opening_accumulated_depreciation()

				asset_value = self.finance_books[idx].asset_value

				if flt(asset_value) <= self.salvage_value:
					status = "Fully Depreciated"
				elif flt(asset_value) < flt(gross_purchase_amount):
					status = "Partially Depreciated"

		elif self.docstatus == 2:
			status = "Cancelled"

		return status

	def get_default_finance_book_idx(self):
		_, company = self.get_asset_details()

		if not self.get("default_finance_book") and company:
			self.default_finance_book = get_default_finance_book(company)

		if self.get("default_finance_book"):
			for finance_book in self.get("finance_books"):
				if finance_book.finance_book == self.default_finance_book:
					return cint(finance_book.idx) - 1

	def validate_cancellation(self):
		if self.status in ("In Maintenance", "Out of Order"):
			frappe.throw(
				_(
					"There are active maintenance or repairs against the asset. \
					You must complete all of them before cancelling the asset."
				)
			)

		if self.status not in ("Submitted", "Partially Depreciated", "Fully Depreciated"):
			frappe.throw(_("{0} cannot be cancelled, as it is already {1}").format(self.name, self.status))

	def cancel_movement_entries(self):
		movement_entries = self.get_movement_entries()
		movement_drafts = []

		for movement in movement_entries:
			if movement.docstatus == 1:
				movement = frappe.get_doc("Asset Movement", movement.name)
				movement.cancel()
			else:
				movement_drafts.append(
					get_link_to_form("Asset Movement", movement.name)
				)

		if movement_drafts:
			frappe.msgprint(
				_("The following Asset Movements drafts are linked with {0}: {1}. \
				Kindly delete the Movements or remove the Asset from them to avoid raising errors.")
				.format(self.name, movement_drafts)
			)

	def get_movement_entries(self):
		filters = self.get_filters()

		movement_names = list(set(frappe.get_all(
			"Asset Movement Item",
			filters = filters,
			pluck = "parent"
		)))

		movement_entries = frappe.get_all(
			"Asset Movement",
			filters = {
				"name": ["in", movement_names]
			},
			fields = ["name", "docstatus"]
		)

		return movement_entries

	def get_filters(self):
		if self.doctype == "Asset":
			return {
				"asset": self.name,
				"serial_no": None,
				"docstatus": ["<", 2]
			}
		else:
			return {
				"asset": self.asset,
				"serial_no": self.serial_no,
				"docstatus": ["<", 2]
			}

	def delete_depreciation_schedules(self):
		filters = self.get_filters()

		linked_schedules = frappe.get_all(
			"Depreciation Schedule",
			filters = filters,
			fields = ["name", "docstatus"]
		)

		for schedule in linked_schedules:
			if schedule.docstatus == 1:
				schedule = frappe.get_doc("Depreciation Schedule", schedule.name)
				schedule.cancel()
			else:
				frappe.db.delete("Depreciation Schedule", schedule.name)

	def delete_depreciation_entries(self):
		filters = self.get_filters()

		linked_entries = frappe.get_all(
			"Depreciation Entry",
			filters = filters,
			pluck = "name"
		)

		for entry in linked_entries:
			entry = frappe.get_doc("Depreciation Entry", entry.name)
			entry.cancel()

def get_default_finance_book(company=None):
	from erpnext import get_default_company

	if not company:
		company = get_default_company()

	if not hasattr(frappe.local, "default_finance_book"):
		frappe.local.default_finance_book = {}

	if not company in frappe.local.default_finance_book:
		frappe.local.default_finance_book[company] = frappe.get_cached_value("Company",
			company,  "default_finance_book")

	return frappe.local.default_finance_book[company]

def is_cwip_accounting_enabled(asset_category):
	return cint(frappe.db.get_value("Asset Category", asset_category, "enable_cwip_accounting"))

def get_asset_account(account_name, asset=None, asset_category=None, company=None):
	account = None
	if asset:
		account = get_asset_category_account(account_name, asset=asset,
				asset_category = asset_category, company = company)

	if not asset and not account:
		account = get_asset_category_account(account_name, asset_category = asset_category, company = company)

	if not account:
		account = frappe.get_cached_value("Company",  company,  account_name)

	if not account:
		if not asset_category:
			frappe.throw(_("Set {0} in company {1}").format(account_name.replace("_", " ").title(), company))
		else:
			frappe.throw(_("Set {0} in asset category {1} or company {2}")
				.format(account_name.replace("_", " ").title(), asset_category, company))

	return account

@frappe.whitelist()
def get_finance_books(asset_category):
	asset_category_doc = frappe.get_doc("Asset Category", asset_category)
	books = []

	for d in asset_category_doc.finance_books:
		books.append({
			"finance_book": d.finance_book,
			"depreciation_template": d.depreciation_template
		})

	return books

@frappe.whitelist()
def make_asset_movement(assets, purpose=None):
	if isinstance(assets, str):
		assets = json.loads(assets)

	if len(assets) == 0:
		frappe.throw(_("Atleast one asset has to be selected."))

	asset_movement = frappe.new_doc("Asset Movement")
	asset_movement.quantity = len(assets)
	asset_movement.purpose = purpose

	for asset in assets:
		location, custodian, company, asset_name, serial_no  = fetch_asset_tracking_details(asset)

		asset_movement.company = company
		asset_movement.append("assets", {
			"asset": asset_name,
			"source_location": location,
			"from_employee": custodian,
			"serial_no": serial_no
		})

	if asset_movement.get("assets"):
		return asset_movement.as_dict()

def fetch_asset_tracking_details(asset):
	asset = frappe._dict(asset)

	if asset.get("serial_no"):
		location, custodian = frappe.get_value("Asset Serial No", asset.name, ["location", "custodian"])
		company = frappe.get_value("Asset", asset.asset, "company")
		asset_name = asset.asset
		serial_no = asset.name
	else:
		location, custodian, company = frappe.get_value("Asset", asset.name, ["location", "custodian", "company"])
		asset_name = asset.name
		serial_no = ""

	return location, custodian, company, asset_name, serial_no

@frappe.whitelist()
def get_purchase_details(asset):
	if isinstance(asset, str):
		asset = frappe._dict(json.loads(asset))

	if asset.doctype == "Asset":
		purchase_receipt, purchase_invoice = asset.purchase_receipt, asset.purchase_invoice
	else:
		purchase_receipt, purchase_invoice = frappe.db.get_value(
			"Asset",
			asset.asset,
			["purchase_receipt", "purchase_invoice"]
		)

	purchase_doctype = "Purchase Receipt" if purchase_receipt else "Purchase Invoice"
	purchase_docname = purchase_receipt or purchase_invoice

	return purchase_doctype, purchase_docname

@frappe.whitelist()
def get_num_of_items_in_purchase_doc(asset, purchase_doctype, purchase_docname):
	if isinstance(asset, str):
		asset = frappe._dict(json.loads(asset))

	items_doctype = purchase_doctype + " Item"
	item = get_item(asset)

	num_of_items_in_purchase_doc = frappe.db.get_value(
		items_doctype,
		{
			"parent": purchase_docname,
			"item_code": item
		},
		"qty"
	)
	return num_of_items_in_purchase_doc

def get_item(asset):
	if asset.doctype == "Asset":
		return asset.item_code
	else:
		return frappe.db.get_value("Asset", asset.asset, "item_code")

def validate_serial_no(doc):
	is_serialized_asset = frappe.db.get_value("Asset", doc.asset, "is_serialized_asset")

	if is_serialized_asset and not doc.serial_no:
		frappe.throw(_("Please enter Serial No as {0} is a Serialized Asset")
			.format(frappe.bold(doc.asset)), title=_("Missing Serial No"))

@frappe.whitelist()
def transfer_asset(asset, purpose, source_location, company):
	movement_entry = frappe.new_doc("Asset Movement")
	movement_entry.company = company
	movement_entry.purpose = purpose
	movement_entry.transaction_date = get_datetime()

	movement_entry.append("assets", {
		"asset": asset,
		"source_location": source_location
	})

	return movement_entry

@frappe.whitelist()
def make_sales_invoice(asset, item_code, company):
	si = frappe.new_doc("Sales Invoice")
	si.company = company
	si.currency = frappe.get_cached_value("Company",  company,  "default_currency")
	disposal_account, depreciation_cost_center = get_disposal_account_and_cost_center(company)

	si.append("items", {
		"item_code": item_code,
		"is_fixed_asset": 1,
		"asset": asset,
		"income_account": disposal_account,
		"cost_center": depreciation_cost_center,
		"qty": 1
	})
	si.set_missing_values()

	return si

def get_disposal_account_and_cost_center(company):
	disposal_account, depreciation_cost_center = frappe.get_cached_value("Company",  company,
		["disposal_account", "depreciation_cost_center"])

	if not disposal_account:
		frappe.throw(_("Please set 'Gain/Loss Account on Asset Disposal' in Company {0}").format(company))
	if not depreciation_cost_center:
		frappe.throw(_("Please set 'Asset Depreciation Cost Center' in Company {0}").format(company))

	return disposal_account, depreciation_cost_center

@frappe.whitelist()
def create_asset_maintenance(asset, item_code, item_name, asset_category, company):
	asset_maintenance = frappe.new_doc("Asset Maintenance")
	asset_maintenance.update({
		"asset_name": asset,
		"company": company,
		"item_code": item_code,
		"item_name": item_name,
		"asset_category": asset_category
	})

	return asset_maintenance

@frappe.whitelist()
def create_asset_repair(asset, asset_name):
	asset_repair = frappe.new_doc("Asset Repair")
	asset_repair.update({
		"asset": asset,
		"asset_name": asset_name
	})

	return asset_repair

@frappe.whitelist()
def create_asset_revaluation(asset, asset_category, company):
	asset_revaluation = frappe.new_doc("Asset Revaluation")
	asset_revaluation.update({
		"asset": asset,
		"company": company,
		"asset_category": asset_category,
		"date": getdate()
	})

	return asset_revaluation

@frappe.whitelist()
def create_depreciation_entry(asset_name, serial_no=None):
	from assets.asset.doctype.depreciation_schedule_.depreciation_posting import get_depreciation_accounts, get_depreciation_details

	asset_category, company, cost_center, is_depreciable_asset = frappe.get_value(
		"Asset", asset_name, ["asset_category", "company", "cost_center", "calculate_depreciation"]
	)

	credit_account, debit_account = get_depreciation_accounts(asset_category, company)
	depreciation_cost_center, depreciation_series = get_depreciation_details(company)
	depreciation_cost_center = cost_center or depreciation_cost_center

	depr_entry = frappe.new_doc("Depreciation Entry")
	depr_entry.update({
		"company": company,
		"posting_date": getdate(),
		"asset": asset_name,
		"serial_no": serial_no,
		"cost_center": cost_center,
		"credit_account": credit_account,
		"debit_account": debit_account,
		"reference_doctype": "Asset Serial No" if serial_no else "Asset",
		"reference_docname": serial_no if serial_no else asset_name
	})

	if depreciation_series:
		depr_entry.naming_series = depreciation_series

	if is_depreciable_asset:
		update_finance_book(depr_entry)

	return depr_entry

def update_finance_book(depr_entry):
	asset_or_serial_no = depr_entry.get_asset_or_serial_no()
	finance_books = depr_entry.get_finance_books_linked_with_asset(asset_or_serial_no)

	if len(finance_books) == 1:
		depr_entry.finance_book = finance_books[0]

def update_maintenance_status():
	assets_that_require_maintenance = get_assets_that_require_maintenance()

	for asset in assets_that_require_maintenance:
		if not asset.is_serialized_asset:
			update_status_for_asset(asset)
		else:
			serial_nos = get_linked_serial_nos(asset)

			for serial_no in serial_nos:
				update_status_for_asset(asset, serial_no)

def update_status_for_asset(asset, serial_no=None):
	if not has_pending_repairs(asset, serial_no):
		asset_or_serial_no = serial_no if serial_no else asset

		if has_maintenance_task_due_today(asset_or_serial_no):
			if asset_or_serial_no.status != "In Maintenance":
				asset_or_serial_no = frappe.get_doc(asset_or_serial_no.doctype, asset_or_serial_no.name)
				asset_or_serial_no.set_status("In Maintenance")
		else:
			if asset_or_serial_no.status == "In Maintenance":
				asset_or_serial_no = frappe.get_doc(asset_or_serial_no.doctype, asset_or_serial_no.name)
				asset_or_serial_no.set_status()

def get_assets_that_require_maintenance():
	assets = frappe.get_all(
		"Asset",
		filters = {
			"docstatus": 1,
			"maintenance_required": 1
		},
		fields = ["doctype", "name", "status", "is_serialized_asset"]
	)

	return assets

def has_pending_repairs(asset, serial_no=None):
	return frappe.db.exists(
		"Asset Repair",
		{
			"asset_name": asset.name,
			"serial_no": serial_no.name,
			"repair_status": "Pending"
		}
	)

def has_maintenance_task_due_today(parent):
	return frappe.db.exists(
		"Asset Maintenance Task",
		{
			"parent": parent.name,
			"next_due_date": getdate()
		}
	)

def get_linked_serial_nos(asset):
	return frappe.get_all(
		"Asset Serial No",
		filters = {
			"asset": asset.name,
			"docstatus": 1,
		},
		fields = ["doctype", "name", "status"]
	)

# cwip entries need to be posted on an asset's available-for-use date
# not on its date of submission
def post_cwip_entries():
	asset_categories= get_asset_categories_with_cwip_accounting_enabled()

	assets = get_assets_that_need_to_be_booked_today(asset_categories)
	serial_nos = get_serial_nos_that_need_to_be_booked_today(asset_categories)

	for asset in (assets + serial_nos):
		doc = frappe.get_doc(asset.doctype, asset.name)
		doc.make_gl_entries()

def get_asset_categories_with_cwip_accounting_enabled():
	return frappe.db.get_all(
		"Asset Category",
		filters = {
			"enable_cwip_accounting": 1
		},
		pluck = "name"
	)

def get_assets_that_need_to_be_booked_today(asset_categories):
	return frappe.get_all(
		"Asset",
		filters = {
			"asset_category": ["in", asset_categories],
			"available_for_use_date": nowdate(),
			"booked_fixed_asset": 0
		},
		fields = ["doctype", "name"]
	)

def get_serial_nos_that_need_to_be_booked_today(asset_categories):
	assets = get_serialized_assets_that_need_cwip_booking(asset_categories)

	return frappe.get_all(
		"Asset Serial No",
		filters = {
			"asset": ["in", assets],
			"available_for_use_date": nowdate(),
			"booked_fixed_asset": 0
		},
		fields = ["doctype", "name"]
	)

def get_serialized_assets_that_need_cwip_booking(asset_categories):
	return frappe.get_all(
		"Asset",
		filters = {
			"asset_category": ["in", asset_categories],
			"is_serialized_asset": 1
		},
		pluck = "name"
	)
