# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.model.meta import get_field_precision
from frappe.utils import cstr, flt
from frappe.utils.xlsxutils import handle_html

from erpnext.accounts.report.sales_register.sales_register import get_mode_of_payments
from erpnext.accounts.report.utils import get_query_columns, get_values_for_columns
from erpnext.selling.report.item_wise_sales_history.item_wise_sales_history import (
	get_customer_details,
)


def execute(filters=None):
	return _execute(filters)


def _execute(filters=None, additional_table_columns=None, additional_conditions=None):
	if not filters:
		filters = {}
	columns = get_columns(additional_table_columns, filters)

	company_currency = frappe.get_cached_value("Company", filters.get("company"), "default_currency")

	item_list = get_items(filters, get_query_columns(additional_table_columns), additional_conditions)
	if item_list:
		itemised_tax, tax_columns = get_tax_accounts(item_list, columns, company_currency)

		scrubbed_tax_fields = {}

		for tax in tax_columns:
			scrubbed_tax_fields.update(
				{
					tax + " Rate": frappe.scrub(tax + " Rate"),
					tax + " Amount": frappe.scrub(tax + " Amount"),
				}
			)

	mode_of_payments = get_mode_of_payments(set(d.parent for d in item_list))
	so_dn_map = get_delivery_notes_against_sales_order(item_list)

	data = []
	total_row_map = {}
	skip_total_row = 0
	prev_group_by_value = ""

	if filters.get("group_by"):
		grand_total = get_grand_total(filters, "Sales Invoice")

	customer_details = get_customer_details()

	for d in item_list:
		customer_record = customer_details.get(d.customer)

		delivery_note = None
		if d.delivery_note:
			delivery_note = d.delivery_note
		elif d.so_detail:
			delivery_note = ", ".join(so_dn_map.get(d.so_detail, []))

		if not delivery_note and d.update_stock:
			delivery_note = d.parent

		row = {
			"item_code": d.item_code,
			"item_name": d.si_item_name if d.si_item_name else d.i_item_name,
			"item_group": d.si_item_group if d.si_item_group else d.i_item_group,
			"description": d.description,
			"invoice": d.parent,
			"posting_date": d.posting_date,
			"customer": d.customer,
			"customer_name": customer_record.customer_name,
			"customer_group": customer_record.customer_group,
			**get_values_for_columns(additional_table_columns, d),
			"debit_to": d.debit_to,
			"mode_of_payment": ", ".join(mode_of_payments.get(d.parent, [])),
			"territory": d.territory,
			"project": d.project,
			"company": d.company,
			"sales_order": d.sales_order,
			"delivery_note": d.delivery_note,
			"income_account": get_income_account(d),
			"cost_center": d.cost_center,
			"stock_qty": d.stock_qty,
			"stock_uom": d.stock_uom,
		}

		if d.stock_uom != d.uom and d.stock_qty:
			row.update({"rate": (d.base_net_rate * d.qty) / d.stock_qty, "amount": d.base_net_amount})
		else:
			row.update({"rate": d.base_net_rate, "amount": d.base_net_amount})

		total_tax = 0
		total_other_charges = 0
		for tax in tax_columns:
			item_tax = itemised_tax.get(d.name, {}).get(tax, {})
			row.update(
				{
					scrubbed_tax_fields[tax + " Rate"]: item_tax.get("tax_rate", 0),
					scrubbed_tax_fields[tax + " Amount"]: item_tax.get("tax_amount", 0),
				}
			)
			if item_tax.get("is_other_charges"):
				total_other_charges += flt(item_tax.get("tax_amount"))
			else:
				total_tax += flt(item_tax.get("tax_amount"))

		row.update(
			{
				"total_tax": total_tax,
				"total_other_charges": total_other_charges,
				"total": d.base_net_amount + total_tax,
				"currency": company_currency,
			}
		)

		if filters.get("group_by"):
			row.update({"percent_gt": flt(row["total"] / grand_total) * 100})
			group_by_field, subtotal_display_field = get_group_by_and_display_fields(filters)
			data, prev_group_by_value = add_total_row(
				data,
				filters,
				prev_group_by_value,
				d,
				total_row_map,
				group_by_field,
				subtotal_display_field,
				grand_total,
				tax_columns,
			)
			add_sub_total_row(row, total_row_map, d.get(group_by_field, ""), tax_columns)

		data.append(row)

	if filters.get("group_by") and item_list:
		total_row = total_row_map.get(prev_group_by_value or d.get("item_name"))
		total_row["percent_gt"] = flt(total_row["total"] / grand_total * 100)
		data.append(total_row)
		data.append({})
		add_sub_total_row(total_row, total_row_map, "total_row", tax_columns)
		data.append(total_row_map.get("total_row"))
		skip_total_row = 1

	return columns, data, None, None, None, skip_total_row


def get_income_account(row):
	if row.enable_deferred_revenue:
		return row.deferred_revenue_account
	elif row.is_internal_customer == 1:
		return row.unrealized_profit_loss_account
	else:
		return row.income_account


def get_columns(additional_table_columns, filters):
	columns = []

	if filters.get("group_by") != ("Item"):
		columns.extend(
			[
				{
					"label": _("Item Code"),
					"fieldname": "item_code",
					"fieldtype": "Link",
					"options": "Item",
					"width": 120,
				},
				{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 120},
			]
		)

	if filters.get("group_by") not in ("Item", "Item Group"):
		columns.extend(
			[
				{
					"label": _("Item Group"),
					"fieldname": "item_group",
					"fieldtype": "Link",
					"options": "Item Group",
					"width": 120,
				}
			]
		)

	columns.extend(
		[
			{"label": _("Description"), "fieldname": "description", "fieldtype": "Data", "width": 150},
			{
				"label": _("Invoice"),
				"fieldname": "invoice",
				"fieldtype": "Link",
				"options": "Sales Invoice",
				"width": 120,
			},
			{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 120},
		]
	)

	if filters.get("group_by") != "Customer":
		columns.extend(
			[
				{
					"label": _("Customer Group"),
					"fieldname": "customer_group",
					"fieldtype": "Link",
					"options": "Customer Group",
					"width": 120,
				}
			]
		)

	if filters.get("group_by") not in ("Customer", "Customer Group"):
		columns.extend(
			[
				{
					"label": _("Customer"),
					"fieldname": "customer",
					"fieldtype": "Link",
					"options": "Customer",
					"width": 120,
				},
				{
					"label": _("Customer Name"),
					"fieldname": "customer_name",
					"fieldtype": "Data",
					"width": 120,
				},
			]
		)

	if additional_table_columns:
		columns += additional_table_columns

	columns += [
		{
			"label": _("Receivable Account"),
			"fieldname": "debit_to",
			"fieldtype": "Link",
			"options": "Account",
			"width": 80,
		},
		{
			"label": _("Mode Of Payment"),
			"fieldname": "mode_of_payment",
			"fieldtype": "Data",
			"width": 120,
		},
	]

	if filters.get("group_by") != "Territory":
		columns.extend(
			[
				{
					"label": _("Territory"),
					"fieldname": "territory",
					"fieldtype": "Link",
					"options": "Territory",
					"width": 80,
				}
			]
		)

	columns += [
		{
			"label": _("Project"),
			"fieldname": "project",
			"fieldtype": "Link",
			"options": "Project",
			"width": 80,
		},
		{
			"label": _("Company"),
			"fieldname": "company",
			"fieldtype": "Link",
			"options": "Company",
			"width": 80,
		},
		{
			"label": _("Sales Order"),
			"fieldname": "sales_order",
			"fieldtype": "Link",
			"options": "Sales Order",
			"width": 100,
		},
		{
			"label": _("Delivery Note"),
			"fieldname": "delivery_note",
			"fieldtype": "Link",
			"options": "Delivery Note",
			"width": 100,
		},
		{
			"label": _("Income Account"),
			"fieldname": "income_account",
			"fieldtype": "Link",
			"options": "Account",
			"width": 100,
		},
		{
			"label": _("Cost Center"),
			"fieldname": "cost_center",
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 100,
		},
		{"label": _("Stock Qty"), "fieldname": "stock_qty", "fieldtype": "Float", "width": 100},
		{
			"label": _("Stock UOM"),
			"fieldname": "stock_uom",
			"fieldtype": "Link",
			"options": "UOM",
			"width": 100,
		},
		{
			"label": _("Rate"),
			"fieldname": "rate",
			"fieldtype": "Float",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Amount"),
			"fieldname": "amount",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
	]

	if filters.get("group_by"):
		columns.append(
			{"label": _("% Of Grand Total"), "fieldname": "percent_gt", "fieldtype": "Float", "width": 80}
		)

	return columns


def get_conditions(filters, additional_conditions=None):
	conditions = ""

	for opts in (
		("company", " and `tabSales Invoice`.company=%(company)s"),
		("customer", " and `tabSales Invoice`.customer = %(customer)s"),
		("item_code", " and `tabSales Invoice Item`.item_code = %(item_code)s"),
		("from_date", " and `tabSales Invoice`.posting_date>=%(from_date)s"),
		("to_date", " and `tabSales Invoice`.posting_date<=%(to_date)s"),
	):
		if filters.get(opts[0]):
			conditions += opts[1]

	if additional_conditions:
		conditions += additional_conditions

	if filters.get("mode_of_payment"):
		conditions += """ and exists(select name from `tabSales Invoice Payment`
			where parent=`tabSales Invoice`.name
				and ifnull(`tabSales Invoice Payment`.mode_of_payment, '') = %(mode_of_payment)s)"""

	if filters.get("warehouse"):
		if frappe.db.get_value("Warehouse", filters.get("warehouse"), "is_group"):
			lft, rgt = frappe.db.get_all(
				"Warehouse", filters={"name": filters.get("warehouse")}, fields=["lft", "rgt"], as_list=True
			)[0]
			conditions += f"and ifnull(`tabSales Invoice Item`.warehouse, '') in (select name from `tabWarehouse` where lft > {lft} and rgt < {rgt}) "
		else:
			conditions += """and ifnull(`tabSales Invoice Item`.warehouse, '') = %(warehouse)s"""

	if filters.get("brand"):
		conditions += """and ifnull(`tabSales Invoice Item`.brand, '') = %(brand)s"""

	if filters.get("item_group"):
		conditions += """and ifnull(`tabSales Invoice Item`.item_group, '') = %(item_group)s"""

	if filters.get("income_account"):
		conditions += """
			and (ifnull(`tabSales Invoice Item`.income_account, '') = %(income_account)s
			or ifnull(`tabSales Invoice Item`.deferred_revenue_account, '') = %(income_account)s
			or ifnull(`tabSales Invoice`.unrealized_profit_loss_account, '') = %(income_account)s)
		"""

	if not filters.get("group_by"):
		conditions += "ORDER BY `tabSales Invoice`.posting_date desc, `tabSales Invoice Item`.item_group desc"
	else:
		conditions += get_group_by_conditions(filters, "Sales Invoice")

	return conditions


def get_group_by_conditions(filters, doctype):
	if filters.get("group_by") == "Invoice":
		return f"ORDER BY `tab{doctype} Item`.parent desc"
	elif filters.get("group_by") == "Item":
		return f"ORDER BY `tab{doctype} Item`.`item_code`"
	elif filters.get("group_by") == "Item Group":
		return "ORDER BY `tab{} Item`.{}".format(doctype, frappe.scrub(filters.get("group_by")))
	elif filters.get("group_by") in ("Customer", "Customer Group", "Territory", "Supplier"):
		return "ORDER BY `tab{}`.{}".format(doctype, frappe.scrub(filters.get("group_by")))


def get_items(filters, additional_query_columns, additional_conditions=None):
	si = frappe.qb.DocType("Sales Invoice")
	sii = frappe.qb.DocType("Sales Invoice Item")
	Item = frappe.qb.DocType("Item")

	query = (
		frappe.qb.from_(si)
		.join(sii)
		.on(si.name == sii.parent)
		# added left join
		.left_join(Item)
		.on(sii.item_code == Item.name)
		.select(
			sii.name,
			sii.parent,
			si.posting_date,
			si.debit_to,
			si.unrealized_profit_loss_account,
			si.is_internal_customer,
			si.customer,
			si.remarks,
			si.territory,
			si.company,
			si.base_net_total,
			sii.project,
			sii.item_code,
			sii.description,
			sii.item_name,
			sii.item_group,
			sii.item_name.as_("si_item_name"),
			sii.item_group.as_("si_item_group"),
			Item.item_name.as_("i_item_name"),
			Item.item_group.as_("i_item_group"),
			sii.sales_order,
			sii.delivery_note,
			sii.income_account,
			sii.cost_center,
			sii.enable_deferred_revenue,
			sii.deferred_revenue_account,
			sii.stock_qty,
			sii.stock_uom,
			sii.base_net_rate,
			sii.base_net_amount,
			si.customer_name,
			si.customer_group,
			sii.so_detail,
			si.update_stock,
			sii.uom,
			sii.qty,
		)
		.where(si.docstatus == 1)
	)
	if filters.get("customer"):
		query = query.where(si.customer == filters["customer"])

	if filters.get("customer_group"):
		query = query.where(si.customer_group == filters["customer_group"])

	return query.run(as_dict=True)


def get_delivery_notes_against_sales_order(item_list):
	so_dn_map = frappe._dict()
	so_item_rows = list(set([d.so_detail for d in item_list]))

	if so_item_rows:
		delivery_notes = frappe.db.sql(
			"""
			select parent, so_detail
			from `tabDelivery Note Item`
			where docstatus=1 and so_detail in (%s)
			group by so_detail, parent
		"""
			% (", ".join(["%s"] * len(so_item_rows))),
			tuple(so_item_rows),
			as_dict=1,
		)

		for dn in delivery_notes:
			so_dn_map.setdefault(dn.so_detail, []).append(dn.parent)

	return so_dn_map


def get_grand_total(filters, doctype):
	return frappe.db.sql(
		f""" SELECT
		SUM(`tab{doctype}`.base_grand_total)
		FROM `tab{doctype}`
		WHERE `tab{doctype}`.docstatus = 1
		and posting_date between %s and %s
	""",
		(filters.get("from_date"), filters.get("to_date")),
	)[0][0]  # nosec


def get_tax_accounts(
	item_list,
	columns,
	company_currency,
	doctype="Sales Invoice",
	tax_doctype="Sales Taxes and Charges",
):
	import json

	item_row_map = {}
	tax_columns = []
	invoice_item_row = {}
	itemised_tax = {}
	add_deduct_tax = "charge_type"

	tax_amount_precision = (
		get_field_precision(frappe.get_meta(tax_doctype).get_field("tax_amount"), currency=company_currency)
		or 2
	)

	for d in item_list:
		invoice_item_row.setdefault(d.parent, []).append(d)
		item_row_map.setdefault(d.parent, {}).setdefault(d.item_code or d.item_name, []).append(d)

	conditions = ""
	if doctype == "Purchase Invoice":
		conditions = (
			" and category in ('Total', 'Valuation and Total') and base_tax_amount_after_discount_amount != 0"
		)
		add_deduct_tax = "add_deduct_tax"

	tax_details = frappe.db.sql(
		f"""
		select
			name, parent, description, item_wise_tax_detail, account_head,
			charge_type, {add_deduct_tax}, base_tax_amount_after_discount_amount
		from `tab%s`
		where
			parenttype = %s and docstatus = 1
			and (description is not null and description != '')
			and parent in (%s)
			%s
		order by description
	"""
		% (tax_doctype, "%s", ", ".join(["%s"] * len(invoice_item_row)), conditions),
		tuple([doctype, *list(invoice_item_row)]),
	)

	account_doctype = frappe.qb.DocType("Account")

	query = (
		frappe.qb.from_(account_doctype)
		.select(account_doctype.name)
		.where(account_doctype.account_type == "Tax")
	)

	tax_accounts = query.run()

	for (
		_name,
		parent,
		description,
		item_wise_tax_detail,
		account_head,
		charge_type,
		add_deduct_tax,
		tax_amount,
	) in tax_details:
		description = handle_html(description)
		if description not in tax_columns and tax_amount:
			# as description is text editor earlier and markup can break the column convention in reports
			tax_columns.append(description)

		if item_wise_tax_detail:
			try:
				item_wise_tax_detail = json.loads(item_wise_tax_detail)

				for item_code, tax_data in item_wise_tax_detail.items():
					itemised_tax.setdefault(item_code, frappe._dict())

					if isinstance(tax_data, list):
						tax_rate, tax_amount = tax_data
					else:
						tax_rate = tax_data
						tax_amount = 0

					if charge_type == "Actual" and not tax_rate:
						tax_rate = "NA"

					item_net_amount = sum(
						[flt(d.base_net_amount) for d in item_row_map.get(parent, {}).get(item_code, [])]
					)

					for d in item_row_map.get(parent, {}).get(item_code, []):
						item_tax_amount = (
							flt((tax_amount * d.base_net_amount) / item_net_amount) if item_net_amount else 0
						)
						if item_tax_amount:
							tax_value = flt(item_tax_amount, tax_amount_precision)
							tax_value = (
								tax_value * -1
								if (doctype == "Purchase Invoice" and add_deduct_tax == "Deduct")
								else tax_value
							)

							itemised_tax.setdefault(d.name, {})[description] = frappe._dict(
								{
									"tax_rate": tax_rate,
									"tax_amount": tax_value,
									"is_other_charges": 0 if tuple([account_head]) in tax_accounts else 1,
								}
							)

			except ValueError:
				continue
		elif charge_type == "Actual" and tax_amount:
			for d in invoice_item_row.get(parent, []):
				itemised_tax.setdefault(d.name, {})[description] = frappe._dict(
					{
						"tax_rate": "NA",
						"tax_amount": flt(
							(tax_amount * d.base_net_amount) / d.base_net_total, tax_amount_precision
						),
					}
				)

	tax_columns.sort()
	for desc in tax_columns:
		columns.append(
			{
				"label": _(desc + " Rate"),
				"fieldname": frappe.scrub(desc + " Rate"),
				"fieldtype": "Float",
				"width": 100,
			}
		)

		columns.append(
			{
				"label": _(desc + " Amount"),
				"fieldname": frappe.scrub(desc + " Amount"),
				"fieldtype": "Currency",
				"options": "currency",
				"width": 100,
			}
		)

	columns += [
		{
			"label": _("Total Tax"),
			"fieldname": "total_tax",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Total Other Charges"),
			"fieldname": "total_other_charges",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"label": _("Total"),
			"fieldname": "total",
			"fieldtype": "Currency",
			"options": "currency",
			"width": 100,
		},
		{
			"fieldname": "currency",
			"label": _("Currency"),
			"fieldtype": "Currency",
			"width": 80,
			"hidden": 1,
		},
	]

	return itemised_tax, tax_columns


def add_total_row(
	data,
	filters,
	prev_group_by_value,
	item,
	total_row_map,
	group_by_field,
	subtotal_display_field,
	grand_total,
	tax_columns,
):
	if prev_group_by_value != item.get(group_by_field, ""):
		if prev_group_by_value:
			total_row = total_row_map.get(prev_group_by_value)
			data.append(total_row)
			data.append({})
			add_sub_total_row(total_row, total_row_map, "total_row", tax_columns)

		prev_group_by_value = item.get(group_by_field, "")

		total_row_map.setdefault(
			item.get(group_by_field, ""),
			{
				subtotal_display_field: get_display_value(filters, group_by_field, item),
				"stock_qty": 0.0,
				"amount": 0.0,
				"bold": 1,
				"total_tax": 0.0,
				"total": 0.0,
				"percent_gt": 0.0,
			},
		)

		total_row_map.setdefault(
			"total_row",
			{
				subtotal_display_field: "Total",
				"stock_qty": 0.0,
				"amount": 0.0,
				"bold": 1,
				"total_tax": 0.0,
				"total": 0.0,
				"percent_gt": 0.0,
			},
		)

	return data, prev_group_by_value


def get_display_value(filters, group_by_field, item):
	if filters.get("group_by") == "Item":
		if item.get("item_code") != item.get("item_name"):
			value = (
				cstr(item.get("item_code"))
				+ "<br><br>"
				+ "<span style='font-weight: normal'>"
				+ cstr(item.get("item_name"))
				+ "</span>"
			)
		else:
			value = item.get("item_code", "")
	elif filters.get("group_by") in ("Customer", "Supplier"):
		party = frappe.scrub(filters.get("group_by"))
		if item.get(party) != item.get(party + "_name"):
			value = (
				item.get(party)
				+ "<br><br>"
				+ "<span style='font-weight: normal'>"
				+ item.get(party + "_name")
				+ "</span>"
			)
		else:
			value = item.get(party)
	else:
		value = item.get(group_by_field)

	return value


def get_group_by_and_display_fields(filters):
	if filters.get("group_by") == "Item":
		group_by_field = "item_code"
		subtotal_display_field = "invoice"
	elif filters.get("group_by") == "Invoice":
		group_by_field = "parent"
		subtotal_display_field = "item_code"
	else:
		group_by_field = frappe.scrub(filters.get("group_by"))
		subtotal_display_field = "item_code"

	return group_by_field, subtotal_display_field


def add_sub_total_row(item, total_row_map, group_by_value, tax_columns):
	total_row = total_row_map.get(group_by_value)
	total_row["stock_qty"] += item["stock_qty"]
	total_row["amount"] += item["amount"]
	total_row["total_tax"] += item["total_tax"]
	total_row["total"] += item["total"]
	total_row["percent_gt"] += item["percent_gt"]

	for tax in tax_columns:
		total_row.setdefault(frappe.scrub(tax + " Amount"), 0.0)
		total_row[frappe.scrub(tax + " Amount")] += flt(item[frappe.scrub(tax + " Amount")])
