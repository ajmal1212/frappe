# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt
import frappe

from frappe.utils import (getdate, validate_email_address, today, 
                          add_years, cstr, comma_sep, comma_and)
from frappe.model.naming import set_name_by_naming_series
from frappe import throw, _, scrub
from frappe.permissions import add_user_permission, remove_user_permission, \
	set_user_permission_if_allowed, has_permission, get_doc_permissions
from erpnext.utilities.transaction_base import delete_events
from frappe.utils.nestedset import NestedSet

class EmployeeUserDisabledError(frappe.ValidationError): pass
class EmployeeLeftValidationError(frappe.ValidationError): pass

class Employee(NestedSet):
	nsm_parent_field = 'reports_to'

	def autoname(self):
		naming_method = frappe.db.get_value("HR Settings", None, "emp_created_by")
		if not naming_method:
			throw(_("Please setup Employee Naming System in Human Resource > HR Settings"))
		else:
			if naming_method == 'Naming Series':
				set_name_by_naming_series(self)
			elif naming_method == 'Employee Number':
				self.name = self.employee_number
			elif naming_method == 'Full Name':
				self.set_employee_name()
				self.name = self.employee_name

		self.employee = self.name

	def validate(self):
		from erpnext.controllers.status_updater import validate_status
		validate_status(self.status, ["Active", "Temporary Leave", "Left"])

		self.employee = self.name
		self.set_employee_name()
		self.validate_date()
		self.validate_email()
		self.validate_status()
		self.validate_reports_to()
		self.validate_preferred_email()
		if self.job_applicant:
			self.validate_onboarding_process()

		if self.user_id:
			self.validate_user_details()
		else:
			existing_user_id = frappe.db.get_value("Employee", self.name, "user_id")
			if existing_user_id:
				remove_user_permission(
					"Employee", self.name, existing_user_id)

	def after_rename(self, old, new, merge):
		self.db_set("employee", new)

	def set_employee_name(self):
		self.employee_name = ' '.join(filter(lambda x: x, [self.first_name, self.middle_name, self.last_name]))

	def validate_user_details(self):
		data = frappe.db.get_value('User',
			self.user_id, ['enabled', 'user_image'], as_dict=1)
		if data.get("user_image") and self.image == '':
			self.image = data.get("user_image")
		self.validate_for_enabled_user_id(data.get("enabled", 0))
		self.validate_duplicate_user_id()

	def update_nsm_model(self):
		frappe.utils.nestedset.update_nsm(self)

	def on_update(self):
		self.update_nsm_model()
		if self.user_id:
			self.update_user()
			self.update_user_permissions()
		self.reset_employee_emails_cache()
		self.update_approver_role()

	def update_user_permissions(self):
		if not self.create_user_permission: return
		if not has_permission('User Permission', ptype='write', raise_exception=False): return

		employee_user_permission_exists = frappe.db.exists('User Permission', {
			'allow': 'Employee',
			'for_value': self.name,
			'user': self.user_id
		})

		if employee_user_permission_exists: return

		employee_user_permission_exists = frappe.db.exists('User Permission', {
			'allow': 'Employee',
			'for_value': self.name,
			'user': self.user_id
		})

		if employee_user_permission_exists: return

		add_user_permission("Employee", self.name, self.user_id)
		set_user_permission_if_allowed("Company", self.company, self.user_id)

	def update_user(self):
		# add employee role if missing
		user = frappe.get_doc("User", self.user_id)
		user.flags.ignore_permissions = True

		if "Employee" not in user.get("roles"):
			user.append_roles("Employee")

		# copy details like Fullname, DOB and Image to User
		if self.employee_name and not (user.first_name and user.last_name):
			employee_name = self.employee_name.split(" ")
			if len(employee_name) >= 3:
				user.last_name = " ".join(employee_name[2:])
				user.middle_name = employee_name[1]
			elif len(employee_name) == 2:
				user.last_name = employee_name[1]

			user.first_name = employee_name[0]

		if self.date_of_birth:
			user.birth_date = self.date_of_birth

		if self.gender:
			user.gender = self.gender

		if self.image:
			if not user.user_image:
				user.user_image = self.image
				try:
					frappe.get_doc({
						"doctype": "File",
						"file_url": self.image,
						"attached_to_doctype": "User",
						"attached_to_name": self.user_id
					}).insert()
				except frappe.DuplicateEntryError:
					# already exists
					pass

		user.save()

	def update_approver_role(self):
		if self.leave_approver:
			user = frappe.get_doc("User", self.leave_approver)
			user.flags.ignore_permissions = True
			user.add_roles("Leave Approver")

		if self.expense_approver:
			user = frappe.get_doc("User", self.expense_approver)
			user.flags.ignore_permissions = True
			user.add_roles("Expense Approver")

	def validate_date(self):
		if self.date_of_birth and getdate(self.date_of_birth) > getdate(today()):
			throw(_("Date of Birth cannot be greater than today."))

		if self.date_of_birth and self.date_of_joining and getdate(self.date_of_birth) >= getdate(self.date_of_joining):
			throw(_("Date of Joining must be greater than Date of Birth"))

		elif self.date_of_retirement and self.date_of_joining and (getdate(self.date_of_retirement) <= getdate(self.date_of_joining)):
			throw(_("Date Of Retirement must be greater than Date of Joining"))

		elif self.relieving_date and self.date_of_joining and (getdate(self.relieving_date) < getdate(self.date_of_joining)):
			throw(_("Relieving Date must be greater than or equal to Date of Joining"))

		elif self.contract_end_date and self.date_of_joining and (getdate(self.contract_end_date) <= getdate(self.date_of_joining)):
			throw(_("Contract End Date must be greater than Date of Joining"))

	def validate_email(self):
		if self.company_email:
			validate_email_address(self.company_email, True)
		if self.personal_email:
			validate_email_address(self.personal_email, True)

	def set_preferred_email(self):
		preferred_email_field = frappe.scrub(self.prefered_contact_email)
		if preferred_email_field:
			preferred_email = self.get(preferred_email_field)
			self.prefered_email = preferred_email

	def validate_status(self):
		if self.status == 'Left':
			reports_to = frappe.db.get_all('Employee',
				filters={'reports_to': self.name, 'status': "Active"},
				fields=['name','employee_name']
			)
			if reports_to:
				link_to_employees = [frappe.utils.get_link_to_form('Employee', employee.name, label=employee.employee_name) for employee in reports_to]
				message = _("The following employees are currently still reporting to {0}:").format(frappe.bold(self.employee_name))
				message += "<br><br><ul><li>" + "</li><li>".join(link_to_employees)
				message += "</li></ul><br>"
				message += _("Please make sure the employees above report to another Active employee.")
				throw(message, EmployeeLeftValidationError, _("Cannot Relieve Employee"))
			if not self.relieving_date:
				throw(_("Please enter relieving date."))

	def validate_for_enabled_user_id(self, enabled):
		if not self.status == 'Active':
			return

		if enabled is None:
			frappe.throw(_("User {0} does not exist").format(self.user_id))
		if enabled == 0:
			frappe.throw(_("User {0} is disabled").format(self.user_id), EmployeeUserDisabledError)

	def validate_duplicate_user_id(self):
		employee = frappe.db.sql_list("""select name from `tabEmployee` where
			user_id=%s and status='Active' and name!=%s""", (self.user_id, self.name))
		if employee:
			throw(_("User {0} is already assigned to Employee {1}").format(
				self.user_id, employee[0]), frappe.DuplicateEntryError)

	def validate_reports_to(self):
		if self.reports_to == self.name:
			throw(_("Employee cannot report to himself."))

	def on_trash(self):
		self.update_nsm_model()
		delete_events(self.doctype, self.name)
		if frappe.db.exists("Employee Transfer", {'new_employee_id': self.name, 'docstatus': 1}):
			emp_transfer = frappe.get_doc("Employee Transfer", {'new_employee_id': self.name, 'docstatus': 1})
			emp_transfer.db_set("new_employee_id", '')

	def validate_preferred_email(self):
		if self.prefered_contact_email and not self.get(scrub(self.prefered_contact_email)):
			frappe.msgprint(_("Please enter {0}").format(self.prefered_contact_email))

	def validate_onboarding_process(self):
		employee_onboarding = frappe.get_all("Employee Onboarding",
			filters={"job_applicant": self.job_applicant, "docstatus": 1, "boarding_status": ("!=", "Completed")})
		if employee_onboarding:
			doc = frappe.get_doc("Employee Onboarding", employee_onboarding[0].name)
			doc.validate_employee_creation()
			doc.db_set("employee", self.name)

	def reset_employee_emails_cache(self):
		prev_doc = self.get_doc_before_save() or {}
		cell_number = cstr(self.get('cell_number'))
		prev_number = cstr(prev_doc.get('cell_number'))
		if (cell_number != prev_number or
			self.get('user_id') != prev_doc.get('user_id')):
			frappe.cache().hdel('employees_with_number', cell_number)
			frappe.cache().hdel('employees_with_number', prev_number)

def get_timeline_data(doctype, name):
	'''Return timeline for attendance'''
	return dict(frappe.db.sql('''select unix_timestamp(attendance_date), count(*)
		from `tabAttendance` where employee=%s
			and attendance_date > date_sub(curdate(), interval 1 year)
			and status in ('Present', 'Half Day')
			group by attendance_date''', name))

@frappe.whitelist()
def get_retirement_date(date_of_birth=None):
	ret = {}
	if date_of_birth:
		try:
			retirement_age = int(frappe.db.get_single_value("HR Settings", "retirement_age") or 60)
			dt = add_years(getdate(date_of_birth),retirement_age)
			ret = {'date_of_retirement': dt.strftime('%Y-%m-%d')}
		except ValueError:
			# invalid date
			ret = {}

	return ret

def validate_employee_role(doc, method):
	# called via User hook
	if "Employee" in [d.role for d in doc.get("roles")]:
		if not frappe.db.get_value("Employee", {"user_id": doc.name}):
			frappe.msgprint(_("Please set User ID field in an Employee record to set Employee Role"))
			doc.get("roles").remove(doc.get("roles", {"role": "Employee"})[0])

def update_user_permissions(doc, method):
	# called via User hook
	if "Employee" in [d.role for d in doc.get("roles")]:
		if not has_permission('User Permission', ptype='write', raise_exception=False): return
		employee = frappe.get_doc("Employee", {"user_id": doc.name})
		employee.update_user_permissions()

def send_holiday_reminders():
	"""
		Send holiday reminders to Employees if 'Send Holiday Reminders' is checked
	"""
	to_send = int(frappe.db.get_single_value("HR Settings", "send_holiday_reminders") or 1)
	if not to_send:
		return
	
	employees = frappe.db.get_all('Employee', pluck='name')

	for employee in employees:
		has_holiday, holiday_descriptions = is_holiday(employee, only_non_weekly=True, with_description=True, raise_exception=False)
		if has_holiday:
			send_holiday_reminder_to_employee(employee, holiday_descriptions)

def send_holiday_reminder_to_employee(employee, descriptions):
	reminder_text, message = get_holiday_reminder_text_and_message(descriptions)
	
	employee_doc = frappe.get_doc('Employee', employee)
	employee_email = get_employee_email(employee_doc)

	frappe.sendmail(
		recipients=[employee_email],
		subject=_("Holiday Reminder"),
		template="holiday_reminder",
		args=dict(
			reminder_text=reminder_text,
			message=message
		),
		header=_("Today is a holiday for you.")
	)

def get_holiday_reminder_text_and_message(descriptions):
	description = descriptions[0] if len(descriptions) == 1 else comma_and(descriptions, add_quotes=False)
	
	reminder_text = _("This email is to remind you about today's holiday.")
	message = _("Holiday is on the occassion of {0}.").format(description)

	return reminder_text, message

def send_work_anniversary_reminders():
	"""Send Employee Work Anniversary Reminders if 'Send Work Anniversary Reminders' is checked"""
	to_send = int(frappe.db.get_single_value("HR Settings", "send_work_anniversary_reminders") or 1) 
	if not to_send:
		return
	
	employees_joined_today = get_employees_having_an_event_today("work_anniversary")

	for company, anniversary_persons in employees_joined_today.items():
		employee_emails = get_all_employee_emails(company)
		anniversary_person_emails = [get_employee_email(doc) for doc in anniversary_persons]
		recipients = list(set(employee_emails) - set(anniversary_person_emails))

		reminder_text, message = get_work_anniversary_reminder_text_and_message(anniversary_persons)		
		send_work_anniversary_reminder(recipients, reminder_text, anniversary_persons, message)

		if len(anniversary_persons) > 1:
			# email for people sharing work anniversaries
			for person in anniversary_persons:
				person_email = person["user_id"] or person["personal_email"] or person["company_email"]	
				others = [d for d in anniversary_persons if d != person]
				reminder_text, message = get_work_anniversary_reminder_text_and_message(others)
				send_work_anniversary_reminder(person_email, reminder_text, others, message)

def get_work_anniversary_reminder_text_and_message(anniversary_persons):
	if len(anniversary_persons) == 1:
		anniversary_person = anniversary_persons[0]['name']
		# Number of years completed at the company
		completed_years = getdate().year - anniversary_persons[0]['date_of_joining'].year
		anniversary_person += f" completed {completed_years} years"
	else:
		person_names_with_years = []
		for person in anniversary_persons:
			person_text = person['name']
			# Number of years completed at the company
			completed_years = getdate().year - person['date_of_joining'].year
			person_text += f" completed {completed_years} years"
			person_names_with_years.append(person_text)

		# converts ["Jim", "Rim", "Dim"] to Jim, Rim & Dim
		anniversary_person = comma_sep(person_names_with_years, frappe._("{0} & {1}"), False)

	reminder_text = _("Today {0} at our Company! 🎉").format(anniversary_person)
	message = _("A friendly reminder of an important date for our team.")
	message += "<br>"
	message += _("Everyone, let’s congratulate {0} on their work anniversary!").format(anniversary_person)

	return reminder_text, message

def send_work_anniversary_reminder(recipients, reminder_text, anniversary_persons, message):
	frappe.sendmail(
		recipients=recipients,
		subject=_("Work Anniversary Reminder"),
		template="anniversary_reminder",
		args=dict(
			reminder_text=reminder_text,
			anniversary_persons=anniversary_persons,
			message=message,
		),
		header=_("🎊️🎊️ Work Anniversary Reminder 🎊️🎊️")
	)

def send_birthday_reminders():
	"""Send Employee birthday reminders if no 'Stop Birthday Reminders' is not set."""
	if int(frappe.db.get_single_value("HR Settings", "stop_birthday_reminders") or 0):
		return

	employees_born_today = get_employees_who_are_born_today()

	for company, birthday_persons in employees_born_today.items():
		employee_emails = get_all_employee_emails(company)
		birthday_person_emails = [get_employee_email(doc) for doc in birthday_persons]
		recipients = list(set(employee_emails) - set(birthday_person_emails))

		reminder_text, message = get_birthday_reminder_text_and_message(birthday_persons)
		send_birthday_reminder(recipients, reminder_text, birthday_persons, message)

		if len(birthday_persons) > 1:
			# special email for people sharing birthdays
			for person in birthday_persons:
				person_email = person["user_id"] or person["personal_email"] or person["company_email"]
				others = [d for d in birthday_persons if d != person]
				reminder_text, message = get_birthday_reminder_text_and_message(others)
				send_birthday_reminder(person_email, reminder_text, others, message)

def get_employee_email(employee_doc):
	return employee_doc.get("user_id") or employee_doc.get("personal_email") or employee_doc.get("company_email")

def get_birthday_reminder_text_and_message(birthday_persons):
	if len(birthday_persons) == 1:
		birthday_person_text = birthday_persons[0]['name']
	else:
		# converts ["Jim", "Rim", "Dim"] to Jim, Rim & Dim
		person_names = [d['name'] for d in birthday_persons]
		birthday_person_text = comma_sep(person_names, frappe._("{0} & {1}"), False)

	reminder_text = _("Today is {0}'s birthday 🎉").format(birthday_person_text)
	message = _("A friendly reminder of an important date for our team.")
	message += "<br>"
	message += _("Everyone, let’s congratulate {0} on their birthday.").format(birthday_person_text)

	return reminder_text, message

def send_birthday_reminder(recipients, reminder_text, birthday_persons, message):
	frappe.sendmail(
		recipients=recipients,
		subject=_("Birthday Reminder"),
		template="birthday_reminder",
		args=dict(
			reminder_text=reminder_text,
			birthday_persons=birthday_persons,
			message=message,
		),
		header=_("Birthday Reminder 🎂")
	)

def get_employees_who_are_born_today():
	"""Get all employee born today & group them based on their company"""
	return get_employees_having_an_event_today("birthday") 

def get_employees_having_an_event_today(event_type):
	"""Get all employee who have `event_type` today 
	& group them based on their company. `event_type`
	can be `birthday` or `work_anniversary`"""

	from collections import defaultdict

	# Set column based on event type
	if event_type == 'birthday':
		condition_column = 'date_of_birth'
	elif event_type == 'work_anniversary':
		condition_column = 'date_of_joining'
	else:
		return

	employees_born_today = frappe.db.multisql({
		"mariadb": f"""
			SELECT `personal_email`, `company`, `company_email`, `user_id`, `employee_name` AS 'name', `image`, `date_of_joining`
			FROM `tabEmployee`
			WHERE
				DAY({condition_column}) = DAY(%(today)s)
			AND
				MONTH({condition_column}) = MONTH(%(today)s)
			AND
				`status` = 'Active'
		""",
		"postgres": f"""
			SELECT "personal_email", "company", "company_email", "user_id", "employee_name" AS 'name', "image"
			FROM "tabEmployee"
			WHERE
				DATE_PART('day', {condition_column}) = date_part('day', %(today)s)
			AND
				DATE_PART('month', {condition_column}) = date_part('month', %(today)s)
			AND
				"status" = 'Active'
		""",
	}, dict(today=today(), condition_column=condition_column), as_dict=1)

	grouped_employees = defaultdict(lambda: [])

	for employee_doc in employees_born_today:
		grouped_employees[employee_doc.get('company')].append(employee_doc)

	return grouped_employees

def get_holiday_list_for_employee(employee, raise_exception=True):
	if employee:
		holiday_list, company = frappe.db.get_value("Employee", employee, ["holiday_list", "company"])
	else:
		holiday_list=''
		company=frappe.db.get_value("Global Defaults", None, "default_company")

	if not holiday_list:
		holiday_list = frappe.get_cached_value('Company',  company,  "default_holiday_list")

	if not holiday_list and raise_exception:
		frappe.throw(_('Please set a default Holiday List for Employee {0} or Company {1}').format(employee, company))

	return holiday_list

def is_holiday(employee, date=None, raise_exception=True, only_non_weekly=False, with_description=False):
	'''
	Returns True if given Employee has an holiday on the given date
		:param employee: Employee `name`
		:param date: Date to check. Will check for today if None
		:param raise_exception: Raise an exception if no holiday list found, default is True
		:param only_non_weekly: Check only non-weekly holidays, default is False
	'''

	holiday_list = get_holiday_list_for_employee(employee, raise_exception)
	if not date:
		date = today()
	
	if not holiday_list:
		return False
	
	filters = {
		'parent': holiday_list,
		'holiday_date': date
	}
	if only_non_weekly:
		filters['weekly_off'] = False

	holidays = frappe.get_all(
		'Holiday', 
		fields=['description'],
		filters=filters, 
		pluck='description'
	)

	if with_description:
		return len(holidays) > 0, holidays

	return len(holidays) > 0

@frappe.whitelist()
def deactivate_sales_person(status = None, employee = None):
	if status == "Left":
		sales_person = frappe.db.get_value("Sales Person", {"Employee": employee})
		if sales_person:
			frappe.db.set_value("Sales Person", sales_person, "enabled", 0)

@frappe.whitelist()
def create_user(employee, user = None, email=None):
	emp = frappe.get_doc("Employee", employee)

	employee_name = emp.employee_name.split(" ")
	middle_name = last_name = ""

	if len(employee_name) >= 3:
		last_name = " ".join(employee_name[2:])
		middle_name = employee_name[1]
	elif len(employee_name) == 2:
		last_name = employee_name[1]

	first_name = employee_name[0]

	if email:
		emp.prefered_email = email

	user = frappe.new_doc("User")
	user.update({
		"name": emp.employee_name,
		"email": emp.prefered_email,
		"enabled": 1,
		"first_name": first_name,
		"middle_name": middle_name,
		"last_name": last_name,
		"gender": emp.gender,
		"birth_date": emp.date_of_birth,
		"phone": emp.cell_number,
		"bio": emp.bio
	})
	user.insert()
	return user.name

def get_all_employee_emails(company):
	'''Returns list of employee emails either based on user_id or company_email'''
	employee_list = frappe.get_all('Employee',
		fields=['name','employee_name'],
		filters={
			'status': 'Active',
			'company': company
		}
	)
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value('Employee',
			employee, ['user_id', 'company_email', 'personal_email'])
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails

def get_employee_emails(employee_list):
	'''Returns list of employee emails either based on user_id or company_email'''
	employee_emails = []
	for employee in employee_list:
		if not employee:
			continue
		user, company_email, personal_email = frappe.db.get_value('Employee', employee,
											['user_id', 'company_email', 'personal_email'])
		email = user or company_email or personal_email
		if email:
			employee_emails.append(email)
	return employee_emails

@frappe.whitelist()
def get_children(doctype, parent=None, company=None, is_root=False, is_tree=False):

	filters = [['status', '!=', 'Left']]
	if company and company != 'All Companies':
		filters.append(['company', '=', company])

	fields = ['name as value', 'employee_name as title']

	if is_root:
		parent = ''
	if parent and company and parent!=company:
		filters.append(['reports_to', '=', parent])
	else:
		filters.append(['reports_to', '=', ''])

	employees = frappe.get_list(doctype, fields=fields,
		filters=filters, order_by='name')

	for employee in employees:
		is_expandable = frappe.get_all(doctype, filters=[
			['reports_to', '=', employee.get('value')]
		])
		employee.expandable = 1 if is_expandable else 0

	return employees


def on_doctype_update():
	frappe.db.add_index("Employee", ["lft", "rgt"])

def has_user_permission_for_employee(user_name, employee_name):
	return frappe.db.exists({
		'doctype': 'User Permission',
		'user': user_name,
		'allow': 'Employee',
		'for_value': employee_name
	})

def has_upload_permission(doc, ptype='read', user=None):
	if not user:
		user = frappe.session.user
	if get_doc_permissions(doc, user=user, ptype=ptype).get(ptype):
		return True
	return doc.user_id == user