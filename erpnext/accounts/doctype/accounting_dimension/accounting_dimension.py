# -*- coding: utf-8 -*-
# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
from frappe.model.document import Document
from frappe.custom.doctype.custom_field.custom_field import create_custom_field
from frappe import scrub
from frappe.utils import cstr
from frappe.utils.background_jobs import enqueue

class AccountingDimension(Document):
	def on_update(self):
		frappe.enqueue(disable_dimension, doc=self)

	def before_insert(self):
		self.set_fieldname_and_label()
		frappe.enqueue(make_dimension_in_accounting_doctypes, doc=self)

	def on_trash(self):
		frappe.enqueue(delete_accounting_dimension, doc=self)

	def set_fieldname_and_label(self):
		if not self.label:
			self.label = cstr(self.document_type)

		if not self.fieldname:
			self.fieldname = scrub(self.label)

def make_dimension_in_accounting_doctypes(doc):
	doclist = get_doclist()

	if doc.is_mandatory:
		df.update({
			"reqd": 1
		})

	for doctype in doclist:

		df = {
			"fieldname": doc.fieldname,
			"label": doc.label,
			"fieldtype": "Link",
			"options": doc.document_type,
			"insert_after": "cost_center"
		}

		if doctype == "Budget":
			df.update({
				"depends_on": "eval:doc.budget_against == '{0}'".format(doc.document_type)
			})

			create_custom_field(doctype, df)

			property_setter = frappe.db.exists("Property Setter", "Budget-budget_against-options")

			if property_setter:
				property_setter_doc = frappe.get_doc("Property Setter", "Budget-budget_against-options")
				property_setter_doc.doc_type = 'Budget'
				property_setter_doc.doctype_or_field = "DocField"
				property_setter_doc.fiel_dname = "budget_against"
				property_setter_doc.property = "options"
				property_setter_doc.property_type = "Text"
				property_setter_doc.value = property_setter_doc.value + "\n" + doc.document_type
				property_setter_doc.save()

				frappe.clear_cache(doctype='Budget')
			else:
				frappe.get_doc({
					"doctype": "Property Setter",
					"doctype_or_field": "DocField",
					"doc_type": "Budget",
					"field_name": "budget_against",
					"property": "options",
					"property_type": "Text",
					"value": "\nCost Center\nProject\n" + doc.document_type
				}).insert(ignore_permissions=True)
			frappe.clear_cache(doctype=doctype)
		else:
			create_custom_field(doctype, df)
			frappe.clear_cache(doctype=doctype)

def delete_accounting_dimension(doc):
	doclist = get_doclist()

	frappe.db.sql("""
		DELETE FROM `tabCustom Field`
		WHERE  fieldname = %s
		AND dt IN (%s)""" %
		('%s', ', '.join(['%s']* len(doclist))), tuple([doc.fieldname] + doclist))

	frappe.db.sql("""
		DELETE FROM `tabProperty Setter`
		WHERE  field_name = %s
		AND doc_type IN (%s)""" %
		('%s', ', '.join(['%s']* len(doclist))), tuple([doc.fieldname] + doclist))

	budget_against_property = frappe.get_doc("Property Setter", "Budget-budget_against-options")
	value_list = budget_against_property.value.split('\n')[3:]
	value_list.remove(doc.document_type)

	budget_against_property.value = "\nCost Center\nProject\n" + "\n".join(value_list)
	budget_against_property.save()

	for doctype in doclist:
		frappe.clear_cache(doctype=doctype)

def disable_dimension(doc):
	if doc.disable:
		df = {"read_only": 1}
	else:
		df = {"read_only": 0}

	doclist = get_doclist()

	for doctype in doclist:
		field = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": doc.fieldname})
		if field:
			custom_field = frappe.get_doc("Custom Field", field)
			custom_field.update(df)
			custom_field.save()

		frappe.clear_cache(doctype=doctype)

def get_doclist():
	doclist = ["GL Entry", "Sales Invoice", "Purchase Invoice", "Payment Entry", "Asset",
		"Expense Claim", "Stock Entry", "Budget", "Payroll Entry", "Delivery Note", "Sales Invoice Item", "Purchase Invoice Item",
		"Purchase Order Item", "Journal Entry Account", "Material Request Item", "Delivery Note Item", "Purchase Receipt Item",
		"Stock Entry Detail", "Payment Entry Deduction"]

	return doclist


def get_accounting_dimensions():
	accounting_dimensions = frappe.get_all("Accounting Dimension", fields=["fieldname"])

	return [d.fieldname for d in accounting_dimensions]
