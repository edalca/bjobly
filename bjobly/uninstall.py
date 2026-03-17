import click

from bjobly.setup import before_uninstall as remove_custom_fields


def before_uninstall():
	try:
		print("Removing BJOBLY Settings from Frappe HR...")
		remove_custom_fields()

	except Exception as e:
		click.secho(
			"Removing Customizations for BJOBLY app failed due to an error."
			" Please try again or",
			fg="bright_red",
		)
		raise e