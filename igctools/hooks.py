app_name = "igctools"
app_title = "IGCTools"
app_publisher = "Ezequiel Sierra"
app_description = "Tools for IGCARIBE"
app_email = "esierra@gmail.com"
app_license = "mit"

doc_events = {
    "PrintCard": {
        "before_save": "igctools.api.printcard_svg.before_save_printcard_set_svg",
    },
    "Project": {
        "before_save": "igctools.api.printcard_svg.auto_svg_from_printcard",
    },
}

app_include_js = [
    "/assets/igctools/js/igc_broadcast_global.js",
]

override_doctype_class = {
    "Job Card": "igctools.overrides.job_card.JobCard",
}

# ------------------
# Apps
# ------------------

# required_apps = []

# add_to_apps_screen = [
# 	{
# 		"name": "igctools",
# 		"logo": "/assets/igctools/logo.png",
# 		"title": "IGCTools",
# 		"route": "/igctools",
# 		"has_permission": "igctools.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# app_include_css = "/assets/igctools/css/igctools.css"
# app_include_js = "/assets/igctools/js/igctools.js"
# web_include_css = "/assets/igctools/css/igctools.css"
# web_include_js = "/assets/igctools/js/igctools.js"
# website_theme_scss = "igctools/public/scss/website"
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}
# page_js = {"page" : "public/js/file.js"}
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}
# app_include_icons = "igctools/public/icons.svg"

# Home Pages
# ----------

# home_page = "login"
# role_home_page = {
# 	"Role": "home_page"
# }

# website_generators = ["Web Page"]

# jinja = {
# 	"methods": "igctools.utils.jinja_methods",
# 	"filters": "igctools.utils.jinja_filters"
# }

# before_install = "igctools.install.before_install"
# after_install = "igctools.install.after_install"
# before_uninstall = "igctools.uninstall.before_uninstall"
# after_uninstall = "igctools.uninstall.after_uninstall"
# before_app_install = "igctools.utils.before_app_install"
# after_app_install = "igctools.utils.after_app_install"
# before_app_uninstall = "igctools.utils.before_app_uninstall"
# after_app_uninstall = "igctools.utils.after_app_uninstall"
# notification_config = "igctools.notifications.get_notification_config"

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"igctools.tasks.all"
# 	],
# 	"daily": [
# 		"igctools.tasks.daily"
# 	],
# 	"hourly": [
# 		"igctools.tasks.hourly"
# 	],
# 	"weekly": [
# 		"igctools.tasks.weekly"
# 	],
# 	"monthly": [
# 		"igctools.tasks.monthly"
# 	],
# }

# before_tests = "igctools.install.before_tests"

# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "igctools.event.get_events"
# }
# override_doctype_dashboards = {
# 	"Task": "igctools.task.get_dashboard_data"
# }

# auto_cancel_exempted_doctypes = ["Auto Repeat"]
# ignore_links_on_delete = ["Communication", "ToDo"]
# before_request = ["igctools.utils.before_request"]
# after_request = ["igctools.utils.after_request"]
# before_job = ["igctools.utils.before_job"]
# after_job = ["igctools.utils.after_job"]

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# ]

# auth_hooks = [
# 	"igctools.auth.validate"
# ]

# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30
# }
