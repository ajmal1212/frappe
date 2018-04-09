# -*- coding: utf-8 -*-
# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
from frappe import _
from frappe.model.document import Document
from erpnext_shopify.exceptions import ShopifySetupError
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from six.moves.urllib.parse import urlparse
from frappe.utils import get_request_session
import json

class ShopifySettings(Document):
	def validate(self):
		self.setup_custom_fields()

		if self.enable_shopify == 1:
			self.validate_access_credentials()
		
		self.setup_webhooks()

	def setup_custom_fields(self):
		dict(fieldname='shipping_bill_date', label='Shipping Bill Date',
			fieldtype='Date', insert_after='shipping_bill_number', print_hide=1,
			depends_on="eval:doc.invoice_type=='Export' ")

		custom_fields = {
			"Customer": [dict(fieldname='shopify_customer_id', label='Shopify Customer Id',
				fieldtype='Data', insert_after='series', read_only=1, print_hide=1)],
			"Address": [dict(fieldname='shopify_address_id', label='Shopify Address Id',
				fieldtype='Data', insert_after='fax', read_only=1, print_hide=1)],
			"Item": [
				dict(fieldname='shopify_variant_id', label='Shopify Variant Id',
					fieldtype='Data', insert_after='item_code', read_only=1, print_hide=1),
				dict(fieldname='shopify_product_id', label='Shopify Product Id',
					fieldtype='Data', insert_after='item_code', read_only=1, print_hide=1),
				dict(fieldname='shopify_description', label='Shopify Description',
					fieldtype='Text Editor', insert_after='description', read_only=1, print_hide=1)
			],
			"Sales Order": [dict(fieldname='shopify_order_id', label='Shopify Order Id',
				fieldtype='Data', insert_after='title', read_only=1, print_hide=1)],
			"Delivery Note":[
				dict(fieldname='shopify_order_id', label='Shopify Order Id',
					fieldtype='Data', insert_after='title', read_only=1, print_hide=1),
				dict(fieldname='shopify_fulfillment_id', label='Shopify Fulfillment Id',
					fieldtype='Data', insert_after='title', read_only=1, print_hide=1)
			],
			"Sales Invoice": [dict(fieldname='shopify_order_id', label='Shopify Order Id',
				fieldtype='Data', insert_after='title', read_only=1, print_hide=1)]
		}

		create_custom_fields(custom_fields)

	def validate_access_credentials(self):
		if self.app_type == "Private":
			if not (self.get_password(raise_exception=False) and self.api_key and self.shopify_url):
				frappe.msgprint(_("Missing value for Password, API Key or Shopify URL"), raise_exception=ShopifySetupError)

		else:
			if not (self.access_token and self.shopify_url):
				frappe.msgprint(_("Access token or Shopify URL missing"), raise_exception=ShopifySetupError)
	
	def setup_webhooks(self):
		webhooks = ["orders/create", "orders/paid", "orders/fulfilled"]
		url = get_shopify_url('admin/webhooks.json', self)

		for webhook in webhooks:
			session = get_request_session()
			try:
				d = session.post(url, data=json.dumps({
					"webhook": {
						"topic": webhook,
						"address": get_webhook_address(),
						"format": "json"
						}
					}), headers=get_header(self))
				d.raise_for_status()
			except Exception:
				pass

def get_shopify_url(path, settings):
	if settings.app_type == "Private":
		return 'https://{}:{}@{}/{}'.format(settings.api_key, settings.get_password('password'), settings.shopify_url, path)
	else:
		return 'https://{}/{}'.format(settings.shopify_url, path)

def get_header(settings):
	header = {'Content-Type': 'application/json'}

	if settings.app_type == "Private":
		return header
	else:
		header["X-Shopify-Access-Token"] = settings.access_token
		return header

def get_webhook_address():
	endpoint = "/api/method/erpnext.erpnext_integrations.connectors.shopify_connection.sync_order"

	# try:
# 		url = frappe.request.url
# 	except RuntimeError:
		# for CI Test to work
	url = "https://testshop.localtunnel.me"

	server_url = '{uri.scheme}://{uri.netloc}'.format(
		uri=urlparse(url)
	)

	delivery_url = server_url + endpoint

	return delivery_url

@frappe.whitelist()
def get_series():
	return {
		"sales_order_series" : frappe.get_meta("Sales Order").get_options("naming_series") or "SO-Shopify-",
		"sales_invoice_series" : frappe.get_meta("Sales Invoice").get_options("naming_series")  or "SI-Shopify-",
		"delivery_note_series" : frappe.get_meta("Delivery Note").get_options("naming_series")  or "DN-Shopify-"
	}