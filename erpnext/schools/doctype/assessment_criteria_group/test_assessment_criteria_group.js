QUnit.module('schools');

QUnit.test('test assessment criteria group', function(assert){
	assert.expect(0);
	let done = assert.async();
	let fee_structure_code;
	frappe.run_serially([
		() => {
			return frappe.tests.make('Assessment Criteria Group', [
				{assessment_criteria_group: 'Scholarship'}
			]);
		},
		() => done()
	]);
});