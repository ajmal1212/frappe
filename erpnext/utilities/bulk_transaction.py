import json
from datetime import date, datetime

import frappe


@frappe.whitelist()
def transaction_processing(data, from_doctype, to_doctype):
	deserialized_data = json.loads(data)
	length_of_data = len(deserialized_data)

	if length_of_data > 10:
		frappe.msgprint("Started a background job to create {1} {0}".format(to_doctype,length_of_data))
		frappe.enqueue(job, deserialized_data=deserialized_data, from_doctype=from_doctype, to_doctype=to_doctype)
	else:
		job(deserialized_data, from_doctype, to_doctype)

def job(deserialized_data, from_doctype, to_doctype):
	failed_history = []
	i = 0
	for d in deserialized_data:
		failed = []

		try:
			i+=1
			doc_name = d.get('name')
			task(doc_name, from_doctype, to_doctype)

		except Exception as e:
			failed_history.append(e)
			failed.append(e)
			update_logger(doc_name, e, from_doctype, to_doctype, status="Failed")

		if not failed:
			update_logger(doc_name, None, from_doctype, to_doctype, status="Success")

	show_job_status(failed_history, deserialized_data, to_doctype)

def task(doc_name, from_doctype, to_doctype):
	from erpnext.accounts.doctype.payment_entry import payment_entry
	from erpnext.accounts.doctype.purchase_invoice import purchase_invoice
	from erpnext.accounts.doctype.sales_invoice import sales_invoice
	from erpnext.buying.doctype.purchase_order import purchase_order
	from erpnext.buying.doctype.supplier_quotation import supplier_quotation
	from erpnext.selling.doctype.quotation import quotation
	from erpnext.selling.doctype.sales_order import sales_order
	from erpnext.stock.doctype.delivery_note import delivery_note
	from erpnext.stock.doctype.purchase_receipt import purchase_receipt

	# From Sales Order
	if from_doctype == "Sales Order" and to_doctype == "Sales Invoice":
		obj = sales_order.make_sales_invoice(doc_name)
	if from_doctype == "Sales Order" and to_doctype == "Delivery Note":
		obj = sales_order.make_delivery_note(doc_name)
	if from_doctype == "Sales Order" and to_doctype == "Advance Payment":
		obj = payment_entry.get_payment_entry(from_doctype, doc_name)
	# From Sales Invoice
	if from_doctype == "Sales Invoice" and to_doctype == "Delivery Note":
		obj = sales_invoice.make_delivery_note(doc_name)
	if from_doctype == "Sales Invoice" and to_doctype == "Payment":
		obj = payment_entry.get_payment_entry(from_doctype, doc_name)
	# From Delivery Note
	if from_doctype == "Delivery Note" and to_doctype == "Sales Invoice":
		obj = delivery_note.make_sales_invoice(doc_name)
	if from_doctype == "Delivery Note" and to_doctype == "Packing Slip":
		obj = delivery_note.make_packing_slip(doc_name)
	# From Quotation
	if from_doctype == "Quotation" and to_doctype == "Sales Order":
		obj = quotation.make_sales_order(doc_name)
	if from_doctype == "Quotation" and to_doctype == "Sales Invoice":
		obj = quotation.make_sales_invoice(doc_name)
	# From Supplier Quotation
	if from_doctype == "Supplier Quotation" and to_doctype == "Purchase Order":
		obj = supplier_quotation.make_purchase_order(doc_name)
	if from_doctype == "Supplier Quotation" and to_doctype == "Purchase Invoice":
		obj = supplier_quotation.make_purchase_invoice(doc_name)
	# From Purchase Order
	if from_doctype == "Purchase Order" and to_doctype == "Purchase Invoice":
		obj = purchase_order.make_purchase_invoice(doc_name)
	if from_doctype == "Purchase Order" and to_doctype == "Purchase Receipt":
		obj = purchase_order.make_purchase_receipt(doc_name)
	if from_doctype == "Purchase Order" and to_doctype == "Advance Payment":
		obj = payment_entry.get_payment_entry(from_doctype, doc_name)
	# From Purchase Invoice
	if from_doctype == "Purchase Invoice" and to_doctype == "Purchase Receipt":
		obj = purchase_invoice.make_purchase_receipt(doc_name)
	if from_doctype == "Purchase Invoice" and to_doctype == "Payment":
		obj = payment_entry.get_payment_entry(from_doctype, doc_name)
	# From Purchase Receipt
	if from_doctype == "Purchase Receipt" and to_doctype == "Purchase Invoice":
		obj = purchase_receipt.make_purchase_invoice(doc_name)

	obj.flags.ignore_validate = True
	obj.insert(ignore_mandatory=True)

def check_logger_doc_exists():
	return frappe.db.exists("Bulk Transaction Logger", str(date.today()))

def get_logger_doc():
	return frappe.get_doc("Bulk Transaction Logger", str(date.today()))

def create_logger_doc():
	log_doc = frappe.new_doc("Bulk Transaction Logger")
	log_doc.set_new_name(set_name= str(date.today()))
	log_doc.log_date = date.today()

	return log_doc

def append_data_to_logger(log_doc, doc_name, error, from_doctype, to_doctype, status, restarted):
	row = log_doc.append("logger_data", {})
	row.transaction_name = doc_name
	row.date = date.today()
	now = datetime.now()
	row.time = now.strftime("%H:%M:%S")
	row.transaction_status = status
	row.error_description = str(error)
	row.from_doctype = from_doctype
	row.to_doctype = to_doctype
	row.retried = restarted

def update_logger(doc_name, e, from_doctype, to_doctype, status, restarted=0):
	if not check_logger_doc_exists():
		log_doc = create_logger_doc()
		append_data_to_logger(log_doc, doc_name, e, from_doctype, to_doctype, status, restarted)
		log_doc.insert()
	else:
		log_doc = get_logger_doc()
		if record_exists(log_doc, doc_name, status):
			append_data_to_logger(log_doc, doc_name, e, from_doctype, to_doctype, status, restarted)
			log_doc.save()

def show_job_status(failed_history, deserialized_data, to_doctype):
	if not failed_history:
		frappe.msgprint("Creation of {0} Successfull".format(to_doctype)
		,title="Successfull", indicator="green")

	if len(failed_history) != 0 and len(failed_history) < len(deserialized_data):
		frappe.msgprint("""Creation of {0} partially Successfull.
		Check <b><a href="/app/bulk-transaction-logger">Bulk Transaction Logger</a></b>""".format(to_doctype)
		,title="Partially Successfull", indicator="orange")

	if len(failed_history) == len(deserialized_data):
		frappe.msgprint("""Creation of {0} Failed.
		Check <b><a href="/app/bulk-transaction-logger">Bulk Transaction Logger</a></b>""".format(to_doctype)
		,title="Failed", indicator="red")

def record_exists(log_doc, doc_name, status):
	record = 0
	for d in log_doc.get("logger_data"):
		if d.transaction_name == doc_name and d.transaction_status == "Failed":
			d.retried = 1
			record = record + 1

	log_doc.save()

	if record and status == "Failed":
		return False
	elif record and status == "Success":
		return True
	else:
		return True
