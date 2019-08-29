# -*- coding: utf-8 -*-
# Copyright (c) 2019, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from __future__ import unicode_literals
import frappe
import json
from six import iteritems
from frappe.model.document import Document
from frappe import _
from frappe.utils import floor, flt, today, cint
from frappe.model.mapper import get_mapped_doc, map_child_doc
from erpnext.stock.get_item_details import get_conversion_factor
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note as create_delivery_note_from_sales_order

# TODO: Prioritize SO or WO group warehouse

class PickList(Document):
	def before_submit(self):
		for item in self.locations:
			if not frappe.get_cached_value('Item', item.item_code, 'has_serial_no'):
				continue
			if len(item.serial_no.split('\n')) == item.picked_qty:
				continue
			frappe.throw(_('For item {0} at row {1}, count of serial numbers does not match with the picked quantity')
				.format(frappe.bold(item.item_code), frappe.bold(item.idx)))

	def set_item_locations(self):
		items = self.aggregate_item_qty()
		self.item_location_map = frappe._dict()

		from_warehouses = None
		if self.parent_warehouse:
			from_warehouses = frappe.db.get_descendants('Warehouse', self.parent_warehouse)

		# reset
		self.delete_key('locations')
		for item_doc in items:
			item_code = item_doc.item_code

			self.item_location_map.setdefault(item_code,
				get_available_item_locations(item_code, from_warehouses, self.item_count_map.get(item_code)))

			locations = get_items_with_location_and_quantity(item_doc, self.item_location_map)

			item_doc.idx = None
			item_doc.name = None

			for row in locations:
				row.update({
					'picked_qty': row.stock_qty
				})

				location = item_doc.as_dict()
				location.update(row)
				self.append('locations', location)

	def aggregate_item_qty(self):
		locations = self.locations
		self.item_count_map = {}
		# aggregate qty for same item
		item_map = frappe._dict()
		for item in locations:
			item_code = item.item_code
			reference = item.sales_order_item or item.material_request_item
			key = (item_code, item.uom, reference)

			item.idx = None
			item.name = None

			if item_map.get(key):
				item_map[key].qty += item.qty
				item_map[key].stock_qty += item.stock_qty
			else:
				item_map[key] = item

			# maintain count of each item (useful to limit get query)
			self.item_count_map.setdefault(item_code, 0)
			self.item_count_map[item_code] += item.stock_qty

		return item_map.values()


def get_items_with_location_and_quantity(item_doc, item_location_map):
	available_locations = item_location_map.get(item_doc.item_code)
	locations = []

	remaining_stock_qty = item_doc.stock_qty
	while remaining_stock_qty > 0 and available_locations:
		item_location = available_locations.pop(0)
		item_location = frappe._dict(item_location)

		stock_qty = remaining_stock_qty if item_location.qty >= remaining_stock_qty else item_location.qty
		qty = stock_qty / (item_doc.conversion_factor or 1)

		uom_must_be_whole_number = frappe.db.get_value('UOM', item_doc.uom, 'must_be_whole_number')
		if uom_must_be_whole_number:
			qty = floor(qty)
			stock_qty = qty * item_doc.conversion_factor
			if not stock_qty: break

		serial_nos = None
		if item_location.serial_no:
			serial_nos = '\n'.join(item_location.serial_no[0: cint(stock_qty)])

		locations.append(frappe._dict({
			'qty': qty,
			'stock_qty': stock_qty,
			'warehouse': item_location.warehouse,
			'serial_no': serial_nos,
			'batch_no': item_location.batch_no
		}))

		remaining_stock_qty -= stock_qty

		qty_diff = item_location.qty - stock_qty
		# if extra quantity is available push current warehouse to available locations
		if qty_diff > 0:
			item_location.qty = qty_diff
			if item_location.serial_no:
				# set remaining serial numbers
				item_location.serial_no = item_location.serial_no[-qty_diff:]
			available_locations = [item_location] + available_locations

	# update available locations for the item
	item_location_map[item_doc.item_code] = available_locations
	return locations

def get_available_item_locations(item_code, from_warehouses, required_qty):
	if frappe.get_cached_value('Item', item_code, 'has_serial_no'):
		return get_available_item_locations_for_serialized_item(item_code, from_warehouses, required_qty)
	elif frappe.get_cached_value('Item', item_code, 'has_batch_no'):
		return get_available_item_locations_for_batched_item(item_code, from_warehouses, required_qty)
	else:
		return get_available_item_locations_for_other_item(item_code, from_warehouses, required_qty)

def get_available_item_locations_for_serialized_item(item_code, from_warehouses, required_qty):
	filters = frappe._dict({
		'item_code': item_code,
		'warehouse': ['!=', '']
	})

	if from_warehouses:
		filters.warehouse = ['in', from_warehouses]

	serial_nos = frappe.get_all('Serial No',
		fields=['name', 'warehouse'],
		filters=filters,
		limit=required_qty,
		order_by='purchase_date',
		as_list=1)

	remaining_stock_qty = required_qty - len(serial_nos)
	if remaining_stock_qty:
		frappe.msgprint('{0} qty of {1} is not available.'
			.format(remaining_stock_qty, item_code))

	warehouse_serial_nos_map = frappe._dict()
	for serial_no, warehouse in serial_nos:
		warehouse_serial_nos_map.setdefault(warehouse, []).append(serial_no)

	locations = []
	for warehouse, serial_nos in iteritems(warehouse_serial_nos_map):
		locations.append({
			'qty': len(serial_nos),
			'warehouse': warehouse,
			'serial_no': serial_nos
		})

	return locations

def get_available_item_locations_for_batched_item(item_code, from_warehouses, required_qty):
	batch_locations = frappe.db.sql("""
		SELECT
			sle.`warehouse`,
			sle.`batch_no`,
			SUM(sle.`actual_qty`) AS `qty`
		FROM
			`tabStock Ledger Entry` sle, `tabBatch` batch
		WHERE
			sle.batch_no = batch.name
			and sle.`item_code`=%(item_code)s
			and IFNULL(batch.`expiry_date`, '2200-01-01') > %(today)s
		GROUP BY
			`warehouse`,
			`batch_no`,
			`item_code`
		HAVING `qty` > 0
		ORDER BY IFNULL(batch.`expiry_date`, '2200-01-01'), batch.`creation`
	""", {
		'item_code': item_code,
		'today': today()
	}, as_dict=1)

	total_qty_available = sum(location.get('qty') for location in batch_locations)

	remaining_qty = required_qty - total_qty_available

	if remaining_qty > 0:
		frappe.msgprint('No batches found for {} qty of {}.'.format(remaining_qty, item_code))

	return batch_locations

def get_available_item_locations_for_other_item(item_code, from_warehouses, required_qty):
	# gets all items available in different warehouses
	filters = frappe._dict({
		'item_code': item_code,
		'actual_qty': ['>', 0]
	})

	if from_warehouses:
		filters.warehouse = ['in', from_warehouses]

	item_locations = frappe.get_all('Bin',
		fields=['warehouse', 'actual_qty as qty'],
		filters=filters,
		limit=required_qty,
		order_by='creation')

	return item_locations

@frappe.whitelist()
def create_delivery_note(source_name, target_doc=None):
	pick_list = frappe.get_doc('Pick List', source_name)
	sales_orders = [d.sales_order for d in pick_list.locations]
	sales_orders = set(sales_orders)

	delivery_note = None
	for sales_order in sales_orders:
		delivery_note = create_delivery_note_from_sales_order(sales_order,
			delivery_note, skip_item_mapping=True)

	item_table_mapper = {
		'doctype': 'Delivery Note Item',
		'field_map': {
			'rate': 'rate',
			'name': 'so_detail',
			'parent': 'against_sales_order',
		},
		'condition': lambda doc: abs(doc.delivered_qty) < abs(doc.qty) and doc.delivered_by_supplier!=1
	}

	for location in pick_list.locations:
		sales_order_item = frappe.get_cached_doc('Sales Order Item', location.sales_order_item)
		dn_item = map_child_doc(sales_order_item, delivery_note, item_table_mapper)

		if dn_item:
			dn_item.warehouse = location.warehouse
			dn_item.qty = location.picked_qty
			dn_item.batch_no = location.batch_no
			dn_item.serial_no = location.serial_no

			update_delivery_note_item(sales_order_item, dn_item, delivery_note)

	set_delivery_note_missing_values(delivery_note)

	delivery_note.pick_list = pick_list.name

	return delivery_note

@frappe.whitelist()
def create_stock_entry(pick_list):
	pick_list = frappe.get_doc(json.loads(pick_list))

	if stock_entry_exists(pick_list.get('name')):
		return frappe.msgprint(_('Stock Entry has been already created against this Pick List'))

	stock_entry = frappe.new_doc('Stock Entry')
	stock_entry.pick_list = pick_list.get('name')
	stock_entry.purpose = pick_list.get('purpose')
	stock_entry.set_stock_entry_type()

	if pick_list.get('work_order'):
		stock_entry = update_stock_entry_based_on_work_order(pick_list, stock_entry)
	elif pick_list.get('material_request'):
		stock_entry = update_stock_entry_based_on_material_request(pick_list, stock_entry)
	else:
		stock_entry = update_stock_entry_items_with_no_reference(pick_list, stock_entry)

	stock_entry.set_incoming_rate()
	stock_entry.set_actual_qty()
	stock_entry.calculate_rate_and_amount(update_finished_item_rate=False)

	return stock_entry.as_dict()

@frappe.whitelist()
def get_pending_work_orders(doctype, txt, searchfield, start, page_length, filters, as_dict):
	return frappe.db.sql("""
		SELECT
			`name`, `company`, `planned_start_date`
		FROM
			`tabWork Order`
		WHERE
			`status` not in ('Completed', 'Stopped')
			AND `qty` > `material_transferred_for_manufacturing`
			AND `docstatus` = 1
			AND `company` = %(company)s
			AND `name` like %(txt)s
		ORDER BY
			if(locate(%(_txt)s, name), locate(%(_txt)s, name), 99999), name
		LIMIT
			%(start)s, %(page_length)s""",
		{
			'txt': "%%%s%%" % txt,
			'_txt': txt.replace('%', ''),
			'start': start,
			'page_length': frappe.utils.cint(page_length),
			'company': filters.get('company')
		}, as_dict=as_dict)

@frappe.whitelist()
def target_document_exists(pick_list_name, purpose):
	if purpose == 'Delivery against Sales Order':
		return frappe.db.exists('Delivery Note', {
			'pick_list': pick_list_name
		})

	return stock_entry_exists(pick_list_name)


def update_delivery_note_item(source, target, delivery_note):
	cost_center = frappe.db.get_value('Project', delivery_note.project, 'cost_center')
	if not cost_center:
		cost_center = get_cost_center(source.item_code, 'Item', delivery_note.company)

	if not cost_center:
		cost_center = get_cost_center(source.item_group, 'Item Group', delivery_note.company)

	target.cost_center = cost_center

def get_cost_center(for_item, from_doctype, company):
	'''Returns Cost Center for Item or Item Group'''
	return frappe.db.get_value('Item Default',
		fieldname=['buying_cost_center'],
		filters={
			'parent': for_item,
			'parenttype': from_doctype,
			'company': company
		})

def set_delivery_note_missing_values(target):
	target.run_method('set_missing_values')
	target.run_method('set_po_nos')
	target.run_method('calculate_taxes_and_totals')

def stock_entry_exists(pick_list_name):
	return frappe.db.exists('Stock Entry', {
		'pick_list': pick_list_name
	})

@frappe.whitelist()
def get_item_details(item_code, uom=None):
	details = frappe.db.get_value('Item', item_code, ['stock_uom', 'name'], as_dict=1)
	details.uom = uom or details.stock_uom
	if uom:
		details.update(get_conversion_factor(item_code, uom))

	return details


def update_stock_entry_based_on_work_order(pick_list, stock_entry):
	work_order = frappe.get_doc("Work Order", pick_list.get('work_order'))

	stock_entry.work_order = work_order.name
	stock_entry.company = work_order.company
	stock_entry.from_bom = 1
	stock_entry.bom_no = work_order.bom_no
	stock_entry.use_multi_level_bom = work_order.use_multi_level_bom
	stock_entry.fg_completed_qty = pick_list.for_qty
	if work_order.bom_no:
		stock_entry.inspection_required = frappe.db.get_value('BOM',
			work_order.bom_no, 'inspection_required')

	is_wip_warehouse_group = frappe.db.get_value('Warehouse', work_order.wip_warehouse, 'is_group')
	if not (is_wip_warehouse_group and work_order.skip_transfer):
		wip_warehouse = work_order.wip_warehouse
	else:
		wip_warehouse = None
	stock_entry.to_warehouse = wip_warehouse

	stock_entry.project = work_order.project

	for location in pick_list.locations:
		item = frappe._dict()
		update_common_item_properties(item, location)
		item.t_warehouse = wip_warehouse

		stock_entry.append('items', item)

	return stock_entry

def update_stock_entry_based_on_material_request(pick_list, stock_entry):
	for location in pick_list.locations:
		target_warehouse = None
		if location.material_request_item:
			target_warehouse = frappe.get_value('Material Request Item',
				location.material_request_item, 'warehouse')
		item = frappe._dict()
		update_common_item_properties(item, location)
		item.t_warehouse = target_warehouse
		stock_entry.append('items', item)

	return stock_entry

def update_stock_entry_items_with_no_reference(pick_list, stock_entry):
	for location in pick_list.locations:
		item = frappe._dict()
		update_common_item_properties(item, location)

		stock_entry.append('items', item)

	return stock_entry

def update_common_item_properties(item, location):
	item.item_code = location.item_code
	item.s_warehouse = location.warehouse
	item.qty = location.picked_qty * location.conversion_factor
	item.transfer_qty = location.picked_qty
	item.uom = location.uom
	item.conversion_factor = location.conversion_factor
	item.stock_uom = location.stock_uom
	item.material_request = location.material_request
	item.serial_no = location.serial_no
	item.batch_no = location.batch_no
	item.material_request_item = location.material_request_item