QUnit.module('Sales Order');

QUnit.only("test sales order", function(assert) {
	assert.expect(4);
	let done = assert.async();
	frappe.run_serially([
		() => frappe.tests.setup_doctype('Customer'),
		() => frappe.tests.setup_doctype('Item'),
		() => frappe.tests.setup_doctype('Sales Taxes and Charges Template'),
		() => frappe.tests.setup_doctype('Terms and Conditions'),
		() => {
			return frappe.tests.make('Sales Order', [
				{customer: 'Test Customer 1'},
				{delivery_date: frappe.datetime.add_days(frappe.defaults.get_default("year_end_date"), 1)},
				{items: [
					[
						{'item_code': 'Test Product 1'},
						{'qty': 5}
					]
				]},
				{taxes_and_charges: 'TEST In State GST'},
				{tc_name: 'TEST Delivery Terms for Sales Order'}
			]);
		},
		() => cur_frm.set_value('taxes_and_charges','TEST In State GST'),
		() => cur_frm.set_value('apply_discount_on','Grand Total'),
		() => cur_frm.set_value('additional_discount_percentage',10),
		() => cur_frm.set_value('terms','<pre style="line-height: 1.42857; background-color: rgb(255, 255, 255);">Delivery Terms for Order number {{ name }}</pre>\
<pre style="line-height: 1.42857; background-color: rgb(255, 255, 255);"><br></pre>\
<pre style="line-height: 1.42857; background-color: rgb(255, 255, 255);">-Order Date : {{ transaction_date }} </pre>\
<pre style="line-height: 1.42857; background-color: rgb(255, 255, 255);">-Expected Delivery Date : {{ delivery_date }}</pre>\
<br><br>'),
		() => frappe.timeout(1),
		() => {
			// get_item_details
			assert.ok(cur_frm.doc.items[0].item_name=='Test Product 1');
			//get tax details
			assert.ok(cur_frm.doc.taxes_and_charges=='TEST In State GST');

			//get tax details
			assert.ok(cur_frm.doc.taxes[0].account_head=='CGST - '+frappe.get_abbr(frappe.defaults.get_default('Company')));
			// calculate_taxes_and_totals
			assert.ok(cur_frm.doc.grand_total==531);
		},
		/*() => cur_frm.savesubmit(),*/
		() => done()
	]);
});

/*
QUnit.only("test taxes", function(assert) {
	assert.expect(2);
	let done = assert.async();
	frappe.run_serially([
		() => {
			return frappe.tests.make('', [
				{customer: 'Test Customer 1'},
				{delivery_date: frappe.datetime.add_days(frappe.defaults.get_default("year_end_date"), 1)},
				{items: [
					[
						{'item_code': 'Test Product 1'},
						{'qty': 5}
					]
				]}
			]);
		},
		() => {
			// get_item_details
			assert.ok(cur_frm.doc.items[0].item_name=='Test Product 1');

			// calculate_taxes_and_totals
			assert.ok(cur_frm.doc.grand_total==500);
		},
		() => done()
	]);
});

*/