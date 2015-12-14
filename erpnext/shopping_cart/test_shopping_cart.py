# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from __future__ import unicode_literals
import unittest
import frappe
from erpnext.shopping_cart.cart import _get_cart_quotation, update_cart, get_customer

class TestShoppingCart(unittest.TestCase):
	"""
		Note:
		Shopping Cart == Quotation
	"""
	def setUp(self):
		frappe.set_user("Administrator")
		self.enable_shopping_cart()

	def tearDown(self):
		frappe.set_user("Administrator")
		self.disable_shopping_cart()

	def test_get_cart_new_user(self):
		self.login_as_new_user()

		# test if lead is created and quotation with new lead is fetched
		quotation = _get_cart_quotation()
		self.assertEquals(quotation.quotation_to, "Customer")
		self.assertEquals(frappe.db.get_value("Contact", {"customer": quotation.customer}, "email_id"),
			"test_cart_user@example.com")
		self.assertEquals(quotation.lead, None)
		self.assertEquals(quotation.contact_email, frappe.session.user)

		return quotation

	def test_get_cart_customer(self):
		self.login_as_customer()

		# test if quotation with customer is fetched
		quotation = _get_cart_quotation()
		self.assertEquals(quotation.quotation_to, "Customer")
		self.assertEquals(quotation.customer, "_Test Customer")
		self.assertEquals(quotation.lead, None)
		self.assertEquals(quotation.contact_email, frappe.session.user)

		return quotation

	def test_add_to_cart(self):
		self.login_as_customer()

		# remove from cart
		self.remove_all_items_from_cart()

		# add first item
		update_cart("_Test Item", 1)

		quotation = self.test_get_cart_customer()

		self.assertEquals(quotation.get("items")[0].item_code, "_Test Item")
		self.assertEquals(quotation.get("items")[0].qty, 1)
		self.assertEquals(quotation.get("items")[0].amount, 10)


		# add second item
		update_cart("_Test Item 2", 1)
		quotation = self.test_get_cart_customer()
		self.assertEquals(quotation.get("items")[1].item_code, "_Test Item 2")
		self.assertEquals(quotation.get("items")[1].qty, 1)
		self.assertEquals(quotation.get("items")[1].amount, 20)

		self.assertEquals(len(quotation.get("items")), 2)

	def test_update_cart(self):
		# first, add to cart
		self.test_add_to_cart()

		# update first item
		update_cart("_Test Item", 5)
		quotation = self.test_get_cart_customer()
		self.assertEquals(quotation.get("items")[0].item_code, "_Test Item")
		self.assertEquals(quotation.get("items")[0].qty, 5)
		self.assertEquals(quotation.get("items")[0].amount, 50)
		self.assertEquals(quotation.net_total, 70)
		self.assertEquals(len(quotation.get("items")), 2)

	def test_remove_from_cart(self):
		# first, add to cart
		self.test_add_to_cart()

		# remove first item
		update_cart("_Test Item", 0)
		quotation = self.test_get_cart_customer()

		self.assertEquals(quotation.get("items")[0].item_code, "_Test Item 2")
		self.assertEquals(quotation.get("items")[0].qty, 1)
		self.assertEquals(quotation.get("items")[0].amount, 20)
		self.assertEquals(quotation.net_total, 20)
		self.assertEquals(len(quotation.get("items")), 1)

		# remove second item
		update_cart("_Test Item 2", 0)
		quotation = self.test_get_cart_customer()

		self.assertEquals(len(quotation.get("items")), 0)
		self.assertEquals(quotation.net_total, 0)

	def test_tax_rule(self):
		self.login_as_customer()
		quotation = self.create_quotation()

		from erpnext.accounts.party import set_taxes

		tax_rule_master = set_taxes(quotation.customer, "Customer", \
			quotation.transaction_date, quotation.organization, None, None, \
			quotation.customer_address, quotation.shipping_address_name, 1)

		self.assertEquals(quotation.taxes_and_charges, tax_rule_master)
		self.assertEquals(quotation.total_taxes_and_charges, 1000.0)

		self.remove_test_quotation(quotation)

	def create_quotation(self):
		quotation = frappe.new_doc("Quotation")

		values = {
			"doctype": "Quotation",
			"quotation_to": "Customer",
			"order_type": "Shopping Cart",
			"customer": get_customer(frappe.session.user).name,
			"docstatus": 0,
			"contact_email": frappe.session.user,
			"selling_price_list": "_Test Price List Rest of the World",
			"currency": "USD",
			"taxes_and_charges" : "_Test Tax 1",
			"items": [{
				"item_code": "_Test Item",
				"qty": 1
			}],
			"taxes": frappe.get_doc("Sales Taxes and Charges Template", "_Test Tax 1").taxes,
			"organization": "_Test organization"
		}

		quotation.update(values)

		quotation.insert(ignore_permissions=True)

		return quotation

	def remove_test_quotation(self, quotation):
		frappe.set_user("Administrator")
		quotation.delete()

	# helper functions
	def enable_shopping_cart(self):
		settings = frappe.get_doc("Shopping Cart Settings", "Shopping Cart Settings")

		settings.update({
			"enabled": 1,
			"organization": "_Test organization",
			"default_customer_group": "_Test Customer Group",
			"quotation_series": "_T-Quotation-",
			"price_list": "_Test Price List India"
		})

		# insert item price
		if not frappe.db.get_value("Item Price", {"price_list":  "_Test Price List India",
			"item_code": "_Test Item"}):
			frappe.get_doc({
				"doctype": "Item Price",
				"price_list": "_Test Price List India",
				"item_code": "_Test Item",
				"price_list_rate": 10
			}).insert()
			frappe.get_doc({
				"doctype": "Item Price",
				"price_list": "_Test Price List India",
				"item_code": "_Test Item 2",
				"price_list_rate": 20
			}).insert()

		settings.save()
		frappe.local.shopping_cart_settings = None

	def disable_shopping_cart(self):
		settings = frappe.get_doc("Shopping Cart Settings", "Shopping Cart Settings")
		settings.enabled = 0
		settings.save()
		frappe.local.shopping_cart_settings = None

	def login_as_new_user(self):
		frappe.set_user("test_cart_user@example.com")

	def login_as_customer(self):
		frappe.set_user("test_contact_customer@example.com")

	def remove_all_items_from_cart(self):
		quotation = _get_cart_quotation()
		quotation.set("items", [])
		quotation.save(ignore_permissions=True)

test_dependencies = ["Sales Taxes and Charges Template", "Price List", "Item Price", "Shipping Rule", "Currency Exchange",
	"Customer Group", "Lead", "Customer", "Contact", "Address", "Item", "Tax Rule"]
